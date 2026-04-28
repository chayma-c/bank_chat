import os
import httpx
import logging
from langgraph.graph import StateGraph, END
from .state import BankChatState
from .nodes import (
    detect_intent, route_to_agent,
    account_agent, transfer_agent, support_agent,
    fraud_agent, handle_fallback, mail_agent,
    text_to_sql_agent,
)
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")
MAIL_SERVICE_URL  = os.getenv("MAIL_SERVICE_URL",  "http://mail-service:8002")
ALERT_EMAIL       = os.getenv("ALERT_EMAIL",        "compliance@yourbank.com")




def should_send_mail(state: BankChatState) -> str:
    """
    Routeur conditionnel après fraud_agent.

    Règle : si le context contient un 'iban' non vide (mis par fraud_agent
    en path ANALYZE) → on passe par mail_agent qui décidera déterministiquement.
    Sinon (path TALK ou erreur) → END directement.

    On NE teste PAS score_final ici car 0 est falsy mais valide.
    On teste uniquement la présence de 'iban' qui prouve qu'une analyse a été faite.
    """
    context = state.get("context", {})
    iban    = context.get("iban", "")
    if iban:
        logger.info(f"[should_send_mail] IBAN={iban} found in context → routing to mail_agent")
        return "mail_agent"
    logger.info("[should_send_mail] No IBAN in context (TALK path) → END")
    return "end"


def create_graph():
    graph = StateGraph(BankChatState)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    graph.add_node("detect_intent",     detect_intent)
    graph.add_node("account_agent",     account_agent)
    graph.add_node("transfer_agent",    transfer_agent)
    graph.add_node("support_agent",     support_agent)
    graph.add_node("fraud_agent",       fraud_agent)
    graph.add_node("text_to_sql_agent", text_to_sql_agent)
    graph.add_node("mail_agent",        mail_agent)
    graph.add_node("fallback",          handle_fallback)

    # ── Entry point ────────────────────────────────────────────────────────────
    graph.set_entry_point("detect_intent")

    # ── Routing après intent detection ────────────────────────────────────────
    graph.add_conditional_edges(
        "detect_intent",
        route_to_agent,
        {
            "account_agent":     "account_agent",
            "transfer_agent":    "transfer_agent",
            "support_agent":     "support_agent",
            "fraud_agent":       "fraud_agent",
            "text_to_sql_agent": "text_to_sql_agent",
            "fallback":          "fallback",
        }
    )

    # ── Après fraud_agent : routing conditionnel vers mail_agent ──────────────
    # CORRECTION : add_conditional_edges avec dict de mapping string→node
    # should_send_mail retourne "mail_agent" ou "end" (chaîne, pas constante END)
    graph.add_conditional_edges(
        "fraud_agent",
        should_send_mail,
        {
            "mail_agent": "mail_agent",
            "end":        END,
        }
    )

    # ── Edges terminaux ────────────────────────────────────────────────────────
    graph.add_edge("account_agent",     END)
    graph.add_edge("transfer_agent",    END)
    graph.add_edge("support_agent",     END)
    graph.add_edge("mail_agent",        END)
    graph.add_edge("text_to_sql_agent", END)
    graph.add_edge("fallback",          END)

    return graph.compile()


bank_graph = create_graph()