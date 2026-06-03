"""
scoring.py — Moteur de scoring dynamique.

Les points de chaque règle viennent de la base de données
(champ `points` de FraudRuleModel), pas de valeurs hardcodées.

All monetary thresholds in EUR (international standard).
Reference: 5AMLD, FATF GAFI 2023, PSD2 SCA, banking scoring matrix.

Score thresholds (unchanged — confirmed by research document):
  0–29   → APPROVED  🟢
  30–59  → REVIEW    🟡
  ≥ 60   → BLOCK     🔴

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

# ── Score thresholds (confirmed — no change) ──────────────────────────────────
SCORE_CAP        = 100
THRESHOLD_BLOCK  = 60   # ≥ 60 → BLOCK   (was THRESHOLD_HIGH)
THRESHOLD_REVIEW = 30   # ≥ 30 → REVIEW  /  < 30 → APPROVED

# Keep legacy alias for backward compatibility with any direct import
THRESHOLD_HIGH   = THRESHOLD_BLOCK
THRESHOLD_MEDIUM = THRESHOLD_REVIEW


# ══════════════════════════════════════════════════════════════════════════════
# Score total depuis les résultats de règles
# ══════════════════════════════════════════════════════════════════════════════

def compute_total_score(rule_results: list[dict]) -> int:
    """
    Sums points from all triggered rules, capped at 100.
    Points come from the 'points' field returned by rule_engine.run_rules_from_db().
    """
    total = sum(r.get("points", 0) for r in rule_results if r.get("triggered"))
    return min(total, SCORE_CAP)


def risk_level_from_score(score: int) -> str:
    if score >= THRESHOLD_BLOCK:
        return "BLOCK"
    elif score >= THRESHOLD_REVIEW:
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
    Calculates the behavioural score.

    If `db` is provided → uses active DB rules (domains BEHAVIORAL, VELOCITY,
    GEOGRAPHIC, LIMIT) to build signals.

    Otherwise → falls back to hardcoded signals (backward compat).

    Returns:
        (score: int, signals: list[dict])
    """
    if db is not None:
        return _behavioral_from_db(df, db)
    return _behavioral_hardcoded(df)


def _behavioral_from_db(df: pd.DataFrame, db: Session) -> tuple[int, list[dict]]:
    """Behavioural signals based on active DB rules."""
    from fraud.models import FraudRuleModel
    from fraud.rule_engine import run_rules_from_db

    BEHAVIORAL_DOMAINS = {"BEHAVIORAL", "VELOCITY", "GEOGRAPHIC", "LIMIT"}

    all_results = run_rules_from_db(df, db)

    signals    = []
    total_pts  = 0

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
    """
    Fallback hardcoded signals — used when no DB session is available.
    All thresholds updated to EUR (international standard).

    Changes vs. previous version:
      - HIGH_AMOUNT   : 3 000 → 5 000 EUR  (PSD2)
      - FOREIGN_IP    : 185.230 prefix retained as fallback only
      - HIGH_RISK_MCC : 1 500 EUR unchanged (confirmed)
      - BALANCE_DRAIN : 80% unchanged (confirmed)
    """
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

    # R1 — Large amount: > 5 000 EUR (was 3 000)
    if not amounts.empty and (amounts > 5_000).any():
        count = int((amounts > 5_000).sum())
        total_pts += 30  # updated score
        signals.append({
            "signal": "HIGH_AMOUNT",
            "points": 30,
            "detail": f"{count} transaction(s) > 5,000 EUR (PSD2 threshold)",
        })

    # R4 — Night transactions: 00:00–05:00 (unchanged)
    if "timestamp" in df.columns:
        hours       = df["timestamp"].dt.hour
        night_count = int(hours.between(0, 4).sum())
        if night_count > 0:
            total_pts += 10
            signals.append({
                "signal": "NIGHT_TX",
                "points": 10,
                "detail": f"{night_count} transaction(s) 00:00–05:00",
            })

    # R5 — Foreign IP: fallback prefix check (geofencing handled in rule_engine when DB available)
    if "ip_address" in df.columns:
        FOREIGN_PREFIXES = ("185.230", "185.", "46.166", "194.165")
        foreign = int(
            df["ip_address"].astype(str)
            .apply(lambda ip: any(ip.startswith(p) for p in FOREIGN_PREFIXES))
            .sum()
        )
        if foreign > 0:
            total_pts += 20  # updated score
            signals.append({
                "signal": "FOREIGN_IP",
                "points": 20,
                "detail": f"{foreign} transaction(s) from foreign/proxy IP range",
            })

    # R6 — High-risk MCC: 1 500 EUR (unchanged)
    if "merchant_mcc" in df.columns and not amounts.empty:
        mcc   = pd.to_numeric(df["merchant_mcc"], errors="coerce")
        risky = mcc.isin({5541, 5999, 5311}) & (amounts > 1_500)
        if risky.any():
            total_pts += 10
            signals.append({
                "signal": "HIGH_RISK_MCC",
                "points": 10,
                "detail": f"{int(risky.sum())} transaction(s) with risky MCC > 1,500 EUR",
            })

    # R7 — Balance drain: > 80% (unchanged)
    bal_col = next(
        (c for c in ("account_currentbalance", "account_current_balance", "balance") if c in df.columns),
        None,
    )
    if bal_col and not amounts.empty:
        balances = pd.to_numeric(df[bal_col], errors="coerce")
        drain    = (balances > 0) & (amounts > 0.80 * balances)
        if drain.any():
            total_pts += 10
            signals.append({
                "signal": "BALANCE_DRAIN",
                "points": 10,
                "detail": f"{int(drain.sum())} transaction(s) > 80% of account balance",
            })

    return min(total_pts, SCORE_CAP), signals


# ══════════════════════════════════════════════════════════════════════════════
# Score AML
# ══════════════════════════════════════════════════════════════════════════════

def compute_aml_score(rule_results: list[dict]) -> int:
    """
    Sums points from all triggered rules.
    Points come from DB via rule_engine → already in rule_results.
    """
    return compute_total_score(rule_results)


# ══════════════════════════════════════════════════════════════════════════════
# Score final
# ══════════════════════════════════════════════════════════════════════════════

def compute_final_score(behavioral_pts: int, aml_score: int) -> tuple[int, str]:
    """
    Final score = max(behavioural, AML), capped at 100.
    Risk level per confirmed thresholds (FATF RBA GAFI 2023):
      < 30  → APPROVED
      30–59 → REVIEW
      ≥ 60  → BLOCK
    """
    score_final = min(SCORE_CAP, max(behavioral_pts, aml_score))
    return score_final, risk_level_from_score(score_final)


# ══════════════════════════════════════════════════════════════════════════════
# TRACFIN trigger
# ══════════════════════════════════════════════════════════════════════════════

def check_tracfin_required(rule_results: list[dict], df: pd.DataFrame) -> bool:
    """
    TRACFIN declaration required if score ≥ 60 AND at least one
    high-regulatory-risk rule is triggered (structuring, large amount, OFAC IBAN).

    5AMLD Art.33 criteria:
      □ > 20 deposits < 10 000 € / week
      □ Wire cycle > 50 000 € < 48h
      □ Cash > 15 000 € without justification
      □ Client refuses KYC
    """
    total = compute_total_score(rule_results)
    if total < THRESHOLD_BLOCK:
        return False

    HIGH_RISK_KEYWORDS = {
        "STRUCTURING", "SMURFING", "SUSPICIOUS_IBAN",
        "HIGH_AMOUNT", "NEAR-THRESHOLD", "OFAC",
        "structur", "smurfing", "iban", "large", "near",
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
