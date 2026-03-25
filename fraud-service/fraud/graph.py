"""
Fraud Detection Agent — LangGraph sub-graph.

Graph structure:
    parse_request → load_data → [route] → analyze_fraud  → generate_summary → END
                                        → export_transactions → generate_summary → END

Integrates into the main BankChat orchestrator as a new agent node.
"""

from langgraph.graph import StateGraph, END

from .state import FraudAgentState
from .nodes import (
    parse_request,
    load_data,
    analyze_fraud,
    export_transactions,
    generate_summary,
    route_fraud_action,
)


def create_fraud_graph():
    """Build and compile the fraud detection sub-graph."""
    graph = StateGraph(FraudAgentState)

    # ── Add nodes ─────────────────────────────────────────────────
    graph.add_node("parse_request",       parse_request)
    graph.add_node("load_data",           load_data)
    graph.add_node("analyze_fraud",       analyze_fraud)
    graph.add_node("export_transactions", export_transactions)
    graph.add_node("generate_summary",    generate_summary)

    # ── Entry point ───────────────────────────────────────────────
    graph.set_entry_point("parse_request")

    # ── Edges ─────────────────────────────────────────────────────
    graph.add_edge("parse_request", "load_data")

    # Conditional routing after data is loaded
    graph.add_conditional_edges(
        "load_data",
        route_fraud_action,
        {
            "analyze_fraud":       "analyze_fraud",
            "export_transactions": "export_transactions",
            "generate_summary":    "generate_summary",  # error path
        },
    )

    graph.add_edge("analyze_fraud",       "generate_summary")
    graph.add_edge("export_transactions", "generate_summary")
    graph.add_edge("generate_summary",    END)

    return graph.compile()


# ── Global instance ───────────────────────────────────────────────────────────
fraud_graph = create_fraud_graph()


def run_fraud_agent(
    messages: list,
    user_id: str = "anonymous",
    session_id: str = "",
    excel_path: str = "",
) -> dict:
    """
    Convenience function to run the fraud agent.
    Can be called from the main orchestrator or directly.

    Args:
        messages: List of LangChain messages (with user's IBAN request)
        user_id: User identifier
        session_id: Conversation session ID
        excel_path: Optional path to Excel file

    Returns:
        Final FraudAgentState dict with all results
    """
    initial_state: FraudAgentState = {
        "messages": messages,
        "user_id": user_id,
        "session_id": session_id,
        "iban": "",
        "action": "",
        "excel_path": excel_path,
        "transactions_raw": [],
        "transactions_count": 0,
        "account_summary": None,
        "fraud_results": [],
        "score_behavioral": 0,
        "score_aml": 0,
        "score_final": 0,
        "risk_level": "",
        "tracfin_required": False,
        "report_path": None,
        "llm_summary": "",
        "error": None,
    }

    result = fraud_graph.invoke(initial_state)
    return result