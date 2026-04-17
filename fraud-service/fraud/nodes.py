"""
nodes.py — Nœuds du graphe LangGraph pour la détection de fraude.

Modification principale :
  - analyze_fraud() ouvre une session DB et appelle rule_engine.run_rules_from_db()
    au lieu de rules.run_all_rules() (qui était statique).
  - Les règles actives sont lues depuis la table fraud_rules à chaque analyse.
  - Ajouter, modifier ou désactiver une règle en DB est immédiatement pris en compte.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_groq import ChatGroq

from .database        import SessionLocal
from .loader          import filter_by_iban, find_transaction_file, get_account_summary, load_transactions
from .output_reports  import route_fraud_output
from .rule_engine     import run_rules_from_db          # ← dynamique (DB)
from .scoring         import (
    compute_aml_score,
    compute_behavioral_score,
    compute_final_score,
    check_tracfin_required,
)
from .state import FraudAgentState

logger = logging.getLogger(__name__)

# ── LLM ──────────────────────────────────────────────────────────────────────
_llm: Any = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = ChatGroq(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0.1,
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
        )
    return _llm


# ── Helpers ───────────────────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)


def _extract_iban(text: str) -> str:
    match = IBAN_PATTERN.search(text or "")
    if match:
        return (match.group(1) or match.group(2) or "").replace(" ", "").upper()
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Nœud 1 — parse_request
# ══════════════════════════════════════════════════════════════════════════════

def parse_request(state: FraudAgentState) -> dict:
    """Extrait l'IBAN et l'action depuis le dernier message utilisateur."""
    last_msg = state["messages"][-1] if state["messages"] else None
    text = last_msg.content if last_msg else ""

    iban = _extract_iban(text)

    action = "fraud_check"
    text_lower = text.lower()
    if any(w in text_lower for w in ("export", "télécharge", "download", "exporter")):
        action = "export_transactions"

    logger.info(f"[parse_request] IBAN={iban!r}  action={action!r}")

    if not iban:
        return {
            "iban":   "",
            "action": action,
            "error":  "Aucun IBAN trouvé dans le message. Veuillez fournir un IBAN valide.",
        }

    return {"iban": iban, "action": action, "error": None}


# ══════════════════════════════════════════════════════════════════════════════
# Nœud 2 — load_data
# ══════════════════════════════════════════════════════════════════════════════

def load_data(state: FraudAgentState) -> dict:
    """Charge et filtre les transactions pour l'IBAN extrait."""
    if state.get("error"):
        return {}

    iban       = state["iban"]
    excel_path = state.get("excel_path", "")

    try:
        df_all = load_transactions(excel_path or None)
        df     = filter_by_iban(df_all, iban)

        if df.empty:
            return {
                "transactions_raw":   [],
                "transactions_count": 0,
                "account_summary":    None,
                "error": f"Aucune transaction trouvée pour l'IBAN {iban}.",
            }

        summary = get_account_summary(df)
        rows    = df.to_dict("records")

        logger.info(f"[load_data] {len(rows)} transactions pour {iban}")
        return {
            "transactions_raw":   rows,
            "transactions_count": len(rows),
            "account_summary":    summary,
            "error":              None,
        }

    except FileNotFoundError as exc:
        return {"error": str(exc), "transactions_raw": [], "transactions_count": 0}
    except Exception as exc:
        logger.exception("[load_data] Unexpected error")
        return {"error": f"Erreur chargement données : {exc}",
                "transactions_raw": [], "transactions_count": 0}


# ══════════════════════════════════════════════════════════════════════════════
# Routeur conditionnel
# ══════════════════════════════════════════════════════════════════════════════

def route_fraud_action(state: FraudAgentState) -> str:
    if state.get("error"):
        return "generate_summary"
    if state.get("action") == "export_transactions":
        return "export_transactions"
    return "analyze_fraud"


# ══════════════════════════════════════════════════════════════════════════════
# Nœud 3a — analyze_fraud  ← MODIFIÉ : lecture des règles depuis DB
# ══════════════════════════════════════════════════════════════════════════════

def analyze_fraud(state: FraudAgentState) -> dict:
    """
    Analyse de fraude dynamique :
      1. Ouvre une session DB
      2. Charge les règles ACTIVES depuis fraud_rules
      3. Évalue chaque règle sur le DataFrame de transactions
      4. Calcule les scores comportemental, AML et final
      5. Génère le rapport Excel
    """
    if state.get("error"):
        return {}

    import pandas as pd
    df = pd.DataFrame(state.get("transactions_raw", []))

    if df.empty:
        return {
            "fraud_results":    [],
            "score_behavioral": 0,
            "score_aml":        0,
            "score_final":      0,
            "risk_level":       "APPROVED",
            "tracfin_required": False,
            "error":            "Aucune donnée à analyser.",
        }

    # ── Ouvrir la session DB pour lire les règles ─────────────────────────
    db = SessionLocal()
    try:
        # ── Évaluation des règles (dynamique depuis DB) ───────────────────
        rule_results = run_rules_from_db(df, db)
        logger.info(f"[analyze_fraud] {len(rule_results)} règles évaluées, "
                    f"{sum(1 for r in rule_results if r['triggered'])} déclenchées")

        # ── Score comportemental (domaines BEHAVIORAL/VELOCITY/GEO/LIMIT) ─
        score_behavioral, behavioral_signals = compute_behavioral_score(df, db=db)

    finally:
        db.close()

    # ── Score AML (toutes règles déclenchées) ─────────────────────────────
    score_aml = compute_aml_score(rule_results)

    # ── Score final ───────────────────────────────────────────────────────
    score_final, risk_level = compute_final_score(score_behavioral, score_aml)

    # ── TRACFIN ───────────────────────────────────────────────────────────
    tracfin = check_tracfin_required(rule_results, df)

    logger.info(f"[analyze_fraud] Score comportemental={score_behavioral} "
                f"AML={score_aml} Final={score_final} Niveau={risk_level} "
                f"TRACFIN={tracfin}")

    # ── Rapport Excel ─────────────────────────────────────────────────────
    iban        = state["iban"]
    output_data = route_fraud_output(
        df=df,
        iban=iban,
        rule_results=rule_results,
        behavioral_signals=behavioral_signals,
        score_behavioral=score_behavioral,
        score_aml=score_aml,
        score_final=score_final,
        risk_level=risk_level,
        tracfin_required=tracfin,
    )

    return {
        "fraud_results":    rule_results,
        "score_behavioral": score_behavioral,
        "score_aml":        score_aml,
        "score_final":      score_final,
        "risk_level":       risk_level,
        "tracfin_required": tracfin,
        "report_path":      output_data.get("local_path"),
        "download_url":     output_data.get("download_url"),
        "sheet_url":        output_data.get("sheet_url"),
        "drive_url":        output_data.get("drive_url"),
        "output_errors":    output_data.get("errors", []),
        "error":            None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Nœud 3b — export_transactions
# ══════════════════════════════════════════════════════════════════════════════

def export_transactions(state: FraudAgentState) -> dict:
    """Exporte toutes les transactions de l'IBAN en Excel sans analyse."""
    if state.get("error"):
        return {}

    import pandas as pd
    from .report import generate_transaction_export

    df   = pd.DataFrame(state.get("transactions_raw", []))
    iban = state["iban"]

    if df.empty:
        return {"error": "Aucune transaction à exporter."}

    try:
        report_path = generate_transaction_export(df, iban)
        return {"report_path": report_path, "error": None}
    except Exception as exc:
        logger.exception("[export_transactions] Error")
        return {"error": f"Erreur export : {exc}"}


# ══════════════════════════════════════════════════════════════════════════════
# Nœud 4 — generate_summary
# ══════════════════════════════════════════════════════════════════════════════

def generate_summary(state: FraudAgentState) -> dict:
    """Génère un résumé LLM en français de l'analyse de fraude."""

    error = state.get("error")
    if error:
        return {"llm_summary": f"❌ Analyse impossible : {error}"}

    iban          = state.get("iban", "")
    score_final   = state.get("score_final", 0)
    risk_level    = state.get("risk_level", "UNKNOWN")
    tracfin       = state.get("tracfin_required", False)
    fraud_results = state.get("fraud_results", [])
    account_sum   = state.get("account_summary") or {}
    download_url  = state.get("download_url", "")

    triggered_rules = [r for r in fraud_results if r.get("triggered")]
    rules_text = "\n".join(
        f"  • [{r.get('domain','?')}] {r.get('rule_name', r.get('rule','?'))} "
        f"(+{r.get('points',0)} pts) : {r.get('details','')}"
        for r in triggered_rules
    ) or "  Aucune règle déclenchée."

    risk_emoji = {"APPROVED": "🟢", "REVIEW": "🟡", "HOLD": "🟠", "BLOCK": "🔴"}.get(risk_level, "⚪")

    prompt = f"""Tu es un expert en détection de fraude bancaire. Génère un résumé concis et professionnel en français de l'analyse suivante.

IBAN analysé : {iban}
Transactions : {account_sum.get('total_transactions', 0)} | Montant total : {account_sum.get('total_amount', 0):,.2f} TND
Score final  : {score_final}/100
Niveau risque: {risk_emoji} {risk_level}
TRACFIN      : {"OUI ⚠️" if tracfin else "NON"}

Règles déclenchées :
{rules_text}

{"Rapport Excel : " + download_url if download_url else ""}

Instructions :
- 3 à 5 phrases maximum
- Cite les règles déclenchées les plus importantes
- Recommande une action concrète (bloquer, réviser, approuver)
- Mentionne TRACFIN si requis
- Ton professionnel et factuel"""

    try:
        llm      = _get_llm()
        response = llm.invoke([HumanMessage(content=prompt)])
        summary  = response.content.strip()
    except Exception as exc:
        logger.warning(f"[generate_summary] LLM failed: {exc}")
        summary = (
            f"{risk_emoji} **Analyse IBAN {iban}** — Score : {score_final}/100 ({risk_level})\n"
            f"Règles déclenchées : {len(triggered_rules)}\n"
            f"TRACFIN : {'OUI ⚠️' if tracfin else 'NON'}"
        )

    if download_url:
        summary += f"\n\n📥 [Télécharger le rapport Excel]({download_url})"

    return {"llm_summary": summary}