from langgraph.graph import StateGraph, END
from .state import BankChatState
from .nodes import detect_intent, route_to_agent, handle_fallback

def create_graph():
    graph = StateGraph(BankChatState)

    # Noeuds
    graph.add_node("detect_intent",  detect_intent)
    graph.add_node("account_agent",  handle_fallback)  # à remplacer
    graph.add_node("transfer_agent", handle_fallback)  # à remplacer
    graph.add_node("support_agent",  handle_fallback)  # à remplacer
    graph.add_node("fallback",       handle_fallback)

    # Point d'entrée
    graph.set_entry_point("detect_intent")

    # Routing conditionnel
    graph.add_conditional_edges(
        "detect_intent",
        route_to_agent,              # fonction qui retourne le nom du noeud suivant
        {
            "account_agent":  "account_agent",
            "transfer_agent": "transfer_agent",
            "support_agent":  "support_agent",
            "fallback":       "fallback",
        }
    )

    graph.add_edge("account_agent",  END)
    graph.add_edge("transfer_agent", END)
    graph.add_edge("support_agent",  END)
    graph.add_edge("fallback",       END)

    return graph.compile()

# Instance globale
bank_graph = create_graph()