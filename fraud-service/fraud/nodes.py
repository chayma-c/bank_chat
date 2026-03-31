"""
LangGraph node functions for the Fraud Detection Agent.
Self-contained — does NOT import from parent packages.
"""

import os
import re
import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from typing import Dict

from .state import FraudAgentState
from .loader import load_transactions, filter_by_iban, get_account_summary
from .rules import run_all_rules
from .scoring import (
    compute_behavioral_score,
    compute_aml_score,
    compute_final_score,
    check_tracfin_required,
)
from .report import generate_transaction_export, generate_fraud_report


# ── LLM autonome ─────────────────────────────────────────────────────────────

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.getenv("OLLAMA_MODEL", "llama3.2")
        print(f"✅ [fraud-service] Ollama: {model} @ {base_url}")
        return ChatOllama(base_url=base_url, model=model, temperature=0.7)
    else:
        api_key = os.getenv("GROQ_API_KEY", "")
        model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if not api_key:
            raise ValueError("GROQ_API_KEY manquant dans .env")
        print(f"✅ [fraud-service] Groq: {model}")
        return ChatGroq(model=model, api_key=api_key, temperature=0.7)


_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm()
    return _llm


# ── IBAN regex ────────────────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Node 1: PARSE REQUEST
# ═══════════════════════════════════════════════════════════════════════════════

def parse_request(state: FraudAgentState) -> Dict:
    last_message = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_message = msg.content
            break

    # Extraire IBAN
    iban = ""
    iban_match = IBAN_PATTERN.search(last_message)
    if iban_match:
        iban = (iban_match.group(1) or iban_match.group(2) or "").replace(" ", "").upper()

    # Détecter l'action
    text_lower = last_message.lower()
    action = "fraud_check"
    if any(kw in text_lower for kw in ["export", "excel", "téléchar", "download",
                                        "relevé", "statement", "historique", "history",
                                        "toutes les transactions", "all transactions"]):
        action = "export_transactions"
    if any(kw in text_lower for kw in ["fraude", "fraud", "suspect", "anomal",
                                        "vérif", "check", "analys", "détect",
                                        "detect", "risque", "risk", "aml", "blanchiment"]):
        action = "fraud_check"

    if not iban:
        error_msg = (
            "❌ Je n'ai pas pu détecter d'IBAN dans votre message.\n\n"
            "Veuillez fournir un IBAN valide, par exemple :\n"
            "- `IBAN_FR123`\n"
            "- `FR7612345678901234567890123`"
        )
        return {
            "iban":    "",
            "action":  action,
            "error":   "IBAN non détecté.",
            "llm_summary": error_msg,
            "messages": [AIMessage(content=error_msg)],
        }

    print(f"[fraud] IBAN extrait: {iban}, action: {action}")
    return {"iban": iban, "action": action, "error": None}


# ═══════════════════════════════════════════════════════════════════════════════
# Node 2: LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════

def load_data(state: FraudAgentState) -> Dict:
    if state.get("error"):
        return {}

    iban       = state["iban"]
    excel_path = state.get("excel_path") or ""

    try:
        full_df     = load_transactions(excel_path or None)
        filtered_df = filter_by_iban(full_df, iban)

        if filtered_df.empty:
            # Lister les IBANs disponibles pour aider au debug
            available = full_df.iloc[:, 0].unique()[:5].tolist() if not full_df.empty else []
            error_msg = (
                f"❌ Aucune transaction trouvée pour l'IBAN **{iban}**.\n\n"
                f"IBANs disponibles dans le fichier (exemples) : {available}\n\n"
                "Vérifiez que l'IBAN est correct."
            )
            return {
                "transactions_raw":   [],
                "transactions_count": 0,
                "account_summary":    None,
                "error":              f"Aucune transaction pour {iban}",
                "llm_summary":        error_msg,
                "messages":           [AIMessage(content=error_msg)],
            }

        summary = get_account_summary(filtered_df)
        print(f"[fraud] {len(filtered_df)} transactions chargées pour {iban}")

        return {
            "transactions_raw":   filtered_df.to_dict("records"),
            "transactions_count": len(filtered_df),
            "account_summary":    summary,
            "error":              None,
        }

    except FileNotFoundError as e:
        error_msg = f"❌ Fichier de transactions introuvable : {str(e)}\n\nVérifiez que `/app/data/transactions.csv` existe."
        return {
            "transactions_raw":   [],
            "transactions_count": 0,
            "error":              str(e),
            "llm_summary":        error_msg,
            "messages":           [AIMessage(content=error_msg)],
        }
    except Exception as e:
        error_msg = f"❌ Erreur chargement données : {str(e)}"
        return {
            "transactions_raw":   [],
            "transactions_count": 0,
            "error":              str(e),
            "llm_summary":        error_msg,
            "messages":           [AIMessage(content=error_msg)],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3a: ANALYZE FRAUD
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_fraud(state: FraudAgentState) -> Dict:
    if state.get("error") or not state.get("transactions_raw"):
        return {}

    df = pd.DataFrame(state["transactions_raw"])
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    rule_results                         = run_all_rules(df)
    score_behavioral, behavioral_signals = compute_behavioral_score(df)
    score_aml                            = compute_aml_score(rule_results)
    score_final, risk_level              = compute_final_score(score_behavioral, score_aml)
    tracfin                              = check_tracfin_required(rule_results, df)

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

    print(f"[fraud] Analyse terminée — score={score_final}, risk={risk_level}")

    return {
        "fraud_results":    rule_results,
        "score_behavioral": score_behavioral,
        "score_aml":        score_aml,
        "score_final":      score_final,
        "risk_level":       risk_level,
        "tracfin_required": tracfin,
        "report_path":      report_path,
        "error":            None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3b: EXPORT TRANSACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def export_transactions(state: FraudAgentState) -> Dict:
    if state.get("error") or not state.get("transactions_raw"):
        return {}

    df = pd.DataFrame(state["transactions_raw"])
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    report_path = generate_transaction_export(df, state["iban"])
    return {"report_path": report_path, "error": None}


# ═══════════════════════════════════════════════════════════════════════════════
# Node 4: GENERATE LLM SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def generate_summary(state: FraudAgentState) -> Dict:
    # ── Cas erreur : retourner le message d'erreur déjà construit ──────────
    if state.get("error"):
        # llm_summary a déjà été rempli par load_data ou parse_request
        existing = state.get("llm_summary", "")
        if existing:
            return {"llm_summary": existing, "messages": [AIMessage(content=existing)]}
        # Fallback si vraiment rien
        msg = f"❌ Erreur : {state['error']}"
        return {"llm_summary": msg, "messages": [AIMessage(content=msg)]}

    action = state.get("action", "fraud_check")

    # ── Export terminé ────────────────────────────────────────────────────
    if action == "export_transactions":
        summary_text = (
            f"✅ **Export terminé**\n\n"
            f"📊 **IBAN:** `{state['iban']}`\n"
            f"📝 **Transactions:** {state.get('transactions_count', 0)}\n"
            f"📁 **Fichier:** `{state.get('report_path', 'N/A')}`\n\n"
            "Le fichier Excel contient toutes les transactions du compte."
        )
        return {"llm_summary": summary_text, "messages": [AIMessage(content=summary_text)]}

    # ── Résumé LLM fraud ──────────────────────────────────────────────────
    risk_emoji = {
        "APPROVED": "🟢", "REVIEW": "🟡", "BLOCK": "🔴"
    }.get(state.get("risk_level", ""), "⚪")

    triggered_rules = [r for r in state.get("fraud_results", []) if r.get("triggered")]
    rules_text = "\n".join(
        f"  - [{r['severity']}] {r['rule']}: {r['details']} (+{r.get('points', 0)} pts)"
        for r in triggered_rules
    ) if triggered_rules else "  Aucune règle déclenchée."

    info = state.get("account_summary", {}) or {}

    prompt = (
        "Tu es un analyste fraude bancaire expert. "
        "Génère un rapport concis et professionnel en français.\n\n"
        f"IBAN: {state['iban']}\n"
        f"Transactions: {state.get('transactions_count', 0)}\n"
        f"Montant total: {info.get('total_amount', 0)}\n"
        f"Période: {info.get('date_range', 'N/A')}\n"
        f"Types de transaction: {info.get('transaction_types', {})}\n\n"
        f"Score comportemental: {state.get('score_behavioral', 0)}/100\n"
        f"Score AML (règles): {state.get('score_aml', 0)}/100\n"
        f"Score final: {state.get('score_final', 0)}/100\n"
        f"Niveau de risque: {state.get('risk_level', 'N/A')} {risk_emoji}\n"
        f"  (Seuils: <30 APPROVED · 30–59 REVIEW · ≥60 BLOCK)\n"
        f"TRACFIN: {'OUI' if state.get('tracfin_required') else 'NON'}\n\n"
        f"Règles déclenchées:\n{rules_text}\n\n"
        "Génère un résumé avec : 1. Verdict global  2. Alertes  3. Recommandations"
    )

    try:
        llm      = _get_llm()
        response = llm.invoke(prompt)
        llm_text = response.content
    except Exception as e:
        llm_text = f"(Résumé LLM indisponible: {e})"

    header = (
        f"# 🏦 Rapport d'Analyse de Fraude\n\n"
        f"**IBAN:** `{state['iban']}`\n"
        f"**Transactions:** {state.get('transactions_count', 0)}\n"
        f"**Score final:** {state.get('score_final', 0)}/100 {risk_emoji} "
        f"**{state.get('risk_level', '')}**\n"
        f"**TRACFIN:** {'⚠️ DÉCLARATION REQUISE' if state.get('tracfin_required') else '✅ Non requis'}\n"
        f"**Rapport Excel:** `{state.get('report_path', 'N/A')}`\n\n"
        f"---\n\n{llm_text}"
    )

    return {"llm_summary": header, "messages": [AIMessage(content=header)]}


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTING
# ═══════════════════════════════════════════════════════════════════════════════

def route_fraud_action(state: FraudAgentState) -> str:
    if state.get("error"):
        return "generate_summary"
    if state.get("action") == "export_transactions":
        return "export_transactions"
    return "analyze_fraud"