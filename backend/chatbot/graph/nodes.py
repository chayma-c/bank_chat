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
from ..search_tool import perform_web_search

# ── SETUP ───────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")
MAIL_SERVICE_URL  = os.getenv("MAIL_SERVICE_URL",  "http://mail-service:8002")
ALERT_EMAIL       = os.getenv("ALERT_EMAIL",        "compliance@yourbank.com")

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
        "You are BankChat's Research Assistant. Summarize web search results clearly. "
        "Cite sources using URLs."
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

def _get_reasoning(intent: str, last_msg: str) -> str:
    """Unified Thinking output for all agents."""
    if intent == "fallback": return ""
    try:
        resp = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPTS["reasoning_prompt"]),
            HumanMessage(content=f"Intent: {intent}\nMessage: {last_msg}")
        ]).content.strip()
        return f"💡 *{resp}*\n\n---\n\n"
    except Exception:
        return "💡 *Traitement de votre demande...*\n\n---\n\n"

# ── INTENT DETECTION ────────────────────────────────────────────────────────

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
        "- search: General info not in bank DB, market trends, exchange rates, latest financial news.\n"
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
    
    # Overrides
    if any(k in last_msg.lower() for k in ["fraude", "iban", "tracfin", "louche", "suspect"]): intent = "fraud"
    if any(k in last_msg.lower() for k in ["cherche", "search", "trouve", "actualité", "cours de", "taux"]): intent = "search"
        
    return {**state, "intent": intent if intent in ("account", "transfer", "support", "fraud", "search") else "fallback"}

# ── NODES ───────────────────────────────────────────────────────────────────

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

def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    resp = llm.invoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": agent_key}

def account_agent(state: BankChatState):   return _run_agent(state, "account_agent")
def transfer_agent(state: BankChatState):  return _run_agent(state, "transfer_agent")
def support_agent(state: BankChatState):   return _run_agent(state, "support_agent")
def handle_fallback(state: BankChatState): return _run_agent(state, "fallback")
def fraud_agent(state: BankChatState):    return _run_agent(state, "fraud_agent")

def search_agent(state: BankChatState) -> BankChatState:
    last_msg = state["messages"][-1].content
    results = perform_web_search(last_msg)
    system = SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nSEARCH RESULTS:\n{results}")
    resp = llm.invoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": "search_agent"}

def text_to_sql_agent(state: BankChatState) -> dict:
    return _run_agent(state, "text_to_sql_agent")

# ── MAIL AGENT (FULL) ───────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match: return None
    try: return json.loads(match.group())
    except: return None

def call_mail_service(payload: dict) -> dict:
    try:
        resp = httpx.post(f"{MAIL_SERVICE_URL}/send", json=payload, timeout=15.0)
        return resp.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}

def mail_agent(state: BankChatState) -> BankChatState:
    context = state.get("context", {})
    score_final = context.get("score_final", 0)
    tracfin = context.get("tracfin_required", False)
    iban = context.get("iban", "")

    if not (score_final >= 50 or tracfin): return {**state, "agent": "mail_agent"}

    template = "critical_alert" if (score_final >= 80 or tracfin) else "fraud_alert"
    subject = f"[BankChat] 🔴 Alerte Fraude score {score_final} — IBAN {iban}"
    mail_context = {
        "iban": iban, "score_final": score_final, "risk_level": context.get("risk_level", ""),
        "tracfin_required": tracfin, "llm_summary": context.get("llm_summary", ""),
        "download_url": context.get("download_url", ""), "triggered_count": len(context.get("fraud_results", []))
    }

    # LLM Composition
    try:
        llm_resp = llm.invoke([
            SystemMessage(content=MAIL_AGENT_SYSTEM),
            HumanMessage(content=f"Fraud result: {json.dumps(mail_context)}")
        ])
        parsed = _extract_json(llm_resp.content)
        if parsed:
            subject = parsed.get("subject", subject)
            template = parsed.get("template", template)
            mail_context = parsed.get("context", mail_context)
    except: pass

    payload = {
        "to": ALERT_EMAIL, "subject": subject, "template": template, "context": mail_context,
        "attachment_path": context.get("report_path"), "iban": iban, "score_final": score_final,
        "session_id": state.get("session_id", ""), "user_id": state.get("user_id", "anonymous")
    }

    result = call_mail_service(payload)
    status = result.get("status", "error")

    # DB Update
    log_id = context.get("decision_log_id") or state.get("decision_log_id")
    if log_id:
        try:
            httpx.patch(f"{FRAUD_SERVICE_URL}/decision-logs/{log_id}/mail", json={
                "mail_sent": status == "sent", "mail_status": status, "mail_id": result.get("id")
            }, timeout=5.0)
        except: pass

    return {**state, "agent": "mail_agent", "context": {**context, "mail_results": [{"status": status}]}}

# ── STREAMING ORCHESTRATOR ──────────────────────────────────────────────────

def stream_agent_response(intent: str, messages: list, user_id: str = "anonymous", session_id: str = "", auth_token: str | None = None):
    last_msg = next((m.content for m in reversed(messages) if m.__class__.__name__ == "HumanMessage"), "")
    
    # 1. THINKING OUTPUT
    yield _get_reasoning(intent, last_msg), f"{intent}_agent"

    # 2. FRAUD
    if intent == "fraud":
        try:
            decision = llm.invoke(f"ANALYZE or TALK: {last_msg}").content.upper()
            if "ANALYZE" in decision:
                iban = extract_iban(messages)
                headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
                resp = httpx.post(f"{FRAUD_SERVICE_URL}/analyze", json={
                    "message": last_msg, "iban": iban, "user_id": user_id, "session_id": session_id
                }, headers=headers, timeout=120.0)
                result = resp.json()
                
                if result.get("error") and "Aucune transaction" in result.get("error"):
                    yield "🔍 *IBAN inconnu localement. Recherche web...*\n\n", "fraud_agent"
                    search_res = perform_web_search(f"scam report IBAN {iban}")
                    yield f"--- Recherche externe ---\n{search_res}\n\n---\n\n", "fraud_agent"
                
                yield result.get("llm_summary", "Analyse terminée."), "fraud_agent", result
            else:
                system = SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])
                for chunk in llm.stream([system] + list(messages)):
                    if chunk.content: yield chunk.content, "fraud_agent"
        except Exception as e:
            yield f"❌ Erreur fraude: {e}", "fraud_agent"
        return

    # 3. SEARCH
    if intent == "search":
        try:
            yield "🔍 *Recherche sur le web en cours...*\n\n", "search_agent"
            res = perform_web_search(last_msg)
            system = SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nRESULTS:\n{res}")
            for chunk in llm.stream([system] + list(messages)):
                if chunk.content: yield chunk.content, "search_agent"
        except Exception as e:
            yield f"❌ Erreur recherche: {e}", "search_agent"
        return

    # 4. SQL
    if intent in ("text_to_sql", "sql"):
        try:
            resp = httpx.post(f"{os.getenv('TEXT2SQL_SERVICE_URL', 'http://text-to-sql-service:8003')}/query", 
                              json={"question": last_msg, "user_id": user_id}, timeout=60.0)
            yield resp.json().get("explanation", "Données prêtes."), "text2sql_agent"
        except Exception as e:
            yield f"❌ Erreur SQL: {e}", "text2sql_agent"
        return

    # 5. NORMAL
    agent_key = {"account": "account_agent", "transfer": "transfer_agent", "support": "support_agent"}.get(intent, "fallback")
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    try:
        for chunk in llm.stream([system] + list(messages)):
            if chunk.content: yield chunk.content, agent_key
    except Exception as e:
        yield f"❌ Erreur: {e}", agent_key