import os
import re
import httpx
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from .state import BankChatState
from ..search_tool import perform_web_search
from typing import Optional
from asgiref.sync import sync_to_async

# MCP Imports
from langchain_mcp_adapters.tools import load_mcp_tools

# ── SETUP ───────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL   = os.getenv("FRAUD_SERVICE_URL",   "http://fraud-service:8001")
MAIL_SERVICE_URL    = os.getenv("MAIL_SERVICE_URL",    "http://mail-service:8002")
TEXT2SQL_SERVICE_URL = os.getenv("TEXT2SQL_SERVICE_URL", "http://text-to-sql-service:8003")
ALERT_EMAIL         = os.getenv("ALERT_EMAIL",          "compliance@yourbank.com")

# ── HELPERS ─────────────────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)

def extract_iban(messages: list) -> str:
    for msg in reversed(messages):
        content = msg.content if hasattr(msg, "content") else str(msg)
        match = IBAN_PATTERN.search(content)
        if match:
            return (match.group(1) or match.group(2) or "").replace(" ", "").upper()
    return ""

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.getenv("OLLAMA_MODEL", "llama3.2")
        return ChatOllama(base_url=base_url, model=model, temperature=0.7)
    api_key = os.getenv("GROQ_API_KEY")
    model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    return ChatGroq(model=model, api_key=api_key, temperature=0.7)

llm = get_llm()

# ── PROMPTS ─────────────────────────────────────────────────────────────────

BASE_POLICY = (
    "\n\nMatch answer length to complexity. Start with a direct answer. "
    "Use bullets for steps. Keep under 350 words. Prioritize readability."
)

SYSTEM_PROMPTS = {
    "account_agent": "You are BankChat, an account specialist." + BASE_POLICY,
    "transfer_agent": "You are BankChat, a transfer specialist." + BASE_POLICY,
    "support_agent": "You are BankChat, a support specialist." + BASE_POLICY,
    "fallback": "You are BankChat, a professional AI banking assistant." + BASE_POLICY,
    "fraud_agent": (
        "You are BankChat's Senior Fraud Officer. Provide professional, secure advice. "
        "Maintain confidentiality."
    ) + BASE_POLICY,
    "search_agent": (
        "You are BankChat's Research Assistant. Your goal is to provide a comprehensive answer based on the provided SEARCH RESULTS. "
        "Do not just list websites; extract the actual information (like weather, rates, news) and summarize it. "
        "Cite your sources using URLs at the end of each relevant section."
    ) + BASE_POLICY,
    "reasoning_prompt": (
        "Explain in ONE short sentence what you are about to do based on the intent. "
        "Start with 'I am going to...' or 'the user wants me to...' or 'I will...'. Be professional."
    )
}

MAIL_AGENT_SYSTEM = """\
You are a banking compliance mail agent. respond with ONLY a valid JSON object.
{
  "subject": "French subject line",
  "template": "fraud_alert" | "critical_alert",
  "context": { ... }
}
"""

# ── SHARED LOGIC ────────────────────────────────────────────────────────────

async def _get_reasoning(intent: str, last_msg: str) -> str:
    """Unified Thinking output for all agents."""
    if intent == "fallback": return ""
    try:
        resp = (await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPTS["reasoning_prompt"]),
            HumanMessage(content=f"Intent: {intent}\nMessage: {last_msg}")
        ])).content.strip()
        return f"💡 *{resp}*\n\n---\n\n"
    except Exception:
        return "💡 *Traitement de votre demande...*\n\n---\n\n"

# ── INTENT DETECTION ────────────────────────────────────────────────────────

# ── Intent keyword lists ─────────────────────────────────────────────────────

# Keywords that strongly indicate a database / SQL query intent
_SQL_KEYWORDS = [
    # Quantitative / aggregation
    "combien", "total", "nombre", "count", "somme", "moyenne", "montant",
    "statistique", "rapport", "reporting", "top ", "les 5", "les 10",
    # DB-query verbs
    "liste", "affiche", "montre", "donne moi", "trouve", "cherche",
    "toutes les transactions", "tous les", "toutes les",
    # Status values that live in the DB (not fraud-reporting actions)
    "bloquées", "bloqués", "blocked", "failed", "pending", "approved",
    "rejetées", "rejetés",
    # Explicit DB / SQL references
    "base de données", "base", "select", "requête sql",
    # Domain columns / tables users ask about
    "score de fraude", "risk_level", "decision", "décision",
    "règles", "règle", "tracfin", "signalement",
    "transaction", "transactions", "virement", "paiement",
]

# Keywords that indicate the user is *reporting* or *asking about* fraud (not querying the DB)
_FRAUD_KEYWORDS = [
    "fraude", "frauduleux", "frauduleuse", "phishing", "arnaque",
    "escroquerie", "hameçonnage", "louche", "suspect", "suspicieux",
    "pirater", "piraté", "vol", "volé",
]

# Keywords that force a live web search
_SEARCH_KEYWORDS = [
    "cours", "taux de change", "change", "bourse", "prix de",
    "météo", "actualité", "news", "qui est", "quand a",
    "weather", "température", "climat",
]


async def detect_intent(state: BankChatState) -> BankChatState:
    selected = state.get("selected_agent")
    if selected and selected not in ("orchestrator", "auto"):
        return {**state, "intent": "text_to_sql" if selected == "sql" else selected}

    last_msg = state["messages"][-1].content
    lower_msg = last_msg.lower()

    # ── Priority 1 : SQL keyword override (before LLM call) ──────────────────
    # If a user explicitly asks for "cherche mes transactions", it should go to SQL if Auto.
    # However, we skip this if they explicitly selected "search".
    if any(k in lower_msg for k in _SQL_KEYWORDS):
        logger.info(f"[detect_intent] SQL keyword override → text_to_sql")
        return {**state, "intent": "text_to_sql"}

    # ── Priority 2 : LLM classification ──────────────────────────────────────
    prompt = (
        "Classify the banking user's intent into ONE of these categories:\n"
        "- account      : Balance, IBAN request, account status, RIB.\n"
        "- transfer     : Sending money, wire transfers, recurring payments.\n"
        "- support      : Lost card, mobile app issues, password reset, generic help.\n"
        "- fraud: Reporting fraudulent emails, reporting scams, auditing an IBAN ,phishing, scam, stolen card.\n"
        "- text_to_sql  : Questions that require querying the banking database — counts, "
        "statistics, lists of transactions, blocked/failed payments, fraud scores, rules, "
        "reports. The answer comes from a SQL query, not from a conversation.\n"
        "- search       : General info not in bank DB, market trends, exchange rates, news.\n"
        "- fallback     : Greetings, off-topic, anything else.\n\n"
        "EXAMPLES:\n"
        "'Quel est mon solde ?' -> account\n"
        "'Je veux envoyer 100€ à Ali' -> transfer\n"
        "'Ma carte est bloquée' -> support\n"
        "'Cet IBAN est-il suspect ?' -> fraud\n"
        "'Signaler un phishing' -> fraud\n"
        "'Analyser l\'IBAN XXXX est-il suspect?' -> fraud\n"
        "'Analyser les transactions frauduleuses' -> fraud\n"
        "'Combien de transactions au total ?' -> text_to_sql\n"
        "'Trouve les transactions bloquées' -> text_to_sql\n"
        "'Quel est le score de fraude moyen ?' -> text_to_sql\n"
        "'Quelles règles AML sont actives ?' -> text_to_sql\n"
        "'Bonjour' -> fallback\n\n"
        f"Message: {last_msg}\n"
        "Classification (one word only):"
    )
    try:
        resp = await llm.ainvoke(prompt)
        intent = resp.content.strip().lower().split()[0]
        logger.info(f"[detect_intent] LLM classified intent as: {intent}")
    except Exception as e:
        logger.error(f"[detect_intent] LLM intent detection failed: {e}")
        intent = "fallback"

    # ── Priority 3 : Fraud keyword override (explicit fraud-reporting terms only) ──
    if intent not in ("fraud", "text_to_sql") and any(k in lower_msg for k in _FRAUD_KEYWORDS):
        intent = "fraud"
        logger.info(f"[detect_intent] Fraud keyword override → fraud")

    # ── Priority 4 : Search override ──────────────────────────────────────────
    if intent not in ("search", "text_to_sql", "fraud") and any(k in lower_msg for k in _SEARCH_KEYWORDS):
        intent = "search"
        logger.info(f"[detect_intent] Search keyword override → search")

    valid_intents = ("account", "transfer", "support", "fraud", "search", "text_to_sql")
    resolved = intent if intent in valid_intents else "fallback"
    logger.info(f"[detect_intent] '{last_msg[:60]}' → {resolved}")
    return {**state, "intent": resolved}

async def _get_fraud_decision_and_result(messages: list, user_id: str, session_id: str, auth_token: str | None = None) -> tuple:
    """Unifies ANALYZE vs TALK logic and calling the fraud-service."""
    last_msg = next((m.content for m in reversed(messages) if m.__class__.__name__ == "HumanMessage"), "")

    prompt = (
        "You are a Fraud Detection Orchestrator. "
        "Decide if the user is asking for a deep technical analysis of transactions (ANALYZE) "
        "or if they are asking a general question about fraud, security, or procedures (TALK).\n\n"
        "EXAMPLES:\n"
        "- 'Check this IBAN FR76...' -> ANALYZE\n"
        "- 'Analyze the transactions for this account' -> ANALYZE\n"
        "- 'What is phishing?' -> TALK\n"
        "- 'How do I report a lost card?' -> TALK\n\n"
        f"User Message: {last_msg}\n\n"
        "Format your response exactly as follows:\n"
        "REASONING: <brief explanation>\n"
        "DECISION: <ANALYZE or TALK>"
    )

    resp = (await llm.ainvoke(prompt)).content.upper()
    reasoning = next((l.split(":", 1)[1].strip() for l in resp.split("\n") if "REASONING:" in l), "Delegated to fraud specialist.")
    decision = "ANALYZE" if "ANALYZE" in resp else "TALK"
    prefix = f"💡 *{reasoning}*\n\n---\n\n"

    if decision == "ANALYZE":
        iban = extract_iban(messages)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{FRAUD_SERVICE_URL}/analyze",
                    json={
                        "message": last_msg,
                        "iban": iban,
                        "action": "fraud_check",
                        "user_id": user_id,
                        "session_id": session_id,
                    },
                    headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {},
                    timeout=120.0
                )
                response.raise_for_status()
                return prefix, response.json(), True
        except Exception as e:
            logger.error(f"Fraud service call failed: {e}")
            return prefix, {"llm_summary": f"❌ Erreur lors de l'appel au service de fraude: {str(e)}"}, True
    
    return prefix, {}, False


# ── Nodes ───────────────────────────────────────────────────────────────────

async def fraud_agent(state: BankChatState) -> BankChatState:
    try:
        prefix, result, is_analyze = await _get_fraud_decision_and_result(
            state["messages"], state["user_id"], state["session_id"], state.get("auth_token")
        )
        if is_analyze:
            return {**state, "messages": [AIMessage(content=prefix + result.get("llm_summary", ""))], "agent": "fraud_agent", "context": result}
        
        resp = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])] + list(state["messages"]))
        return {**state, "messages": [AIMessage(content=prefix + resp.content)], "agent": "fraud_agent", "context": {}}
    except Exception as e:
        logger.exception("Fraud agent error")
        return {**state, "messages": [AIMessage(content=f"❌ Error: {e}")], "agent": "fraud_agent", "context": {}}

# ── Routing & Agents ──────────────────────────────────────────────────────────

def route_to_agent(state: BankChatState) -> str:
    """Required by orchestrator.py for conditional edge routing."""
    return {
        "account":     "account_agent",
        "transfer":    "transfer_agent",
        "support":     "support_agent",
        "fraud":       "fraud_agent",
        "search":      "search_agent",
        "text_to_sql": "text_to_sql_agent",
    }.get(state["intent"], "fallback")

async def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    resp = await llm.ainvoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": agent_key}

async def account_agent(state: BankChatState):   return await _run_agent(state, "account_agent")
async def transfer_agent(state: BankChatState):  return await _run_agent(state, "transfer_agent")
async def support_agent(state: BankChatState):   return await _run_agent(state, "support_agent")
async def handle_fallback(state: BankChatState): return await _run_agent(state, "fallback")

async def search_agent(state: BankChatState) -> BankChatState:
    last_msg = state["messages"][-1].content
    
    # Step 1: Query Optimization (Keyword extraction)
    optimize_prompt = (
        "Extract the most relevant search keywords from the following user message to perform a precise web search. "
        "Focus on entities, dates, and core intent. Return ONLY the keywords, no explanation.\n\n"
        f"Message: {last_msg}"
    )
    try:
        resp = await llm.ainvoke(optimize_prompt)
        optimized_query = resp.content.strip()
    except Exception:
        optimized_query = last_msg
    logger.info(f"[search_agent] Optimized query: {optimized_query}")

    # Step 2: MCP Search Call
    try:
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        server_script = os.path.join(current_dir, "..", "mcp_server.py")
        
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[server_script],
            env=os.environ.copy()
        )
        
        # Load MCP tools (via stdio bridge) - PROPERLY AWAITING
        logger.info(f"[search_agent] Connecting to MCP server: {server_script}")
        mcp_tools = await load_mcp_tools(
            None,
            connection={
                "transport": "stdio",
                "command": sys.executable,
                "args": [server_script],
                "env": os.environ.copy()
            }
        )
        
        # Find the web_search tool
        search_tool = next((t for t in mcp_tools if t.name == "web_search"), None)
        
        if search_tool:
            logger.info(f"[search_agent] Calling MCP tool 'web_search' with query: {optimized_query}")
            results = await search_tool.ainvoke({"query": optimized_query})
            logger.info("[search_agent] MCP tool results received successfully.")
        else:
            logger.warning("[search_agent] web_search tool not found in MCP server, falling back to legacy tool.")
            results = perform_web_search(optimized_query)
            
    except Exception as e:
        logger.error(f"[search_agent] MCP call failed: {e}. Falling back to legacy tool.")
        results = perform_web_search(optimized_query)

    # Step 3: Summarization
    system = SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nSEARCH RESULTS:\n{results}")
    resp = await llm.ainvoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": "search_agent"}

async def text_to_sql_agent(state: BankChatState) -> dict:
    """
    Text-to-SQL agent node. (async)

    Delegates to the text-to-sql-service microservice which:
      1. Converts the NL question to SQL (LLM)
      2. Validates SQL security (SELECT-only, whitelisted tables)
      3. Executes against PostgreSQL banking_data
      4. Returns an explanation + markdown table

    Requires the JWT auth_token from state (bank_agent / admin only).
    """
    # Extract the last human message
    last_user_msg = ""
    for msg in reversed(state["messages"]):
        if msg.__class__.__name__ == "HumanMessage":
            last_user_msg = msg.content
            break

    if not last_user_msg.strip():
        return {
            **state,
            "messages": [AIMessage(content="❌ Message vide — veuillez poser une question.")],
            "agent": "text_to_sql_agent",
            "context": {},
        }

    try:
        headers = {}
        if state.get("auth_token"):
            headers["Authorization"] = f"Bearer {state['auth_token']}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEXT2SQL_SERVICE_URL}/query",
                json={
                    "question": last_user_msg,
                    "user_id":  state.get("user_id", "anonymous"),
                },
                headers=headers,
                timeout=90.0,
            )
            resp.raise_for_status()
            result = resp.json()

        status = result.get("status", "error")
        explanation = result.get("explanation", "")
        sql = result.get("sql", "")

        if status == "success":
            row_count = result.get("row_count", 0)
            duration  = result.get("duration_ms", 0)
            truncated = result.get("truncated", False)

            # Build the header line
            header = (
                f"🧠 **Analyse SQL** — {row_count} résultat(s) "
                f"en {duration:.0f}ms"
            )
            if truncated:
                header += f" _(limité à {row_count} lignes)_"

            # Show the generated SQL in a collapsible code block
            sql_block = f"\n\n<details>\n<summary>🔍 Requête SQL générée</summary>\n\n```sql\n{sql}\n```\n\n</details>\n\n"

            ai_content = f"{header}{sql_block}{explanation}"

            logger.info(
                f"[text_to_sql_agent] ✅ {row_count} rows "
                f"(truncated={truncated}) in {duration:.0f}ms"
            )
        else:
            # Service returned an error but with 200 status
            error_msg = result.get("error", "Erreur inconnue.")
            ai_content = (
                f"⛔ **Erreur Text-to-SQL**\n\n"
                f"{explanation or error_msg}"
            )
            logger.warning(f"[text_to_sql_agent] Service error: {error_msg}")

        return {
            **state,
            "messages": [AIMessage(content=ai_content)],
            "agent":    "text_to_sql_agent",
            "context":  result,
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            msg = (
                "🔒 **Accès refusé** — La fonctionnalité Text-to-SQL est "
                "réservée aux agents et administrateurs de la banque."
            )
        elif e.response.status_code == 401:
            msg = "🔒 **Non authentifié** — Veuillez vous reconnecter."
        else:
            msg = f"❌ Erreur service SQL ({e.response.status_code}) : {e.response.text[:200]}"
        logger.error(f"[text_to_sql_agent] HTTP error: {e}")
        return {
            **state,
            "messages": [AIMessage(content=msg)],
            "agent": "text_to_sql_agent",
            "context": {},
        }
    except Exception as e:
        logger.exception("[text_to_sql_agent] Unexpected error")
        return {
            **state,
            "messages": [AIMessage(content=f"❌ Erreur interne Text-to-SQL : {str(e)}")],
            "agent": "text_to_sql_agent",
            "context": {},
        }


# ── Mail helpers ──────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """
    Parse the first JSON object found in `text`.
    Returns a dict or None if no valid JSON object is found.
    """
    import re as _re
    match = _re.search(r'\{.*\}', text, _re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


async def call_mail_service(payload: dict) -> dict:
    """
    POST payload to mail-service /send (Async).
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{MAIL_SERVICE_URL}/send", json=payload, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(f"[call_mail_service] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:
        logger.error(f"[call_mail_service] Failed: {exc}")
        return {"status": "error", "detail": str(exc)}


# ── Mail agent (agentique hybride) ────────────────────────────────────────────

async def mail_agent(state: BankChatState) -> BankChatState:
    """
    Nœud agentique mail hybride :
      1. Décision DÉTERMINISTE sur seuils (score/TRACFIN) — fiable, sans LLM
      2. Composition du sujet/contexte via LLM — avec fallback robuste si parse échoue
      3. Appel HTTP au mail-service

    Appelé uniquement après fraud_agent path ANALYZE (context non vide via orchestrator).
    """
    context     = state.get("context", {})
    score_final = context.get("score_final", 0)
    tracfin     = context.get("tracfin_required", False)
    iban        = context.get("iban", "")

    # ── Étape 1 : Décision déterministe (PAS de LLM) ─────────────────────────
    logger.info(
        f"[mail_agent] Evaluating — IBAN={iban} score={score_final} "
        f"tracfin={tracfin} risk={context.get('risk_level')}"
    )

    should_send = score_final >= 50 or tracfin
    if not should_send:
        logger.info(f"[mail_agent] score={score_final} < 50 and tracfin=False — no mail.")
        return {**state, "agent": "mail_agent"}

    # Choix du template selon seuil
    template = "critical_alert" if (score_final >= 80 or tracfin) else "fraud_alert"

    # ── Fallback sujet + contexte (utilisé si LLM échoue) ────────────────────
    subject = (
        f"[BankChat] 🔴 Alerte critique score {score_final}/100 — "
        f"{context.get('risk_level', '')} — IBAN {iban}"
        if template == "critical_alert" else
        f"[BankChat] ⚠️ Alerte fraude score {score_final}/100 — IBAN {iban}"
    )
    mail_context = {
        "iban":             iban,
        "score_final":      score_final,
        "risk_level":       context.get("risk_level", ""),
        "tracfin_required": tracfin,
        "llm_summary":      context.get("llm_summary", ""),
        "download_url":     context.get("download_url", ""),
        "triggered_count":  len(context.get("fraud_results", [])),
    }

    # ── Étape 2 : LLM pour composer sujet + contexte (avec fallback) ─────────
    context_for_llm = json.dumps({
        **mail_context,
        "alert_email":  ALERT_EMAIL,
        "session_id":   state.get("session_id", ""),
        "user_id":      state.get("user_id", "anonymous"),
    }, ensure_ascii=False)

    try:
        llm_resp = await llm.ainvoke([
            SystemMessage(content=MAIL_AGENT_SYSTEM),
            HumanMessage(content=f"Fraud analysis result:\n{context_for_llm}"),
        ])
        parsed = _extract_json(llm_resp.content)
        if parsed:
            subject      = parsed.get("subject", subject)
            template     = parsed.get("template", template)
            mail_context = parsed.get("context", mail_context)
            logger.info(f"[mail_agent] LLM composed — template={template} subject={subject}")
    except Exception as e:
        logger.warning(f"[mail_agent] LLM composition failed, using fallback: {e}")

    # ── Étape 3 : Envoi via mail-service ─────────────────────────────────────
    payload = {
        "to":              ALERT_EMAIL,
        "subject":         subject,
        "template":        template,
        "context":         mail_context,
        "attachment_path": context.get("report_path") or None,
        # ── Métadonnées pour la traçabilité BD ───────────────────────────────
        "iban":            iban,
        "score_final":     score_final,
        "risk_level":      context.get("risk_level", ""),
        "tracfin":         tracfin,
        "session_id":      state.get("session_id", ""),
        "user_id":         state.get("user_id", "anonymous"),
    }

    result = await call_mail_service(payload)
    status = result.get("status", "error")

    if status == "sent":
        logger.info(f"[mail_agent] ✅ Email sent → {ALERT_EMAIL}")
    else:
        logger.error(f"[mail_agent] ❌ Email FAILED: {result.get('detail')}")

    # ── Mettre à jour le decision log avec les infos mail ─────────────────
    decision_log_id = context.get("decision_log_id", "") or state.get("decision_log_id", "")
    logger.info(f"[mail_agent] decision_log_id={decision_log_id!r}")

    if decision_log_id:
        try:
            _auth_headers = {"Authorization": f"Bearer {state.get('auth_token')}"} if state.get("auth_token") else {}
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"{FRAUD_SERVICE_URL}/decision-logs/{decision_log_id}/mail",
                    json={
                        "mail_sent":      status == "sent",
                        "mail_recipient": ALERT_EMAIL,
                        "mail_template":  template,
                        "mail_status":    status,
                        "mail_id":        result.get("id"),
                    },
                    headers=_auth_headers,
                    timeout=5.0,
                )
            patch_resp.raise_for_status()
            logger.info(f"[mail_agent] ✅ Decision log {decision_log_id} updated with mail info")
        except Exception as e:
            logger.warning(f"[mail_agent] Could not update decision log mail info: {e}")
    else:
        logger.warning("[mail_agent] No decision_log_id — mail info not persisted to DB")
    updated_context = {
        **context,
        "mail_results": [{"to": ALERT_EMAIL, "subject": subject, "status": status}]
    }
    return {
        **state,
        "agent":   "mail_agent",
        "context": updated_context,
    }


# ── Streaming helper ──────────────────────────────────────────────────────────



async def stream_agent_response(
    intent: str,
    messages: list,
    user_id: str = "anonymous",
    session_id: str = "",
    auth_token: Optional[str] = None
):
    """
    Yields (token, agent_key) tuples (Async Generator).
    """

    # ── FRAUD FLOW ─────────────────────────────────────────
    if intent == "fraud":
        last_msg = ""
        for msg in reversed(messages):
            if msg.__class__.__name__ == "HumanMessage":
                last_msg = msg.content
                break

        decision_prompt = (
            "You are a fraud detection reasoning engine. "
            "Based on the user's message, decide if we need ANALYZE or TALK.\n\n"
            f"Message: {last_msg}\n\n"
            "Format:\nREASONING: <text>\nDECISION: <ANALYZE or TALK>"
        )

        try:
            decision_resp = (await llm.ainvoke(decision_prompt)).content
            lines = decision_resp.strip().split("\n")

            reasoning = "Analyse de la requête..."
            decision = "TALK"

            for line in lines:
                if line.upper().startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()
                if line.upper().startswith("DECISION:"):
                    decision = "ANALYZE" if "ANALYZE" in line.upper() else "TALK"

            yield f"💡 *{reasoning}*\n\n---\n\n", "fraud_agent"

            if decision == "ANALYZE":
                iban = extract_iban(messages)

                _headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{FRAUD_SERVICE_URL}/analyze",
                        json={
                            "message": last_msg,
                            "iban": iban,
                            "action": "fraud_check",
                            "user_id": user_id,
                            "session_id": session_id,
                            "excel_path": "",
                        },
                        headers=_headers,
                        timeout=120.0,
                    )
                resp.raise_for_status()

                result = resp.json()
                summary = result.get("llm_summary", "Analyse terminée.")

                yield summary, "fraud_agent", result
            else:
                system = SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])
                async for chunk in llm.astream([system] + list(messages)):
                    if chunk.content:
                        yield chunk.content, "fraud_agent"
        except Exception as e:
            yield f"❌ Erreur : {str(e)}", "fraud_agent"
        return

    # ── OTHER AGENTS ───────────────────────────────────────
    agent_key_map = {
        "account": "account_agent",
        "transfer": "transfer_agent",
        "support": "support_agent",
        "search": "search_agent",
        "text_to_sql": "text_to_sql_agent",
        "sql": "text_to_sql_agent",
    }
    agent_key = agent_key_map.get(intent, "fallback")

    # ── TEXT TO SQL ────────────────────────────────────────
    if agent_key == "text_to_sql_agent":
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.__class__.__name__ == "HumanMessage":
                last_user_msg = msg.content
                break

        try:
            headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{TEXT2SQL_SERVICE_URL}/query",
                    json={"question": last_msg, "user_id": user_id},
                    headers=headers,
                    timeout=90.0
                )
                resp.raise_for_status()
                result = resp.json()
            
            status      = result.get("status", "error")
            explanation = result.get("explanation", "")
            sql         = result.get("sql", "")
            row_count   = result.get("row_count", 0)
            duration    = result.get("duration_ms", 0)
            truncated   = result.get("truncated", False)

            if status == "success":
                header = (
                    f"🧠 **Analyse SQL** — {row_count} résultat(s) "
                    f"en {duration:.0f}ms"
                    + (f" _(limité à {row_count} lignes)_" if truncated else "")
                )
                sql_block = (
                    f"\n\n<details>\n<summary>🔍 Requête SQL générée</summary>"
                    f"\n\n```sql\n{sql}\n```\n\n</details>\n\n"
                )
                yield header + sql_block + explanation, "text_to_sql_agent"
            else:
                error_msg = result.get("error", "Erreur inconnue.")
                yield f"⛔ **Erreur Text-to-SQL**\n\n{explanation or error_msg}", "text_to_sql_agent"

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                yield (
                    "🔒 **Accès refusé** — La fonctionnalité Text-to-SQL est "
                    "réservée aux agents et administrateurs de la banque.",
                    "text_to_sql_agent",
                )
            elif e.response.status_code == 401:
                yield "🔒 **Non authentifié** — Veuillez vous reconnecter.", "text_to_sql_agent"
            else:
                yield f"❌ Erreur service SQL ({e.response.status_code}) : {e.response.text[:100]}", "text_to_sql_agent"
        except Exception as e:
            yield f"❌ Erreur service SQL : {str(e)}", "text_to_sql_agent"
        return

    # ── SEARCH AGENT ───────────────────────────────────────
    if agent_key == "search_agent":
        last_msg = messages[-1].content
        yield "💡 *Recherche d'informations en cours...*\n\n---\n\n", "search_agent"

        # Step 1: Query Optimization
        optimize_prompt = (
            "Extract the most relevant search keywords from the following user message to perform a precise web search. "
            "Focus on entities, dates, and core intent. Return ONLY the keywords, no explanation.\n\n"
            f"Message: {last_msg}"
        )
        try:
            resp = await llm.ainvoke(optimize_prompt)
            optimized_query = resp.content.strip()
        except Exception:
            optimized_query = last_msg

        # Step 2: Search Tool Execution
        try:
            import sys
            current_dir = os.path.dirname(os.path.abspath(__file__))
            server_script = os.path.join(current_dir, "..", "mcp_server.py")
            logger.info(f"[stream_agent_response] Connecting to MCP server for search: {server_script}")
            
            # Signature correct for langchain-mcp-adapters 0.1.x
            mcp_tools = await load_mcp_tools(
                None, 
                connection={
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [server_script],
                    "env": os.environ.copy()
                }
            )
            search_tool = next((t for t in mcp_tools if t.name == "web_search"), None)
            
            if search_tool:
                logger.info(f"[stream_agent_response] Calling MCP tool 'web_search' with query: {optimized_query}")
                results = await search_tool.ainvoke({"query": optimized_query})
                logger.info("[stream_agent_response] MCP search successful.")
            else:
                logger.warning("[stream_agent_response] MCP tool not found, falling back.")
                results = await sync_to_async(perform_web_search)(optimized_query)
        except Exception as e:
            logger.error(f"[stream_agent_response] MCP search failed: {e}")
            results = await sync_to_async(perform_web_search)(optimized_query)

        system = SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nRESULTS:\n{results}")
        async for chunk in llm.astream([system] + list(messages)):
            if chunk.content:
                yield chunk.content, "search_agent"
        return

    # ── FALLBACK / OTHERS ──────────────────────────────────
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    try:
        async for chunk in llm.astream([system] + list(messages)):
            if chunk.content:
                yield chunk.content, agent_key
    except Exception as e:
        logger.exception("[stream_agent_response] Error")
        yield f"❌ Error: {str(e)}", agent_key