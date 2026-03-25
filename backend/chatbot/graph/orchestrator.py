import os
import httpx
from langgraph.graph import StateGraph, END
from .state import BankChatState
from .nodes import detect_intent, route_to_agent, account_agent, transfer_agent, support_agent, handle_fallback
from langchain_core.messages import AIMessage

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")


def fraud_agent(state: BankChatState) -> dict:
    """
    Fraud detection agent node.
    Delegates to the fraud-service microservice via HTTP.
    No longer imports run_fraud_agent directly.
    """
    # Extraire le dernier message utilisateur pour récupérer l'IBAN
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
            f"{FRAUD_SERVICE_URL}/analyze",
            json={
                "message":    last_user_msg,
                "user_id":    state.get("user_id", "anonymous"),
                "session_id": state.get("session_id", ""),
                "action":     "fraud_check",
                "excel_path": "",
            },
            timeout=120.0,  # analyse peut être longue (LLM + Excel)
        )
        response.raise_for_status()
        result = response.json()

        ai_response = (
            result.get("llm_summary") or
            result.get("summary") or
            result.get("error") or
            "Analyse de fraude terminée — aucun résumé généré."
        )
        return {
            "messages": [AIMessage(content=ai_response)],
            "agent":    "fraud_agent",
            "context": {
                "score_final":      result.get("score_final", 0),
                "risk_level":       result.get("risk_level", ""),
                "report_path":      result.get("report_path", ""),
                "tracfin_required": result.get("tracfin_required", False),
                "iban":             result.get("iban", ""),
            },
        }

    except httpx.TimeoutException:
        return {
            "messages": [AIMessage(content="⏱️ Le service d'analyse de fraude a mis trop de temps à répondre. Veuillez réessayer.")],
            "agent": "fraud_agent",
            "error": "timeout",
        }
    except httpx.HTTPStatusError as e:
        return {
            "messages": [AIMessage(content=f"❌ Erreur du service de fraude (HTTP {e.response.status_code}).")],
            "agent": "fraud_agent",
            "error": str(e),
        }
    except Exception as e:
        return {
            "messages": [AIMessage(content=f"❌ Erreur lors de l'analyse de fraude: {str(e)}")],
            "agent": "fraud_agent",
            "error": str(e),
        }


def create_graph():
    graph = StateGraph(BankChatState)

    graph.add_node("detect_intent",  detect_intent)
    graph.add_node("account_agent",  account_agent)
    graph.add_node("transfer_agent", transfer_agent)
    graph.add_node("support_agent",  support_agent)
    graph.add_node("fraud_agent",    fraud_agent)
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
            "fallback":       "fallback",
        }
    )

    graph.add_edge("account_agent",  END)
    graph.add_edge("transfer_agent", END)
    graph.add_edge("support_agent",  END)
    graph.add_edge("fraud_agent",    END)
    graph.add_edge("fallback",       END)

    return graph.compile()


bank_graph = create_graph()