"""
scoring.py — Moteur de scoring dynamique.

Les points de chaque règle viennent désormais de la base de données
(champ `points` de FraudRuleModel), pas de valeurs hardcodées.

API publique inchangée pour ne pas casser nodes.py :
  compute_behavioral_score(df, db?)  → (int, list)
  compute_aml_score(rule_results)    → int
  compute_final_score(b_pts, aml)    → (int, str)
  check_tracfin_required(results, df)→ bool
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Seuils (README) ───────────────────────────────────────────────────────────
SCORE_CAP        = 100
THRESHOLD_HIGH   = 60   # ≥ 60 → BLOCK
THRESHOLD_MEDIUM = 30   # ≥ 30 → REVIEW  /  < 30 → APPROVED


# ══════════════════════════════════════════════════════════════════════════════
# Score total depuis les résultats de règles
# ══════════════════════════════════════════════════════════════════════════════

def compute_total_score(rule_results: list[dict]) -> int:
    """
    Somme les points de toutes les règles déclenchées, plafonnée à 100.
    Les points viennent du champ 'points' retourné par rule_engine.run_rules_from_db().
    """
    total = sum(r.get("points", 0) for r in rule_results if r.get("triggered"))
    return min(total, SCORE_CAP)


def risk_level_from_score(score: int) -> str:
    if score >= THRESHOLD_HIGH:
        return "BLOCK"
    elif score >= THRESHOLD_MEDIUM:
        return "REVIEW"
    else:
        return "APPROVED"


# ══════════════════════════════════════════════════════════════════════════════
# Score comportemental dynamique (depuis DB si session fournie)
# ══════════════════════════════════════════════════════════════════════════════

def compute_behavioral_score(
    df: pd.DataFrame,
    db: Optional[Session] = None,
) -> tuple[int, list[dict]]:
    """
    Calcule le score comportemental.

    Si `db` est fourni → utilise les règles actives en DB (domaines BEHAVIORAL,
    VELOCITY, GEOGRAPHIC, LIMIT) pour construire les signaux.

    Sinon → fallback sur les signaux hardcodés (compatibilité).

    Returns:
        (score: int, signals: list[dict])
    """
    if db is not None:
        return _behavioral_from_db(df, db)
    return _behavioral_hardcoded(df)


def _behavioral_from_db(df: pd.DataFrame, db: Session) -> tuple[int, list[dict]]:
    """Signaux comportementaux basés sur les règles actives en DB."""
    from fraud.models import FraudRuleModel
    from fraud.rule_engine import run_rules_from_db

    # On récupère toutes les règles actives et on filtre les domaines comportementaux
    BEHAVIORAL_DOMAINS = {"BEHAVIORAL", "VELOCITY", "GEOGRAPHIC", "LIMIT"}

    all_results = run_rules_from_db(df, db)

    signals = []
    total_pts = 0

    for r in all_results:
        if not r.get("triggered"):
            continue
        domain = r.get("domain", "")
        if domain not in BEHAVIORAL_DOMAINS:
            continue
        pts = r.get("points", 0)
        total_pts += pts
        signals.append({
            "signal": r.get("rule_name", r.get("rule", "")),
            "points": pts,
            "detail": r.get("details", ""),
        })

    return min(total_pts, SCORE_CAP), signals


def _behavioral_hardcoded(df: pd.DataFrame) -> tuple[int, list[dict]]:
    """Fallback hardcodé — utilisé si pas de session DB."""
    signals: list[dict] = []
    total_pts = 0

    amount_col = next(
        (c for c in ("transaction_amount", "montant", "amount") if c in df.columns),
        None,
    )
    amounts = (
        pd.to_numeric(df[amount_col], errors="coerce")
        if amount_col else pd.Series(dtype=float)
    )

    if not amounts.empty and (amounts > 3_000).any():
        count = int((amounts > 3_000).sum())
        total_pts += 20
        signals.append({"signal": "LARGE_AMOUNT", "points": 20,
                         "detail": f"{count} transaction(s) > 3,000"})

    if "timestamp" in df.columns:
        hours = df["timestamp"].dt.hour
        night_count = int(hours.between(0, 4).sum())
        if night_count > 0:
            total_pts += 10
            signals.append({"signal": "NIGHT_TX", "points": 10,
                             "detail": f"{night_count} transaction(s) 00:00–05:00"})

    if "ip_address" in df.columns:
        foreign = int(df["ip_address"].astype(str).str.startswith("185.230").sum())
        if foreign > 0:
            total_pts += 15
            signals.append({"signal": "FOREIGN_IP", "points": 15,
                             "detail": f"{foreign} transaction(s) IP étrangère"})

    if "merchant_mcc" in df.columns and not amounts.empty:
        mcc = pd.to_numeric(df["merchant_mcc"], errors="coerce")
        risky = mcc.isin({5541, 5999, 5311}) & (amounts > 1_500)
        if risky.any():
            total_pts += 10
            signals.append({"signal": "HIGH_RISK_MCC", "points": 10,
                             "detail": f"{int(risky.sum())} transaction(s) MCC risqué"})

    if "account_currentbalance" in df.columns and not amounts.empty:
        balances = pd.to_numeric(df["account_currentbalance"], errors="coerce")
        drain = (balances > 0) & (amounts > 0.80 * balances)
        if drain.any():
            total_pts += 10
            signals.append({"signal": "BALANCE_DRAIN", "points": 10,
                             "detail": f"{int(drain.sum())} transaction(s) > 80% du solde"})

    return min(total_pts, SCORE_CAP), signals


# ══════════════════════════════════════════════════════════════════════════════
# Score AML (somme des règles déclenchées)
# ══════════════════════════════════════════════════════════════════════════════

def compute_aml_score(rule_results: list[dict]) -> int:
    """
    Somme les points de toutes les règles déclenchées.
    Les points viennent de la DB via rule_engine → déjà dans rule_results.
    """
    return compute_total_score(rule_results)


# ══════════════════════════════════════════════════════════════════════════════
# Score final
# ══════════════════════════════════════════════════════════════════════════════

def compute_final_score(behavioral_pts: int, aml_score: int) -> tuple[int, str]:
    """
    Score final = max(comportemental, AML), plafonné à 100.
    Niveau de risque selon les seuils README.
    """
    score_final = min(SCORE_CAP, max(behavioral_pts, aml_score))
    return score_final, risk_level_from_score(score_final)


# ══════════════════════════════════════════════════════════════════════════════
# TRACFIN
# ══════════════════════════════════════════════════════════════════════════════

def check_tracfin_required(rule_results: list[dict], df: pd.DataFrame) -> bool:
    """
    Déclaration TRACFIN requise si score ≥ 60 ET au moins une règle
    de structuring / gros montant / IBAN suspect est déclenchée.

    Compatible avec les deux formats de résultats (DB et hardcodé).
    """
    total = compute_total_score(rule_results)
    if total < THRESHOLD_HIGH:
        return False

    # Mots-clés qui indiquent une règle à haut risque réglementaire
    HIGH_RISK_KEYWORDS = {
        "STRUCTURING", "LARGE_OR_ROUND_AMOUNT", "SUSPICIOUS_IBAN",
        "structur", "large", "iban",
    }

    for r in rule_results:
        if not r.get("triggered"):
            continue
        rule_id   = str(r.get("rule",      "")).upper()
        rule_name = str(r.get("rule_name", "")).upper()
        if any(kw.upper() in rule_id or kw.upper() in rule_name
               for kw in HIGH_RISK_KEYWORDS):
            return True

    return False