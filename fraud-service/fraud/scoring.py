"""
Fraud scoring engine — README-aligned implementation.

Scoring model (from fraud-service/README.md):
  - Sum the 'points' from all triggered rules, capped at 100.
  - Risk levels:
      < 30   → LOW_RISK   (APPROVED)
      30–59  → MEDIUM_RISK (REVIEW)
      ≥ 60   → HIGH_RISK  (BLOCK)

Public API (unchanged so nodes.py doesn't need edits):
  compute_behavioral_score(df) → (int, list)   ← now delegates to rule points
  compute_aml_score(rule_results) → int
  compute_final_score(behavioral_pts, aml_score) → (int, str)
  check_tracfin_required(rule_results, df) → bool
"""

import pandas as pd
from typing import Dict, List, Tuple


# ── Scoring constants (README) ────────────────────────────────────────────────

SCORE_CAP = 100

# Risk thresholds (README §Risk Scoring System)
THRESHOLD_HIGH   = 60   # ≥ 60 → HIGH_RISK / BLOCK
THRESHOLD_MEDIUM = 30   # ≥ 30 → MEDIUM_RISK / REVIEW
                        # < 30 → LOW_RISK / APPROVED


# ── Main scorer ───────────────────────────────────────────────────────────────

def compute_total_score(rule_results: List[Dict]) -> int:
    """
    Sum all 'points' from triggered rules, capped at SCORE_CAP (100).
    This is the primary score from the README model.
    """
    total = sum(r.get("points", 0) for r in rule_results if r.get("triggered"))
    return min(total, SCORE_CAP)


def risk_level_from_score(score: int) -> str:
    """Map a 0–100 score to a risk level string."""
    if score >= THRESHOLD_HIGH:
        return "BLOCK"
    elif score >= THRESHOLD_MEDIUM:
        return "REVIEW"
    else:
        return "APPROVED"


# ── Compatibility shim for nodes.py (public API preserved) ───────────────────

def compute_behavioral_score(df: pd.DataFrame) -> Tuple[int, List[Dict]]:
    """
    Behavioral signals extracted from the DataFrame.

    README-aligned signals (derived from available columns):
      • Amount > 3,000         → +20 pts  (LARGE_AMOUNT)
      • Night tx (00:00–05:00) → +10 pts  (NIGHT_TX)
      • Foreign IP 185.230.x.x → +15 pts  (FOREIGN_IP)
      • High-risk MCC + >1,500 → +10 pts  (HIGH_RISK_MCC)
      • Amount > 80% balance   → +10 pts  (BALANCE_DRAIN)
    """
    signals: List[Dict] = []
    total_pts = 0

    # ── Amount helpers ────────────────────────────────────────────────────────
    amount_col = next(
        (c for c in ("transaction_amount", "montant", "amount") if c in df.columns),
        None,
    )
    amounts = (
        pd.to_numeric(df[amount_col], errors="coerce")
        if amount_col else pd.Series(dtype=float)
    )

    # 1. Large amount > 3,000
    if not amounts.empty and (amounts > 3_000).any():
        count = int((amounts > 3_000).sum())
        total_pts += 20
        signals.append({
            "signal": "LARGE_AMOUNT",
            "points": 20,
            "detail": f"{count} transaction(s) > 3,000 (max: {amounts[amounts > 3_000].max():,.2f})",
        })

    # 2. Night transactions (00:00–05:00)
    if "timestamp" in df.columns:
        hours = df["timestamp"].dt.hour
        night_count = int(hours.between(0, 4).sum())
        if night_count > 0:
            total_pts += 10
            signals.append({
                "signal": "NIGHT_TX",
                "points": 10,
                "detail": f"{night_count} transaction(s) between 00:00–05:00",
            })

    # 3. Foreign IP (185.230.x.x)
    if "ip_address" in df.columns:
        foreign_count = int(
            df["ip_address"].astype(str).str.startswith("185.230").sum()
        )
        if foreign_count > 0:
            total_pts += 15
            signals.append({
                "signal": "FOREIGN_IP",
                "points": 15,
                "detail": f"{foreign_count} transaction(s) from foreign IP (185.230.*)",
            })

    # 4. High-risk MCC with amount > 1,500
    if "merchant_mcc" in df.columns and not amounts.empty:
        HIGH_RISK_MCC = {5541, 5999, 5311}
        mcc = pd.to_numeric(df["merchant_mcc"], errors="coerce")
        risky = (mcc.isin(HIGH_RISK_MCC)) & (amounts > 1_500)
        risky_count = int(risky.sum())
        if risky_count > 0:
            total_pts += 10
            signals.append({
                "signal": "HIGH_RISK_MCC",
                "points": 10,
                "detail": f"{risky_count} high-risk MCC transaction(s) > 1,500",
            })

    # 5. Balance drain (amount > 80% of balance)
    if "account_currentbalance" in df.columns and not amounts.empty:
        balances = pd.to_numeric(df["account_currentbalance"], errors="coerce")
        drain = (balances > 0) & (amounts > 0.80 * balances)
        drain_count = int(drain.sum())
        if drain_count > 0:
            total_pts += 10
            signals.append({
                "signal": "BALANCE_DRAIN",
                "points": 10,
                "detail": f"{drain_count} transaction(s) exceed 80% of account balance",
            })

    return min(total_pts, SCORE_CAP), signals


def compute_aml_score(rule_results: List[Dict]) -> int:
    """
    Sum points from all triggered rules (README additive model), capped at 100.
    This replaces the old "max rule score × 100" approach.
    """
    return compute_total_score(rule_results)


def compute_final_score(behavioral_pts: int, aml_score: int) -> Tuple[int, str]:
    """
    Final score = the higher of behavioral score and AML rule score, capped at 100.
    Risk levels follow the README thresholds:
      < 30  → APPROVED
      30–59 → REVIEW
      ≥ 60  → BLOCK
    """
    score_final = min(SCORE_CAP, max(behavioral_pts, aml_score))
    risk_level  = risk_level_from_score(score_final)
    return score_final, risk_level


def check_tracfin_required(rule_results: List[Dict], df: pd.DataFrame) -> bool:
    """
    TRACFIN declaration is required when risk is HIGH (score ≥ 60)
    and at least one structuring or large-amount rule is triggered.
    """
    total = compute_total_score(rule_results)
    if total < THRESHOLD_HIGH:
        return False

    high_risk_rules = {"STRUCTURING", "LARGE_OR_ROUND_AMOUNT", "SUSPICIOUS_IBAN"}
    for r in rule_results:
        if r.get("rule") in high_risk_rules and r.get("triggered"):
            return True

    return False