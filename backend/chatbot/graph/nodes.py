import os
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage
from .state import BankChatState

# ── Configuration du LLM (Groq ou Ollama) ─────────────────────────

def get_llm():
    """
    Initialise le LLM selon la configuration dans .env
    
    Variables d'environnement :
    - LLM_PROVIDER: "groq" (défaut) ou "ollama"
    - GROQ_API_KEY: clé API Groq (si provider=groq)
    - GROQ_MODEL: modèle Groq (défaut: llama-3.3-70b-versatile)
    - OLLAMA_BASE_URL: URL Ollama (défaut: http://localhost:11434)
    - OLLAMA_MODEL: modèle Ollama (défaut: llama3.2)
    """
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    
    if provider == "ollama":
        # Configuration Ollama (modèle local)
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("OLLAMA_MODEL", "llama3.2")
        
        print(f"✅ Using Ollama LLM: {model} at {base_url}")
        
        return ChatOllama(
            base_url=base_url,
            model=model,
            temperature=0.7,
        )
    else:
        # Configuration Groq (API cloud) - par défaut
        api_key = os.getenv("GROQ_API_KEY")
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        
        if not api_key:
            raise ValueError(
                "GROQ_API_KEY is required when LLM_PROVIDER=groq. "
                "Set it in your .env file or switch to LLM_PROVIDER=ollama"
            )
        
        print(f"✅ Using Groq LLM: {model}")
        
        return ChatGroq(
            model=model,
            api_key=api_key,
            temperature=0.7,
        )

# Initialiser le LLM au démarrage
llm = get_llm()

# ── Specialized system prompts per agent ─────────────────────────────────────

SYSTEM_PROMPTS = {
    "account_agent": (
        "You are BankChat, a specialized banking assistant for account inquiries. "
        "You help customers with: account balances, transaction history, account statements, "
        "account details, interest rates, fees, credit limits and savings plans. "
        "Be professional, precise and concise. Never ask for passwords or PINs. "
        "If real account data is needed, explain that the customer must log in to the secure portal."
    ),
    "transfer_agent": (
        "You are BankChat, a specialized banking assistant for money transfers and payments. "
        "You help customers with: wire transfers, internal transfers between accounts, "
        "payment scheduling, beneficiary management, transfer limits, SWIFT/IBAN/BIC details, "
        "international fees and currency conversion. "
        "Always stress the importance of verifying recipient details before confirming a transfer."
    ),
    "support_agent": (
        "You are BankChat, a specialized banking customer support agent. "
        "You assist with: card blocking and unblocking, fraud alerts and dispute filing, "
        "complaints and escalations, technical issues with online banking, "
        "account opening procedures, loan and mortgage inquiries, and product information. "
        "Be empathetic, patient and always offer a clear next step."
    ),
    "fallback": (
        "You are BankChat, a professional AI banking assistant for a modern retail bank. "
        "Answer banking-related questions clearly, concisely and professionally. "
        "You can help with accounts, transfers, cards, loans, investments and general banking advice. "
        "If a question is completely unrelated to banking or finance, politely let the customer know "
        "you are specialized in banking services and redirect them appropriately."
    ),
}

# ── Intent detection ──────────────────────────────────────────────────────────

def detect_intent(state: BankChatState) -> BankChatState:
    """Classifies the user message into one of 4 intents."""
    last_msg = state["messages"][-1].content

    prompt = (
        "You are a banking intent classifier. "
        "Read the customer message below and reply with ONLY one word from this list:\n"
        "  account   → balance, transactions, statements, account info\n"
        "  transfer  → send money, wire, payment, IBAN, beneficiary\n"
        "  support   → problem, card blocked, fraud, complaint, help\n"
        "  fallback  → anything else\n\n"
        f"Customer message: {last_msg}\n"
        "Reply with one word only."
    )

    response = llm.invoke(prompt)
    intent = response.content.strip().lower().split()[0]
    # Sanitize in case LLM returns something unexpected
    if intent not in ("account", "transfer", "support"):
        intent = "fallback"

    return {**state, "intent": intent}


def route_to_agent(state: BankChatState) -> str:
    """Returns the name of the next node based on detected intent."""
    return {
        "account":  "account_agent",
        "transfer": "transfer_agent",
        "support":  "support_agent",
    }.get(state["intent"], "fallback")


# ── Specialized agent nodes ───────────────────────────────────────────────────

def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    """Generic agent runner — injects the right system prompt."""
    system = SystemMessage(content=SYSTEM_PROMPTS[agent_key])
    messages_with_system = [system] + list(state["messages"])
    response = llm.invoke(messages_with_system)
    return {
        **state,
        "messages": [AIMessage(content=response.content)],
        "agent": agent_key,
    }


def account_agent(state: BankChatState) -> BankChatState:
    return _run_agent(state, "account_agent")

def transfer_agent(state: BankChatState) -> BankChatState:
    return _run_agent(state, "transfer_agent")

def support_agent(state: BankChatState) -> BankChatState:
    return _run_agent(state, "support_agent")

def handle_fallback(state: BankChatState) -> BankChatState:
    return _run_agent(state, "fallback")


# ── Streaming helper (used directly by the stream view) ──────────────────────

def stream_agent_response(intent: str, messages: list):
    """
    Yields text tokens from the LLM one by one.
    Used by StreamChatView for real-time streaming.
    """
    agent_key = {
        "account":  "account_agent",
        "transfer": "transfer_agent",
        "support":  "support_agent",
    }.get(intent, "fallback")

    system = SystemMessage(content=SYSTEM_PROMPTS[agent_key])
    messages_with_system = [system] + list(messages)

    for chunk in llm.stream(messages_with_system):
        if chunk.content:
            yield chunk.content, agent_key


