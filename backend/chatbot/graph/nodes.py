import os
from langchain_groq import ChatGroq
from .state import BankChatState

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=os.getenv("GROQ_API_KEY"),
)

def detect_intent(state: BankChatState) -> BankChatState:
    """Détecte l'intention de l'utilisateur"""
    last_msg = state["messages"][-1].content

    prompt = f"""
    Analyse ce message bancaire et retourne UNIQUEMENT un mot parmi :
    account, transfer, support, fallback
    
    Message: {last_msg}
    """
    response = llm.invoke(prompt)
    intent = response.content.strip().lower()

    return {**state, "intent": intent}

def route_to_agent(state: BankChatState) -> str:
    """Retourne le nom du noeud suivant"""
    intent_map = {
        "account":  "account_agent",
        "transfer": "transfer_agent",
        "support":  "support_agent",
    }
    return intent_map.get(state["intent"], "fallback")

def handle_fallback(state: BankChatState) -> BankChatState:
    """Réponse générique temporaire"""
    from langchain_core.messages import AIMessage
    response = llm.invoke(state["messages"])
    return {
        **state,
        "messages": [AIMessage(content=response.content)],
        "agent": "fallback"
    }