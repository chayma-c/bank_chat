import os
import httpx
from langgraph.graph import StateGraph, END
from .state import BankChatState
from .nodes import (
    detect_intent, route_to_agent, account_agent, 
    transfer_agent, support_agent, fraud_agent, handle_fallback
)
from langchain_core.messages import AIMessage

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")


def text_to_sql_agent(state: BankChatState) -> dict:
    """
    Text-to-SQL agent node.
    Delegates to the text-to-sql-service microservice via HTTP.
    """
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
            f"{os.getenv('TEXT2SQL_SERVICE_URL', 'http://text-to-sql-service:8002')}/query",
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
        return {
            "messages": [AIMessage(content=f"❌ Erreur service SQL : {str(e)}")],
            "agent": "text2sql_agent",
            "error": str(e),
        }

def create_graph():
    graph = StateGraph(BankChatState)

    graph.add_node("detect_intent",  detect_intent)
    graph.add_node("account_agent",  account_agent)
    graph.add_node("transfer_agent", transfer_agent)
    graph.add_node("support_agent",  support_agent)
    graph.add_node("fraud_agent",    fraud_agent)
    graph.add_node("text_to_sql_agent", text_to_sql_agent)
    graph.add_node("fallback",       handle_fallback)

    graph.set_entry_point("detect_intent")

    graph.add_conditional_edges(
        "detect_intent",
        route_to_agent,
        {
            "account_agent":  "account_agent",
            "transfer_agent": "transfer_agent",
            "support_agent":  "support_agent",
            "fraud_agent":    "fraud_agent",
            "text_to_sql_agent": "text_to_sql_agent",
            "fallback":       "fallback",
        }
    )

    graph.add_edge("account_agent",  END)
    graph.add_edge("transfer_agent", END)
    graph.add_edge("support_agent",  END)
    graph.add_edge("fraud_agent",    END)
    graph.add_edge("text_to_sql_agent", END)
    graph.add_edge("fallback",       END)

    return graph.compile()


bank_graph = create_graph()