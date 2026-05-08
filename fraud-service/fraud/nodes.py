"""
nodes.py — Nœuds du graphe LangGraph pour la détection de fraude.

Modification principale :
  - analyze_fraud() ouvre une session DB et appelle rule_engine.run_rules_from_db()
    au lieu de rules.run_all_rules() (qui était statique).
  - Les règles actives sont lues depuis la table fraud_rules à chaque analyse.
  - Ajouter, modifier ou désactiver une règle en DB est immédiatement pris en compte.
"""

from __future__ import annotations

import json
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
    """Extrait l'IBAN et l'action via LLM pour plus de robustesse."""
    last_msg = state["messages"][-1] if state["messages"] else None
    text = last_msg.content if last_msg else ""

    if not text:
        return {"error": "Message vide."}

    # ── Tentative d'extraction intelligente par LLM ───────────────────
    extraction_prompt = f"""Tu es un extracteur d'entités bancaires expert.
Extrais l'IBAN et l'intention (action) du message suivant.

Message: "{text}"

Règles :
1. IBAN: Doit être un IBAN complet (ex: TN59...) ou un identifiant court (ex: IBAN_123). Si absent, renvoie "".
2. Action: "export_transactions" si l'utilisateur veut télécharger, exporter ou obtenir un fichier Excel. Sinon "fraud_check".

Réponds UNIQUEMENT au format JSON :
{{"iban": "...", "action": "..."}}"""

    iban = ""
    action = "fraud_check"
    try:
        llm = _get_llm()
        resp = llm.invoke([HumanMessage(content=extraction_prompt)])
        import json
        data = json.loads(re.search(r"\{.*\}", resp.content, re.DOTALL).group(0))
        iban = data.get("iban", "").replace(" ", "").upper()
        action = data.get("action", "fraud_check")
    except Exception as exc:
        logger.warning(f"[parse_request] LLM extraction failed: {exc}. Falling back to Regex.")
        # Fallback Regex
        iban = _extract_iban(text)
        if any(w in text.lower() for w in ("export", "télécharge", "download", "exporter")):
            action = "export_transactions"

    logger.info(f"[parse_request] IBAN={iban!r} action={action!r}")

    if not iban:
        return {
            "iban":   "",
            "action": action,
            "error":  "L'IBAN n'a pas pu être identifié. Veuillez préciser le compte à analyser.",
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

    # ── Sélectionner des transactions suspectes pour le LLM ──────────────
    # On prend les 5 plus gros montants + les transactions liées à des règles
    suspicious_samples = []
    if not df.empty:
        # Top 5 montants
        top_amounts = df.sort_values(by=df.columns[df.columns.str.contains("amount|montant")][0], ascending=False).head(5)
        for _, row in top_amounts.iterrows():
            suspicious_samples.append(f"• {row.get('timestamp','?')} | {row.get('amount', row.get('transaction_amount',0)):,.2f} TND | {row.get('transaction_type','?')} -> {row.get('counterparty_iban','?')}")

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
        "suspicious_samples": "\n".join(suspicious_samples[:5]),
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

    samples_text = state.get("suspicious_samples", "Aucun échantillon disponible.")

    risk_emoji = {"APPROVED": "🟢", "REVIEW": "🟡", "HOLD": "🟠", "BLOCK": "🔴"}.get(risk_level, "⚪")

    prompt = f"""Tu es le Senior Fraud Compliance Officer (Expert AML/CTF) de BankChat.

IBAN : {iban}
Score Risque : {score_final}/100 ({risk_level})
TRACFIN : {"REQUIS ⚠️" if tracfin else "Non requis"}
Activité : {account_sum.get('total_transactions', 0)} txs ({account_sum.get('total_amount', 0):,.2f} TND)

Règles déclenchées :
{rules_text}

Échantillons de transactions notables :
{samples_text}

STRUCTURE DU RAPPORT (RÉPONDS EN FRANÇAIS) :
1. ANALYSE MULTI-FACTEURS : Explique la corrélation entre les règles déclenchées. Ne te contente pas de les lister. (Ex: "Le client effectue des dépôts structurés juste avant des transferts nocturnes vers des IPs étrangères, ce qui suggère une tentative de dissimulation de fonds.")
2. ÉVALUATION DES ÉCHANTILLONS : Commente brièvement les transactions les plus suspectes citées ci-dessus.
3. VERDICT & JUSTIFICATION : Confirme le niveau de risque ({risk_level}) et explique pourquoi il est proportionné.
4. ACTIONS IMMÉDIATES : Liste les étapes (ex: Demander justificatifs, Blocage temporaire, Déclaration TRACFIN).

TON : Clinique, autoritaire, expert, sans fioritures."""

    try:
        llm      = _get_llm()
        # On utilise un système de chain-of-thought implicite via la structure demandée
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