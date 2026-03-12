from langgraph.graph import StateGraph, END
from .state import BankChatState
from .nodes import detect_intent, route_to_agent, account_agent, transfer_agent, support_agent, handle_fallback
from .fraud.graph import run_fraud_agent
from langchain_core.messages import AIMessage


def fraud_agent(state: BankChatState) -> dict:
    """
    Fraud detection agent node.
    Delegates to the fraud sub-graph and returns the result
    back into the main BankChatState format.
    """
    try:
        result = run_fraud_agent(
            messages=state["messages"],
            user_id=state.get("user_id", "anonymous"),
            session_id=state.get("session_id", ""),
        )

        ai_response = result.get("llm_summary", "Analyse terminée.")
        return {
            "messages": [AIMessage(content=ai_response)],
            "agent": "fraud_agent",
            "context": {
                "score_final": result.get("score_final", 0),
                "risk_level": result.get("risk_level", ""),
                "report_path": result.get("report_path", ""),
                "tracfin_required": result.get("tracfin_required", False),
            },
        }

    except Exception as e:
        return {
            "messages": [AIMessage(content=f"❌ Erreur lors de l'analyse de fraude: {str(e)}")],
            "agent": "fraud_agent",
            "error": str(e),
        }


def create_graph():
    graph = StateGraph(BankChatState)

    # Nodes — each has its own specialized system prompt
    graph.add_node("detect_intent",  detect_intent)
    graph.add_node("account_agent",  account_agent)
    graph.add_node("transfer_agent", transfer_agent)
    graph.add_node("support_agent",  support_agent)
    graph.add_node("fraud_agent",    fraud_agent)       # ✨ NEW
    graph.add_node("fallback",       handle_fallback)

    # Entry point
    graph.set_entry_point("detect_intent")

    # Routing conditionnel (updated with fraud)
    graph.add_conditional_edges(
        "detect_intent",
        route_to_agent,
        {
            "account_agent":  "account_agent",
            "transfer_agent": "transfer_agent",
            "support_agent":  "support_agent",
            "fraud_agent":    "fraud_agent",            # ✨ NEW
            "fallback":       "fallback",
        }
    )

    graph.add_edge("account_agent",  END)
    graph.add_edge("transfer_agent", END)
    graph.add_edge("support_agent",  END)
    graph.add_edge("fraud_agent",    END)                # ✨ NEW
    graph.add_edge("fallback",       END)

    return graph.compile()


# Global instance
bank_graph = create_graph()