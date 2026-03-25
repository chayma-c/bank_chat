import os
import re
import httpx
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from .state import BankChatState

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")

# ── IBAN extraction helper ────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)

def extract_iban(messages: list) -> str:
    """
    Extrait le premier IBAN trouvé dans la liste de messages.
    Parcourt du plus récent au plus ancien.
    Retourne "" si aucun IBAN trouvé.
    """
    for msg in reversed(messages):
        content = msg.content if hasattr(msg, "content") else str(msg)
        match = IBAN_PATTERN.search(content)
        if match:
            return (match.group(1) or match.group(2) or "").replace(" ", "").upper()
    return ""


# ── Configuration du LLM (Groq ou Ollama) ────────────────────────────────────

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").lower()

    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.getenv("OLLAMA_MODEL", "llama3.2")
        print(f"✅ Using Ollama LLM: {model} at {base_url}")
        return ChatOllama(base_url=base_url, model=model, temperature=0.7)
    else:
        api_key = os.getenv("GROQ_API_KEY")
        model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if not api_key:
            raise ValueError(
                "GROQ_API_KEY is required when LLM_PROVIDER=groq. "
                "Set it in your .env file or switch to LLM_PROVIDER=ollama"
            )
        print(f"✅ Using Groq LLM: {model}")
        return ChatGroq(model=model, api_key=api_key, temperature=0.7)


llm = get_llm()

# ── System prompts ────────────────────────────────────────────────────────────

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
    last_msg = state["messages"][-1].content

    prompt = (
        "You are a banking intent classifier. "
        "Read the customer message below and reply with ONLY one word from this list:\n"
        "  account   → balance, statements, account info, account details\n"
        "  transfer  → send money, wire, payment, beneficiary\n"
        "  support   → problem, card blocked, complaint, help, technical issue\n"
        "  fraud     → fraud detection, suspicious activity, IBAN analysis, anomaly check, "
        "AML, blanchiment, export transactions, analyse fraude, vérifier fraude, "
        "detect fraud, check fraud, weird behaviour, suspicious transactions, "
        "transaction analysis, risque, risk analysis, IBAN_\n"
        "  fallback  → anything else\n\n"
        f"Customer message: {last_msg}\n"
        "Reply with one word only."
    )

    response = llm.invoke(prompt)
    intent   = response.content.strip().lower().split()[0]

    if intent not in ("account", "transfer", "support", "fraud"):
        intent = "fallback"

    print(f"🧠 Detected intent: {intent} (from: '{last_msg[:80]}...')")
    return {**state, "intent": intent}


def route_to_agent(state: BankChatState) -> str:
    return {
        "account":  "account_agent",
        "transfer": "transfer_agent",
        "support":  "support_agent",
        "fraud":    "fraud_agent",
    }.get(state["intent"], "fallback")


# ── Agent nodes ───────────────────────────────────────────────────────────────

def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    system = SystemMessage(content=SYSTEM_PROMPTS[agent_key])
    messages_with_system = [system] + list(state["messages"])
    response = llm.invoke(messages_with_system)
    return {
        **state,
        "messages": [AIMessage(content=response.content)],
        "agent":    agent_key,
    }

def account_agent(state: BankChatState) -> BankChatState:
    return _run_agent(state, "account_agent")

def transfer_agent(state: BankChatState) -> BankChatState:
    return _run_agent(state, "transfer_agent")

def support_agent(state: BankChatState) -> BankChatState:
    return _run_agent(state, "support_agent")

def handle_fallback(state: BankChatState) -> BankChatState:
    return _run_agent(state, "fallback")


# ── Streaming helper ──────────────────────────────────────────────────────────

def stream_agent_response(intent: str, messages: list):
    """
    Yields (token, agent_key) tuples.
    Pour le fraud intent : appel HTTP au fraud-service (pas de streaming token par token).
    Pour les autres agents : streaming LLM natif.
    """
    if intent == "fraud":
        # Extraire l'IBAN du dernier message utilisateur
        iban = extract_iban(messages)

        # Récupérer le texte brut du dernier message pour l'envoyer au service
        last_msg = ""
        for msg in reversed(messages):
            if msg.__class__.__name__ == "HumanMessage":
                last_msg = msg.content
                break

        try:
            response = httpx.post(
                f"{FRAUD_SERVICE_URL}/analyze",
                json={
                    "message":    last_msg,
                    "iban":       iban,
                    "action":     "fraud_check",
                    "user_id":    "anonymous",
                    "session_id": "",
                    "excel_path": "",
                },
                timeout=120.0,
            )
            response.raise_for_status()
            result  = response.json()
            summary = result.get("llm_summary", result.get("summary", "Analyse de fraude terminée."))
        except httpx.TimeoutException:
            summary = "⏱️ Le service de fraude a mis trop de temps à répondre. Réessayez."
        except Exception as e:
            summary = f"❌ Erreur service de fraude : {str(e)}"

        yield summary, "fraud_agent"
        return

    # Agents classiques — streaming token par token
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