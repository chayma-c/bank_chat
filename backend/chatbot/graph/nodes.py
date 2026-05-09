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

# MCP Imports
from mcp import StdioServerParameters
from langchain_mcp_adapters.tools import load_mcp_tools

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
    
    # Simple hardcoded overrides for reliability
    lower_msg = last_msg.lower()
    if intent != "fraud" and any(k in lower_msg for k in ["fraude", "iban", "tracfin", "louche", "suspect"]):
        intent = "fraud"
    
    # Force search for time-related or exchange rate queries
    search_keywords = ["cours", "taux", "change", "bourse", "prix", "météo", "actualité", "news", "date", "heure", "qui est", "quand"]
    if intent != "search" and any(k in lower_msg for k in search_keywords):
        intent = "search"
        
    valid_intents = ("account", "transfer", "support", "fraud", "search")
    return {**state, "intent": intent if intent in valid_intents else "fallback"}

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
        iban = extract_iban(messages)
        try:
            response = httpx.post(
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
    elif intent == "text_to_sql":
        try:
            result = text_to_sql_agent(state)
            # text_to_sql_agent returns messages as a list of AIMessages
            content = result["messages"][0].content
            yield content, "text2sql_agent"
            return
        except Exception as e:
            yield f"❌ Error service SQL : {e}", "text2sql_agent"
            return
    else:
        agent_key = {
            "account":  "account_agent",
            "transfer": "transfer_agent",
            "support":  "support_agent",
            "search":   "search_agent"
        }.get(intent, "fallback")
    
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    for chunk in llm.stream([system] + list(messages)):
        if chunk.content:
            yield chunk.content, agent_key

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

def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    resp = llm.invoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": agent_key}

def account_agent(state: BankChatState):   return _run_agent(state, "account_agent")
def transfer_agent(state: BankChatState):  return _run_agent(state, "transfer_agent")
def support_agent(state: BankChatState):   return _run_agent(state, "support_agent")
def handle_fallback(state: BankChatState): return _run_agent(state, "fallback")

def search_agent(state: BankChatState) -> BankChatState:
    last_msg = state["messages"][-1].content
    
    # Step 1: Query Optimization (Keyword extraction)
    optimize_prompt = (
        "Extract the most relevant search keywords from the following user message to perform a precise web search. "
        "Focus on entities, dates, and core intent. Return ONLY the keywords, no explanation.\n\n"
        f"Message: {last_msg}"
    )
    optimized_query = llm.invoke(optimize_prompt).content.strip()
    logger.info(f"[search_agent] Optimized query: {optimized_query}")

    # Step 2: MCP Search Call
    try:
        # Determine path to mcp_server.py
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        server_script = os.path.join(current_dir, "..", "mcp_server.py")
        
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[server_script],
            env=os.environ.copy()
        )
        
        # Load MCP tools (via stdio bridge)
        logger.info(f"[search_agent] Connecting to MCP server: {server_script}")
        mcp_tools = load_mcp_tools("stdio", server_params)
        
        # Find the web_search tool
        search_tool = next((t for t in mcp_tools if t.name == "web_search"), None)
        
        if search_tool:
            logger.info(f"[search_agent] Calling MCP tool 'web_search' with query: {optimized_query}")
            results = search_tool.invoke({"query": optimized_query})
            logger.info("[search_agent] MCP tool results received successfully.")
        else:
            logger.warning("[search_agent] web_search tool not found in MCP server, falling back to legacy tool.")
            results = perform_web_search(optimized_query)
            
    except Exception as e:
        logger.error(f"[search_agent] MCP call failed: {e}. Falling back to legacy tool.")
        results = perform_web_search(optimized_query)

    # Step 3: Summarization
    system = SystemMessage(content=SYSTEM_PROMPTS["search_agent"] + f"\n\nSEARCH RESULTS:\n{results}")
    resp = llm.invoke([system] + list(state["messages"]))
    return {**state, "messages": [AIMessage(content=resp.content)], "agent": "search_agent"}

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
        response = httpx.post(
            f"{os.getenv('TEXT2SQL_SERVICE_URL', 'http://text-to-sql-service:8003')}/query",
            json={"question": last_user_msg, "user_id": state.get("user_id", "anonymous")},
            timeout=60.0,
        )
        response.raise_for_status()
        result = response.json()
        return {
            "messages": [AIMessage(content=result.get("explanation", "Query executed."))],
            "agent": "text2sql_agent",
        }
    except Exception as e:
        logger.exception("[text_to_sql_agent] Error")
        return {
            "messages": [AIMessage(content=f"❌ Erreur service SQL : {str(e)}")],
            "agent": "text2sql_agent",
            "error": str(e),
        }