import os
import sys
import re
import httpx
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from .state import BankChatState
from ..search_tool import perform_web_search
from typing import Optional, Any

# MCP Imports
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent

# Local Imports
from .prompts import BASE_POLICY, SYSTEM_PROMPTS, MAIL_AGENT_SYSTEM, ADVANCED_RESEARCH_PROMPT

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
# Prompts are now imported from .prompts

async def get_research_agent():
    """Returns a ReAct agent configured with the MCP search tool."""
    import sys
    current_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(current_dir, "..", "mcp_server.py")
    
    # Load MCP tools
    mcp_tools = await load_mcp_tools(
        None, 
        connection={
            "transport": "stdio",
            "command": sys.executable,
            "args": [server_script],
            "env": os.environ.copy()
        }
    )
    
    # Filter for the search tool
    search_tool = next((t for t in mcp_tools if t.name == "web_search"), None)
    if not search_tool:
        return None
        
    # Create the ReAct agent
    return create_react_agent(
        llm, 
        tools=[search_tool], 
        state_modifier=ADVANCED_RESEARCH_PROMPT
    )

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

 
# ── NIVEAU 1 : EXCLUSIONS STRICTES ───────────────────────────────────────────
# Si la requête contient ces patterns → NE JAMAIS router vers text_to_sql

_TEXT_TO_SQL_EXCLUSIONS = [
    # Fraude avec IBAN (va vers fraud_agent)
    r"\bIBAN[A-Z0-9_\s]*\b",
    r"\banalyse.*fraude.*IBAN\b",
    r"\bfraude.*pour\s+IBAN\b",
    r"\bTRACFIN.*IBAN\b",
    
    # Actions de transfert (va vers transfer_agent)
    r"\bfaire.*virement\b",
    r"\benvoyer.*\d+\s*€\b",
    r"\btransf[eé]rer\b",
    r"\bvirement.*vers\b",
    r"\bpayer\b",
    
    # Consultation de compte (va vers account_agent)
    r"\bmon solde\b",
    r"\bmes comptes?\b",
    r"\bsolde.*compte\b",
    r"\brelev[eé]\b",
    r"\bmes op[eé]rations\b",
    
    # Signalement de fraude (va vers fraud_agent)
    r"\bsignaler.*fraude\b",
    r"\bsignaler.*phishing\b",
    r"\brapport.*fraude\b",
    r"\bqui est.*suspect\b",
]


# ── NIVEAU 2 : INDICATEURS SQL ANALYTIQUES ───────────────────────────────────
# La requête DOIT contenir au moins UN de ces patterns pour être éligible
 
_TEXT_TO_SQL_INDICATORS = [
    # Quantitatifs stricts
    r"\bcombien\s+(de|y\s+a-t-il|y\s+a)\b",
    r"\bnombre\s+(total\s+)?de\b",
    r"\btotal\s+de\b",
    r"\bmoyenne\s+de\b",
    r"\bsomme\s+de\b",
    r"\bpourcentage\s+de\b",
    
    # Listings analytiques
    r"\bliste\s+(les|toutes?|des)\s+(transactions|r[èe]gles|analyses|d[ée]cisions)\b",
    r"\baffiche\s+(les|toutes?)\s+(transactions|r[èe]gles)\b",
    r"\bmontre[-\s]moi\s+(les|toutes?)\s+\b",
    r"\btop\s+\d+\b",
    r"\bpremier[s]?\s+\d+\b",
    
    # Recherche filtrée
    r"\btrouv(?:e|er)\s+(les|toutes?)\s+(transactions|r[èe]gles)\b",
    r"\bcherch(?:e|er)\s+dans\s+(la\s+base|les\s+transactions)\b",
    r"\bfiltre\b",
    r"\bo[ùu]\s+.*\s*=\s*\b",
    r"\bavec\s+.*\s*=\s*\b",
    
    # Comparatifs et analytiques
    r"\bcompare\b",
    r"\br[ée]partition\b",
    r"\btendance\b",
    r"\b[ée]volution\b",
]

# ── NIVEAU 3 : CONTEXTE BASE DE DONNÉES ──────────────────────────────────────
# Renforce la confiance si présent (mais pas obligatoire)
 
_SQL_CONTEXT_KEYWORDS = {
    # Tables
    "transactions", "fraud_rules", "fraud_decision_logs",
    "règles", "analyses", "décisions", "logs",
    
    # Attributs consultables (pas IBAN seul !)
    "status", "statut", "score", "montant", "amount",
    "bloqué", "bloquée", "bloquées", 
    "frauduleux", "frauduleuses",
    "suspect", "suspectes", 
    "date", "créé",
    
    # Contexte lecture
    "base", "database", "table", "données", "historique",
    "total", "nombre",
}
 
 
def _is_text_to_sql_intent(message: str) -> bool:
    """
    Détection stricte à 3 niveaux pour text_to_sql.
    
    Returns:
        True si la requête doit aller vers text_to_sql_agent
    """
    import re
    msg_lower = message.lower()
    
    # ── NIVEAU 1 : EXCLUSIONS (priorité absolue) ─────────────────────────────
    for pattern in _TEXT_TO_SQL_EXCLUSIONS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            logger.info(f"[text_to_sql] ❌ EXCLUDED by pattern: {pattern}")
            return False
    
    # ── NIVEAU 2 : INDICATEURS ANALYTIQUES REQUIS ────────────────────────────
    has_indicator = False
    for pattern in _TEXT_TO_SQL_INDICATORS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            has_indicator = True
            logger.info(f"[text_to_sql] ✅ Indicator found: {pattern}")
            break
    
    if not has_indicator:
        logger.info("[text_to_sql] ❌ No SQL analytical indicator")
        return False
    
    # ── NIVEAU 3 : CONTEXTE SQL (bonus de confiance) ─────────────────────────
    has_context = any(kw in msg_lower for kw in _SQL_CONTEXT_KEYWORDS)
    
    if has_context:
        logger.info("[text_to_sql] ✅ SQL context detected → APPROVED")
    else:
        logger.info("[text_to_sql] ⚠️ No SQL context but indicator present → APPROVED")
    
    return True

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
   # ── PRIORITÉ 1 : Sélection manuelle ──────────────────────────────────────
    if selected and selected not in ("orchestrator", "auto"):
        intent = "text_to_sql" if selected == "sql" else selected
        logger.info(f"[detect_intent] Manual selection: {intent}")
        return {**state, "intent": intent}
 
    last_msg = state["messages"][-1].content
    lower_msg = last_msg.lower()
   
   # ── PRIORITÉ 2 : Text-to-SQL avec détection stricte ──────────────────────
    if _is_text_to_sql_intent(last_msg):
        logger.info(f"[detect_intent] ✅ Routing to text_to_sql_agent")
        return {**state, "intent": "text_to_sql"}
    

    # ── Priority 3: LLM classification ──────────────────────────────────────
    prompt = (
        "Classify the banking user's intent into ONE of these categories:\n"
        "- account      : Balance, IBAN request, account status, RIB.\n"
        "- transfer     : Sending money, wire transfers, recurring payments.\n"
        "- support      : Lost card, mobile app issues, password reset, generic help.\n"
        "- fraud        : Reporting fraudulent emails, scams, phishing, analyzing an IBAN for fraud.\n"
        "- search       : General info not in bank DB, market trends, exchange rates, news.\n"
        "- fallback     : Greetings, off-topic, anything else.\n\n"
        "IMPORTANT: Do NOT classify database queries (counts, lists, statistics) here - "
        "they are handled separately.\n\n"
        "EXAMPLES:\n"
        "'Quel est mon solde ?' -> account\n"
        "'Je veux envoyer 100€ à Ali' -> transfer\n"
        "'Ma carte est bloquée' -> support\n"
        "'Analyser l'IBAN XXXX pour fraude' -> fraud\n"
        "'Signaler un phishing' -> fraud\n"
        "'Quel est le cours de l'EUR/USD ?' -> search\n"
        "'Bonjour' -> fallback\n\n"
        f"Message: {last_msg}\n"
        "Classification (one word only):"
    )

    intent = llm.invoke(prompt).content.strip().lower().split()[0]

    # ── Validation & Override ─────────────────────────────────────────────────
    
    # Fraud keywords override
    _FRAUD_KEYWORDS = ["fraude", "frauduleux", "phishing", "arnaque", "suspect"]
    if intent not in ("fraud", "text_to_sql") and any(k in lower_msg for k in _FRAUD_KEYWORDS):
        if "iban" in lower_msg or "signaler" in lower_msg or "analyser" in lower_msg:
            intent = "fraud"
            logger.info(f"[detect_intent] Fraud keyword override → fraud")
    
    # Search keywords override
    _SEARCH_KEYWORDS = ["cours", "taux de change", "météo", "actualité", "news"]
    if intent not in ("search", "text_to_sql", "fraud") and any(k in lower_msg for k in _SEARCH_KEYWORDS):
        intent = "search"
        logger.info(f"[detect_intent] Search keyword override → search")
    
    # Validation finale
    valid_intents = ("account", "transfer", "support", "fraud", "search", "text_to_sql")
    resolved = intent if intent in valid_intents else "fallback"
    
    logger.info(f"[detect_intent] '{last_msg[:60]}...' → {resolved}")
    # Validation finale
    valid_intents = ("account", "transfer", "support", "fraud", "search", "text_to_sql")
    resolved = intent if intent in valid_intents else "fallback"
    
    # ── LOG D'INTENTION AJOUTÉ POUR LE RAPPORT ET LES LOGS DOCKER ──
    logger.info(
        f"\n"
        f"╔══════════════════════════════════════════════════════════════════════════\n"
        f"║ [INTENT_DETECTION] Analyse de l'orchestrateur LangGraph\n"
        f"║ ➜ Entrée utilisateur : \"{last_msg[:80]}\"\n"
        f"║ ➜ Intention détectée : {resolved.upper()}\n"
        f"║ ➜ Routage dynamique  : {route_to_agent({'intent': resolved})}\n"
        f"╚══════════════════════════════════════════════════════════════════════════"
    )
    
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
    messages = state["messages"]
    last_msg = messages[-1].content
    logger.info(f"[search_agent] Starting autonomous research for: {last_msg}")

    # Step 1: Run Autonomous Research Agent
    try:
        agent = await get_research_agent()
        if agent:
            # We pass the full message history to give the agent context
            result = await agent.ainvoke({"messages": messages})
            # Extract final answer from the last message in the returned state
            final_answer = result["messages"][-1].content
        else:
            logger.warning("[search_agent] Autonomous agent not initialized, falling back.")
            results = perform_web_search(last_msg)
            final_answer = (await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nRESULTS:\n{results}"),
                HumanMessage(content=last_msg)
            ])).content
            
    except Exception as e:
        logger.error(f"[search_agent] Autonomous research failed: {e}")
        # Fallback to legacy single-pass
        results = perform_web_search(last_msg)
        final_answer = (await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nRESULTS:\n{results}"),
            HumanMessage(content=last_msg)
        ])).content

    return {**state, "messages": messages + [AIMessage(content=final_answer)]}

# ── Helper : message d'erreur SQL convivial ─────────────────────────────────────

def _format_sql_error(error_msg: str, explanation: str, sql: str) -> str:
    """
    Convertit un message d'erreur brut du text-to-sql-service en un message
    UI convivial, avec un bloc détail technique repliable.

    Cela couvre deux cas :
      - Erreurs de validation (error_type renvoyé dans la réponse JSON)
      - Erreurs d'exécution PostgreSQL résiduelles
    """
    msg_lower = (error_msg or "").lower()
    expl_lower = (explanation or "").lower()
    combined = msg_lower + " " + expl_lower

    # ── Classifier le type d'erreur ─────────────────────────────────────
    if any(k in combined for k in ("delete", "update", "insert", "drop", "alter",
                                   "truncate", "opération interdite", "dml",
                                   "lecture seule", "non autorisé")):
        icon, title = "🚫", "Opération non autorisée"
        guidance = (
            "Je suis désolé, mais je ne peux pas exécuter des opérations de modification "
            "(DELETE, UPDATE, INSERT, DROP…). Ce système est en **lecture seule** "
            "pour protéger l'intégrité des données bancaires.\n"
            "Reformulez votre demande sous forme de consultation."
        )
    elif any(k in combined for k in (";", "requêtes multiples", "stacked")):
        icon, title = "🚫", "Requêtes multiples bloquées"
        guidance = (
            "Pour des raisons de sécurité, seule **une seule requête SELECT** est acceptée à la fois. "
            "Les requêtes enchaînées via `;` sont interdites."
        )
    elif any(k in combined for k in ("pg_", "information_schema", "système interdit",
                                     "tables système", "system_table")):
        icon, title = "🔒", "Accès système interdit"
        guidance = (
            "L'accès aux tables système PostgreSQL est strictement interdit. "
            "Veuillez reformuler votre question en ciblant les tables bancaires disponibles."
        )
    elif any(k in combined for k in ("hors périmètre", "non autorisée",
                                     "unknown_table", "table '")):
        icon, title = "📊", "Table hors périmètre"
        guidance = (
            "La table demandée n'est pas accessible dans ce système. "
            "Les tables autorisées sont : **transactions**, **fraud\_rules**, **fraud\_decision\_logs**."
        )
    elif any(k in combined for k in ("colonne", "column", "hallucination",
                                     "inconnue", "does not exist")):
        icon, title = "❓", "Colonne inconnue dans le schéma"
        guidance = (
            "La question fait référence à une colonne qui n'existe pas dans le schéma bancaire. "
            "Reformulez en utilisant uniquement les colonnes disponibles dans les tables concernées."
        )
    else:
        icon, title = "❌", "Erreur lors de l'analyse"
        guidance = (
            "Une erreur s'est produite lors du traitement de votre requête. "
            "Vérifiez que votre question porte bien sur les transactions, "
            "les règles de fraude ou les décisions d'analyse."
        )

    # Détail technique en bloc repliable
    detail_src = explanation or error_msg or ""
    sql_block = f"\n**SQL généré :**\n```sql\n{sql}\n```" if sql else ""
    detail = (
        f"<details>\n<summary>Détail technique</summary>\n\n"
        f"> {detail_src}"
        f"{sql_block}\n</details>"
    )

    return f"{icon} **{title}**\n\n{guidance}\n\n{detail}"


def text_to_sql_agent(state: BankChatState) -> dict:
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
            ai_content = _format_sql_error(error_msg, explanation, sql)
            logger.warning(f"[text_to_sql_agent] Service error [{result.get('status')}]: {error_msg}")

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
    intent     = state.get("intent", "fallback")
    # ── LOG DE FLUX AJOUTÉ POUR TRACER LE STREAMING ──
    logger.info(f"[STREAM] Initialisation du flux asynchrone HTTP (Chunk Streaming) pour l'intention : {intent.upper()}")
    
    messages   = state.get("messages", [])
    user_id    = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "")
    auth_token = state.get("auth_token")

    # ── FRAUD FLOW ─────────────────────────────────────────
    if intent == "fraud":

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
                yield _format_sql_error(error_msg, explanation, sql), "text_to_sql_agent"

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
        logger.info(f"[stream_agent_response] Starting autonomous streaming research for: {last_msg}")
        try:
            agent = await get_research_agent()
            if not agent:
                raise Exception("Could not initialize research agent")

            # Use astream_events to capture tokens AND tool calls
            async for event in agent.astream_events({"messages": list(messages)}, version="v2"):
                kind = event["event"]
                
                # 1. Handle tokens from the LLM
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        yield content, "search_agent"
                
                # 2. Handle tool starts (show progress to user)
                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    tool_input = event["data"].get("input", {}).get("query", "...")
                    if tool_name == "web_search":
                        yield f"\n\n> 🔍 **Recherche: {tool_input}**\n\n", "search_agent"
                    else:
                        yield f"\n\n> 🛠️ **Action: {tool_name}**\n\n", "search_agent"

            return

        except Exception as e:
            logger.error(f"[stream_agent_response] Autonomous stream failed: {e}")
            # Fallback to single-pass search
            results = await sync_to_async(perform_web_search)(last_msg)
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