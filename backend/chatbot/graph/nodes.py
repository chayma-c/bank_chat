import os
import re
import httpx
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from .state import BankChatState
from typing import Optional

logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")
MAIL_SERVICE_URL  = os.getenv("MAIL_SERVICE_URL",  "http://mail-service:8002")
ALERT_EMAIL       = os.getenv("ALERT_EMAIL",        "compliance@yourbank.com")

# ── HELPER : IBAN Extraction ──────────────────────────────────────────────────

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

# ── LLM Configuration ─────────────────────────────────────────────────────────

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.getenv("OLLAMA_MODEL", "llama3.2")
        logger.info(f"✅ Using Ollama LLM: {model} at {base_url}")
        return ChatOllama(base_url=base_url, model=model, temperature=0.7)
    else:
        api_key = os.getenv("GROQ_API_KEY")
        model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if not api_key:
            raise ValueError("GROQ_API_KEY is required for Groq provider.")
        logger.info(f"✅ Using Groq LLM: {model}")
        return ChatGroq(model=model, api_key=api_key, temperature=0.7)

llm = get_llm()

# ── System Prompts (Unified) ──────────────────────────────────────────────────

BASE_RESPONSE_POLICY = (
    "\n\nResponse policy: "
    "- Match answer length to the complexity of the user's request. "
    "- Use the minimum words necessary to fully answer. "
    "- Start with a direct answer first. "
    "- Prefer 1–5 sentences for simple questions. "
    "- Use bullets for multi-step explanations. "
    "- Keep responses under 350 words unless requested otherwise.\n"
    "Response Layout Rules: "
    "- Short paragraphs, headings for long answers, bullets for lists. "
    "- Prioritize readability and scanability."
)

SYSTEM_PROMPTS = {
    "account_agent": "You are BankChat, a specialized assistant for account inquiries." + BASE_RESPONSE_POLICY,
    "transfer_agent": "You are BankChat, a specialized assistant for money transfers." + BASE_RESPONSE_POLICY,
    "support_agent": "You are BankChat, a specialized customer support agent." + BASE_RESPONSE_POLICY,
    "fallback": "You are BankChat, a professional AI banking assistant." + BASE_RESPONSE_POLICY,
    "fraud_agent": (
        "You are BankChat's Senior Compliance & Fraud Prevention Officer. "
        "Your expertise includes AML (Anti-Money Laundering), KYC (Know Your Customer), and TRACFIN regulations. "
        "You provide professional, secure, and measured advice. "
        "If a user asks about suspicious activity or suspicious IBANs, you should encourage them  perform a formal analysis. "
        "Maintain a high level of confidentiality and professionalism."
    ) + BASE_RESPONSE_POLICY,
}

MAIL_AGENT_SYSTEM = """\
You are a banking compliance mail agent. respond with ONLY a valid JSON object.
{
  "subject": "French subject line",
  "template": "fraud_alert" | "critical_alert",
  "context": { ... }
}
"""

# ── Shared Fraud Logic ────────────────────────────────────────────────────────

def _get_fraud_decision_and_result(messages: list, user_id: str, session_id: str, auth_token: str | None = None) -> tuple:
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
    
    resp = llm.invoke(prompt).content.upper()
    reasoning = next((l.split(":", 1)[1].strip() for l in resp.split("\n") if "REASONING:" in l), "Delegated to fraud specialist.")
    decision = "ANALYZE" if "ANALYZE" in resp else "TALK"
    
    prefix = f"💡 *{reasoning}*\n\n---\n\n"
    
    if decision == "ANALYZE":
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        resp = httpx.post(
            f"{FRAUD_SERVICE_URL}/analyze",
            json={
                "message": last_msg,
                "iban": extract_iban(messages),
                "user_id": user_id,
                "session_id": session_id,
            },
            headers=headers,
            timeout=120.0
        )
        resp.raise_for_status()
        return prefix, resp.json(), True
    return prefix, None, False

# ── Intent Detection ──────────────────────────────────────────────────────────

def detect_intent(state: BankChatState) -> BankChatState:
    selected = state.get("selected_agent")
    if selected and selected not in ("orchestrator", "auto"):
        return {**state, "intent": "text_to_sql" if selected == "sql" else selected}
        
    last_msg = state["messages"][-1].content
    prompt = (
        "Classify the banking user's intent based on the following categories:\n"
        "- account: Balance inquiry, IBAN request, account status, RIB.\n"
        "- transfer: Sending money, wire transfers, recurring payments, RIB management.\n"
        "- support: Lost card, mobile app issues, password reset, generic help.\n"
        "- fraud: Suspicious transactions, fraudulent emails, reporting scams, auditing an IBAN.\n"
        "- fallback: Anything else, greetings, off-topic questions.\n\n"
        "EXAMPLES:\n"
        "'Quel est mon solde ?' -> account\n"
        "'Je veux envoyer 100€ à Ali' -> transfer\n"
        "'Ma carte est bloquée' -> support\n"
        "'Cet IBAN est-il suspect ?' -> fraud\n"
        "'Bonjour' -> fallback\n\n"
        f"Message: {last_msg}\n"
        "Classification (one word only):"
    )
    intent = llm.invoke(prompt).content.strip().lower().split()[0]
    
    # Simple hardcoded overrides for reliability
    if intent != "fraud" and any(k in last_msg.lower() for k in ["fraude", "iban", "tracfin", "louche", "suspect"]):
        intent = "fraud"
        
    return {**state, "intent": intent if intent in ("account", "transfer", "support", "fraud") else "fallback"}

# ── Nodes ───────────────────────────────────────────────────────────────────

def fraud_agent(state: BankChatState) -> BankChatState:
    try:
        prefix, result, is_analyze = _get_fraud_decision_and_result(
            state["messages"], state["user_id"], state["session_id"], state.get("auth_token")
        )
        if is_analyze:
            return {**state, "messages": [AIMessage(content=prefix + result.get("llm_summary", ""))], "agent": "fraud_agent", "context": result}
        
        resp = llm.invoke([SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])] + list(state["messages"]))
        return {**state, "messages": [AIMessage(content=prefix + resp.content)], "agent": "fraud_agent", "context": {}}
    except Exception as e:
        logger.exception("Fraud agent error")
        return {**state, "messages": [AIMessage(content=f"❌ Error: {e}")], "agent": "fraud_agent", "context": {}}

# (mail_agent full implementation is defined below at line ~279)




# ── Routing & Agents ──────────────────────────────────────────────────────────

def route_to_agent(state: BankChatState) -> str:
    return {
        "account":     "account_agent",
        "transfer":    "transfer_agent",
        "support":     "support_agent",
        "fraud":       "fraud_agent",
        "text_to_sql": "text_to_sql_agent",
    }.get(state["intent"], "fallback")

def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    resp = llm.invoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": agent_key}

def account_agent(state: BankChatState):   return _run_agent(state, "account_agent")
def transfer_agent(state: BankChatState):  return _run_agent(state, "transfer_agent")
def support_agent(state: BankChatState):   return _run_agent(state, "support_agent")

def handle_fallback(state: BankChatState): return _run_agent(state, "fallback")

def text_to_sql_agent(state: BankChatState) -> dict:
    """Text-to-SQL agent node — delegates to the text-to-sql-service microservice."""
    last_user_msg = ""
    for msg in reversed(state["messages"]):
        if hasattr(msg, "type") and msg.type == "human":
            last_user_msg = msg.content
            break
        if msg.__class__.__name__ == "HumanMessage":
            last_user_msg = msg.content
            break

    try:
        decision_resp = llm.invoke(decision_prompt).content
        lines     = decision_resp.strip().split("\n")
        reasoning = "Analyse de la requête..."
        decision  = "TALK"
        for line in lines:
            if line.upper().startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()
            if line.upper().startswith("DECISION:"):
                decision = "ANALYZE" if "ANALYZE" in line.upper() else "TALK"

        prefix = f"💡 *{reasoning}*\n\n---\n\n"

        if decision == "ANALYZE":
            iban = extract_iban(state["messages"])
            resp = httpx.post(
                f"{FRAUD_SERVICE_URL}/analyze",
                json={
                    "message":    last_msg,
                    "iban":       iban,
                    "action":     "fraud_check",
                    "user_id":    state.get("user_id", "anonymous"),
                    "session_id": state.get("session_id", ""),
                    "excel_path": "",
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            result = resp.json()

            logger.info(
                f"[fraud_agent] ANALYZE done — IBAN={result.get('iban')} "
                f"score={result.get('score_final')} risk={result.get('risk_level')} "
                f"tracfin={result.get('tracfin_required')}"
            )

            ai_response = prefix + (
                result.get("llm_summary") or
                result.get("summary") or
                "Analyse terminée."
            )
            return {
                **state,
                "messages": [AIMessage(content=ai_response)],
                "agent":    "fraud_agent",
                "context":  result,   # ← mail_agent lira ce dict
            }
        else:
            system = SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])
            resp   = llm.invoke([system] + list(state["messages"]))
            return {
                **state,
                "messages": [AIMessage(content=prefix + resp.content)],
                "agent":    "fraud_agent",
                "context":  {},   # pas d'analyse → pas de mail
            }

    except Exception as e:
        logger.exception("[fraud_agent] Error")
        return {
            **state,
            "messages": [AIMessage(content=f"❌ Erreur agent fraude : {str(e)}")],
            "agent":    "fraud_agent",
            "context":  {},
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


def call_mail_service(payload: dict) -> dict:
    """
    POST payload to mail-service /send.
    Returns the JSON response dict, or {"status": "error", "detail": ...} on failure.
    """
    try:
        resp = httpx.post(f"{MAIL_SERVICE_URL}/send", json=payload, timeout=15.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(f"[call_mail_service] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:
        logger.error(f"[call_mail_service] Failed: {exc}")
        return {"status": "error", "detail": str(exc)}


# ── Mail agent (agentique hybride) ────────────────────────────────────────────

def mail_agent(state: BankChatState) -> BankChatState:
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
        llm_resp = llm.invoke([
            SystemMessage(content=MAIL_AGENT_SYSTEM),
            HumanMessage(content=f"Fraud analysis result:\n{context_for_llm}"),
        ])
        parsed = _extract_json(llm_resp.content)
        if parsed:
            subject      = parsed.get("subject", subject)
            template     = parsed.get("template", template)
            mail_context = parsed.get("context", mail_context)
            logger.info(f"[mail_agent] LLM composed — template={template} subject={subject}")
        else:
            logger.warning(
                f"[mail_agent] LLM returned unparseable content, using fallback. "
                f"Raw (200): {llm_resp.content[:200]}"
            )
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

    logger.info(f"[mail_agent] → Calling mail-service: to={ALERT_EMAIL} subject={subject}")
    result = call_mail_service(payload)
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
            patch_resp = httpx.patch(
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



def stream_agent_response(intent: str, messages: list, user_id: str = "anonymous", session_id: str = "", auth_token: str | None = None):
    """
    Yields (token, agent_key) tuples — ou (token, agent_key, fraud_result) pour fraud ANALYZE.
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
            decision_resp = llm.invoke(decision_prompt).content
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
                resp = httpx.post(
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
                for chunk in llm.stream([system] + list(messages)):
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
        "text_to_sql": "text_to_sql_agent",
        "sql": "text_to_sql_agent",
    }

    agent_key = agent_key_map.get(intent, "fallback")

    # ── TEXT TO SQL (inchangé) ─────────────────────────────
    if agent_key == "text_to_sql_agent":
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.__class__.__name__ == "HumanMessage":
                last_user_msg = msg.content
                break

        try:
            resp = httpx.post(
                f"{os.getenv('TEXT2SQL_SERVICE_URL', 'http://text-to-sql-service:8003')}/query",
                json={"question": last_user_msg, "user_id": "anonymous"},
                timeout=60.0,
            )
            resp.raise_for_status()
            result = resp.json()

            yield result.get("explanation", "Query executed."), "text2sql_agent"

        except Exception as e:
            yield f"❌ Erreur service SQL : {str(e)}", "text2sql_agent"

        return

    # ── NORMAL STREAM ──────────────────────────────────────
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))

    try:
        for chunk in llm.stream([system] + list(messages)):
            if chunk.content:
                yield chunk.content, agent_key

    except Exception as e:
        logger.exception("[stream_agent_response] Error")
        yield f"❌ Error: {str(e)}", agent_key