import os
import re
import httpx
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from .state import BankChatState

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
    "- Prefer 1–3 sentences for simple questions. "
    "- Use bullets for multi-step explanations. "
    "- Keep responses under 120 words unless requested otherwise.\n"
    "Response Layout Rules: "
    "- Short paragraphs, headings for long answers, bullets for lists. "
    "- Prioritize readability and scanability."
)

SYSTEM_PROMPTS = {
    "account_agent": "You are BankChat, a specialized assistant for account inquiries." + BASE_RESPONSE_POLICY,
    "transfer_agent": "You are BankChat, a specialized assistant for money transfers." + BASE_RESPONSE_POLICY,
    "support_agent": "You are BankChat, a specialized customer support agent." + BASE_RESPONSE_POLICY,
    "fallback": "You are BankChat, a professional AI banking assistant." + BASE_RESPONSE_POLICY,
    "fraud_agent": "You are BankChat, a specialized expert in banking security and fraud detection." + BASE_RESPONSE_POLICY,
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
        "Decide if we need to call ANALYZE or TALK.\n"
        f"User: {last_msg}\n"
        "Format: REASONING: <text>\nDECISION: <ANALYZE or TALK>"
    )
    
    resp = llm.invoke(prompt).content.upper()
    reasoning = next((l.split(":", 1)[1].strip() for l in resp.split("\n") if "REASONING:" in l), "Processing...")
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
    prompt = f"Classify intent: account, transfer, support, fraud, fallback. Message: {last_msg}"
    intent = llm.invoke(prompt).content.strip().lower().split()[0]
    
    # Simple hardcoded overrides for reliability
    if intent != "fraud" and any(k in last_msg.lower() for k in ["fraude", "iban", "tracfin"]):
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

def mail_agent(state: BankChatState) -> BankChatState:
    context = state.get("context", {})
    if not context or (context.get("score_final", 0) < 50 and not context.get("tracfin_required")):
        return {**state, "agent": "mail_agent"}
    
    # Simplified mail composition for brevity, assuming existing mail-service logic
    payload = {
        "to": ALERT_EMAIL,
        "subject": f"Fraud Alert: {context.get('iban')}",
        "template": "fraud_alert",
        "context": context,
        "user_id": state["user_id"],
        "session_id": state["session_id"]
    }
    try:
        httpx.post(f"{MAIL_SERVICE_URL}/send", json=payload, timeout=10)
    except:
        logger.warning("Mail service failed")
    return {**state, "agent": "mail_agent"}

def stream_agent_response(intent: str, state: BankChatState):
    messages = state["messages"]
    if intent == "fraud":
        try:
            prefix, result, is_analyze = _get_fraud_decision_and_result(
                messages, state["user_id"], state["session_id"], state.get("auth_token")
            )
            yield prefix, "fraud_agent"
            if is_analyze:
                yield result.get("llm_summary", "Analysis complete."), "fraud_agent", result
                return
            agent_key = "fraud_agent"
        except Exception as e:
            yield f"❌ Error: {e}", "fraud_agent"
            return
    else:
        agent_key = {"account": "account_agent", "transfer": "transfer_agent", "support": "support_agent"}.get(intent, "fallback")
    
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    for chunk in llm.stream([system] + list(messages)):
        if chunk.content:
            yield chunk.content, agent_key

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