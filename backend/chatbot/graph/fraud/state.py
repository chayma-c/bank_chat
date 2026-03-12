"""
State definition for the Fraud Detection Agent.
Follows the same TypedDict pattern as the main BankChatState.
"""

from typing import TypedDict, Annotated, List, Optional
from langchain_core.messages import BaseMessage
import operator


class FraudAnalysisResult(TypedDict):
    """Result of a single fraud rule check."""
    rule_name: str
    triggered: bool
    score: float
    details: str
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"


class TransactionSummary(TypedDict):
    """Summary statistics of an account's transactions."""
    total_transactions: int
    total_amount: float
    avg_amount: float
    max_amount: float
    min_amount: float
    currencies: List[str]
    countries: List[str]
    date_range: str
    transaction_types: dict


class FraudAgentState(TypedDict):
    """
    LangGraph state for the Fraud Detection Agent.

    Flow:
        parse_request → load_data → [route] → analyze_fraud / export_transactions → generate_report → summarize
    """
    # ── Conversation ──────────────────────────────────────────────
    messages: Annotated[List[BaseMessage], operator.add]
    user_id: str
    session_id: str

    # ── Input ─────────────────────────────────────────────────────
    iban: str                                    # IBAN extracted from user prompt
    action: str                                  # "fraud_check" | "export_transactions"
    excel_path: str                              # path to the Excel file

    # ── Data ──────────────────────────────────────────────────────
    transactions_raw: list                       # raw rows as list of dicts
    transactions_count: int
    account_summary: Optional[TransactionSummary]

    # ── Fraud Analysis ────────────────────────────────────────────
    fraud_results: List[FraudAnalysisResult]     # individual rule results
    score_behavioral: int                        # 0-130 pts (signals)
    score_aml: int                               # 0-100 (AML rules)
    score_final: int                             # min(100, max(behavioral, aml))
    risk_level: str                              # "APPROVED" / "REVIEW" / "HOLD" / "BLOCK"
    tracfin_required: bool                       # whether TRACFIN declaration is needed

    # ── Output ────────────────────────────────────────────────────
    report_path: Optional[str]                   # path to generated Excel report
    llm_summary: str                             # LLM-generated natural language summary
    error: Optional[str]