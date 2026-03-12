"""
LangGraph node functions for the Fraud Detection Agent.
Each function takes and returns the FraudAgentState.
"""

import re
import pandas as pd
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from typing import Dict

from .state import FraudAgentState
from .loader import load_transactions, filter_by_iban, get_account_summary, validate_iban
from .rules import run_all_rules
from .scoring import compute_behavioral_score, compute_aml_score, compute_final_score, check_tracfin_required
from .report import generate_transaction_export, generate_fraud_report
from ..nodes import get_llm


# ── Lazy LLM (reuse from main graph) ─────────────────────────────────────────
_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm()
    return _llm


# ── IBAN Extraction Regex ─────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",  # Also match IBAN_FR123 format from the dataset
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Node 1: PARSE REQUEST
# ═══════════════════════════════════════════════════════════════════════════════

def parse_request(state: FraudAgentState) -> Dict:
    """
    Extract IBAN and desired action from the user's message.
    Uses LLM to understand intent if pattern matching fails.
    """
    last_message = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_message = msg.content
            break

    # 1. Extract IBAN via regex
    iban = ""
    iban_match = IBAN_PATTERN.search(last_message)
    if iban_match:
        iban = (iban_match.group(1) or iban_match.group(2) or "").replace(" ", "").upper()

    # 2. Determine action from keywords
    text_lower = last_message.lower()

    fraud_keywords = [
        "fraude", "fraud", "suspect", "anomal", "bizarre", "weird",
        "vérif", "check", "analys", "détect", "detect", "risque",
        "risk", "aml", "blanchiment", "suspicious",
    ]
    export_keywords = [
        "export", "excel", "téléchar", "download", "relevé",
        "statement", "historique", "history", "toutes les transactions",
        "all transactions", "génère", "generate",
    ]

    action = "fraud_check"  # default
    if any(kw in text_lower for kw in export_keywords):
        action = "export_transactions"
    if any(kw in text_lower for kw in fraud_keywords):
        action = "fraud_check"  # fraud keywords take priority

    # 3. If no IBAN found, ask via LLM
    if not iban:
        return {
            "iban": "",
            "action": action,
            "error": "IBAN non détecté dans votre message. Veuillez fournir un IBAN valide (ex: IBAN_FR123 ou FR7612345678901234567890).",
            "messages": [AIMessage(content=(
                "❌ Je n'ai pas pu détecter d'IBAN dans votre message.\n\n"
                "Veuillez me fournir un IBAN valide, par exemple:\n"
                "- `IBAN_FR123`\n"
                "- `FR7612345678901234567890123`\n\n"
                "Et précisez si vous souhaitez:\n"
                "1. 🔍 **Vérifier les fraudes** sur ce compte\n"
                "2. 📊 **Exporter les transactions** en Excel"
            ))],
        }

    return {
        "iban": iban,
        "action": action,
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 2: LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════

def load_data(state: FraudAgentState) -> Dict:
    """Load transactions from Excel and filter by IBAN."""
    if state.get("error"):
        return {}  # Skip if previous node had error

    iban = state["iban"]
    excel_path = state.get("excel_path", "")

    try:
        full_df = load_transactions(excel_path or None)
        filtered_df = filter_by_iban(full_df, iban)

        if filtered_df.empty:
            return {
                "transactions_raw": [],
                "transactions_count": 0,
                "account_summary": None,
                "error": f"Aucune transaction trouvée pour l'IBAN: {iban}",
                "messages": [AIMessage(content=(
                    f"❌ Aucune transaction trouvée pour l'IBAN **{iban}**.\n\n"
                    f"Vérifiez que l'IBAN est correct et que le fichier Excel contient des données pour ce compte."
                ))],
            }

        summary = get_account_summary(filtered_df)

        return {
            "transactions_raw": filtered_df.to_dict("records"),
            "transactions_count": len(filtered_df),
            "account_summary": summary,
            "error": None,
        }

    except FileNotFoundError as e:
        return {
            "transactions_raw": [],
            "transactions_count": 0,
            "error": str(e),
            "messages": [AIMessage(content=f"❌ Fichier de transactions introuvable.\n\n{str(e)}")],
        }
    except Exception as e:
        return {
            "transactions_raw": [],
            "transactions_count": 0,
            "error": f"Erreur lors du chargement: {str(e)}",
            "messages": [AIMessage(content=f"❌ Erreur: {str(e)}")],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3a: ANALYZE FRAUD
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_fraud(state: FraudAgentState) -> Dict:
    """
    Run the full fraud detection pipeline:
    1. Rule-based checks (13 rules)
    2. Behavioral scoring (signal points)
    3. AML scoring
    4. Composite final score
    5. TRACFIN check
    """
    if state.get("error") or not state.get("transactions_raw"):
        return {}

    df = pd.DataFrame(state["transactions_raw"])

    # Parse timestamp back
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # 1. Run all rules
    rule_results = run_all_rules(df)

    # 2. Behavioral scoring
    score_behavioral, behavioral_signals = compute_behavioral_score(df)

    # 3. AML scoring
    score_aml = compute_aml_score(rule_results)

    # 4. Final score
    score_final, risk_level = compute_final_score(score_behavioral, score_aml)

    # 5. TRACFIN check
    tracfin = check_tracfin_required(rule_results, df)

    # 6. Generate report
    report_path = generate_fraud_report(
        df=df,
        iban=state["iban"],
        rule_results=rule_results,
        behavioral_signals=behavioral_signals,
        score_behavioral=score_behavioral,
        score_aml=score_aml,
        score_final=score_final,
        risk_level=risk_level,
        tracfin_required=tracfin,
    )

    return {
        "fraud_results": rule_results,
        "score_behavioral": score_behavioral,
        "score_aml": score_aml,
        "score_final": score_final,
        "risk_level": risk_level,
        "tracfin_required": tracfin,
        "report_path": report_path,
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3b: EXPORT TRANSACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def export_transactions(state: FraudAgentState) -> Dict:
    """Export all transactions for the IBAN to Excel."""
    if state.get("error") or not state.get("transactions_raw"):
        return {}

    df = pd.DataFrame(state["transactions_raw"])

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    report_path = generate_transaction_export(df, state["iban"])

    return {
        "report_path": report_path,
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 4: GENERATE LLM SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def generate_summary(state: FraudAgentState) -> Dict:
    """
    Use LLM to generate a natural-language fraud analysis summary.
    This makes the report human-readable and actionable.
    """
    if state.get("error"):
        return {"llm_summary": "", "messages": []}

    action = state.get("action", "fraud_check")

    if action == "export_transactions":
        summary_text = (
            f"✅ **Export terminé**\n\n"
            f"📊 **IBAN:** {state['iban']}\n"
            f"📝 **Transactions:** {state.get('transactions_count', 0)}\n"
            f"📁 **Fichier:** `{state.get('report_path', 'N/A')}`\n\n"
            f"Le fichier Excel contient toutes les transactions associées à ce compte."
        )
        return {
            "llm_summary": summary_text,
            "messages": [AIMessage(content=summary_text)],
        }

    # ── Fraud analysis summary via LLM ──
    risk_emoji = {
        "APPROVED": "🟢", "REVIEW": "🟡", "HOLD": "🟠", "BLOCK": "🔴"
    }.get(state.get("risk_level", ""), "⚪")

    triggered_rules = [r for r in state.get("fraud_results", []) if r.get("triggered")]
    rules_text = "\n".join(
        f"  - [{r['severity']}] {r['rule']}: {r['details']} (score: {r['score']})"
        for r in triggered_rules
    ) if triggered_rules else "  Aucune règle déclenchée."

    summary_info = state.get("account_summary", {})

    prompt = (
        "Tu es un analyste fraude bancaire expert. Génère un rapport concis et professionnel "
        "en français basé sur les résultats d'analyse suivants.\n\n"
        f"IBAN analysé: {state['iban']}\n"
        f"Nombre de transactions: {state.get('transactions_count', 0)}\n"
        f"Montant total: €{summary_info.get('total_amount', 0)}\n"
        f"Période: {summary_info.get('date_range', 'N/A')}\n"
        f"Pays impliqués: {summary_info.get('countries', [])}\n\n"
        f"Score comportemental: {state.get('score_behavioral', 0)}/130\n"
        f"Score AML: {state.get('score_aml', 0)}/100\n"
        f"Score final: {state.get('score_final', 0)}/100\n"
        f"Niveau de risque: {state.get('risk_level', 'N/A')} {risk_emoji}\n"
        f"TRACFIN requis: {'OUI' if state.get('tracfin_required') else 'NON'}\n\n"
        f"Règles déclenchées:\n{rules_text}\n\n"
        "Génère un résumé structuré avec:\n"
        "1. Verdict global\n"
        "2. Alertes principales\n"
        "3. Recommandations d'action\n"
        "Sois précis et utilise des emojis pour la lisibilité."
    )

    try:
        llm = _get_llm()
        response = llm.invoke(prompt)
        llm_text = response.content
    except Exception as e:
        llm_text = f"(Résumé LLM indisponible: {e})"

    # Build the final formatted response
    header = (
        f"# 🏦 Rapport d'Analyse de Fraude\n\n"
        f"**IBAN:** `{state['iban']}`\n"
        f"**Transactions analysées:** {state.get('transactions_count', 0)}\n"
        f"**Score final:** {state.get('score_final', 0)}/100 {risk_emoji} **{state.get('risk_level', '')}**\n"
        f"**TRACFIN:** {'⚠️ DÉCLARATION REQUISE' if state.get('tracfin_required') else '✅ Non requis'}\n"
        f"**Rapport Excel:** `{state.get('report_path', 'N/A')}`\n\n"
        f"---\n\n"
        f"{llm_text}"
    )

    return {
        "llm_summary": header,
        "messages": [AIMessage(content=header)],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTING FUNCTION
# ════════════════════════════════════════════════════════════════════════════��══

def route_fraud_action(state: FraudAgentState) -> str:
    """Route to the correct analysis node based on action."""
    if state.get("error"):
        return "generate_summary"
    if state.get("action") == "export_transactions":
        return "export_transactions"
    return "analyze_fraud"