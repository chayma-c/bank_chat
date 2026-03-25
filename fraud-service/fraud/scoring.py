"""
Fraud scoring engine.
Combines:
  1. Behavioral signal scoring (0-130 pts)
  2. AML rule scoring (0-100)
  3. Final score = min(100, max(behavioral, AML))

Based on the banking standard scoring matrix.
"""

import pandas as pd
from typing import Dict, List, Tuple


# ── Behavioral Signal Points ──────────────────────────────────────────────────
# (from documentation Section 5: SCORING MATRIX STANDARD BANQUE)

def compute_behavioral_score(df: pd.DataFrame) -> Tuple[int, List[Dict]]:
    """
    Compute behavioral signal score (0 to ~130 pts theoretical max).

    Scoring:
        Montant ≥ 10,000€  → +40 pts
        00h-04h             → +20 pts
        Pays étranger       → +30 pts
        Vélocité rapide     → +25 pts
        Marchand inconnu    → +15 pts
    """
    signals = []
    total_pts = 0

    amount_col = "montant" if "montant" in df.columns else "amount"

    # 1. High amount (≥ €10,000)
    if amount_col in df.columns:
        amounts = pd.to_numeric(df[amount_col], errors="coerce")
        if (amounts >= 10_000).any():
            total_pts += 40
            signals.append({"signal": "MONTANT_ELEVE", "points": 40,
                           "detail": f"Transactions ≥€10,000 detected"})

    # 2. Night-time (00h-04h)
    if "heure_jour" in df.columns:
        hours = pd.to_numeric(df["heure_jour"], errors="coerce")
        if (hours.between(0, 3)).any():
            total_pts += 20
            signals.append({"signal": "HEURE_NUIT", "points": 20,
                           "detail": "Transactions between 00h-04h"})
    elif "timestamp" in df.columns:
        hours = df["timestamp"].dt.hour
        if (hours.between(0, 3)).any():
            total_pts += 20
            signals.append({"signal": "HEURE_NUIT", "points": 20,
                           "detail": "Transactions between 00h-04h"})

    # 3. Foreign country
    country_col = "pays_dest" if "pays_dest" in df.columns else "country"
    if country_col in df.columns:
        countries = df[country_col].astype(str).str.upper().str.strip()
        # Assume TN (Tunisia) or FR (France) as home — flag others
        local_countries = {"TN", "FR"}
        foreign = countries[~countries.isin(local_countries) & (countries != "NAN")]
        if not foreign.empty:
            total_pts += 30
            signals.append({"signal": "PAYS_ETRANGER", "points": 30,
                           "detail": f"Foreign countries: {foreign.unique().tolist()}"})

    # 4. High velocity
    if "velocity_1h" in df.columns:
        vel = pd.to_numeric(df["velocity_1h"], errors="coerce")
        if (vel > 5).any():
            total_pts += 25
            signals.append({"signal": "VELOCITE_RAPIDE", "points": 25,
                           "detail": f"Max velocity: {int(vel.max())} txs/h"})

    # 5. Unknown merchant (new user agent)
    if "user_agent_new" in df.columns:
        unknown = df["user_agent_new"].astype(str).str.lower()
        if unknown.isin(["unknown", "new", "true"]).any():
            total_pts += 15
            signals.append({"signal": "DEVICE_INCONNU", "points": 15,
                           "detail": "Unknown/new device detected"})

    return total_pts, signals


# ── AML Rule Score ────────────────────────────────────────────────────────────

def compute_aml_score(rule_results: List[Dict]) -> int:
    """
    Convert rule-based fraud results to AML score (0-100).
    Takes the maximum rule score × 100.
    """
    if not rule_results:
        return 0

    max_score = max(r.get("score", 0.0) for r in rule_results)
    return int(max_score * 100)


# ── Final Composite Score ─────────────────────────────────────────────────────

def compute_final_score(behavioral_pts: int, aml_score: int) -> Tuple[int, str]:
    """
    Final score = min(100, max(behavioral, aml))
    Based on RBA GAFI 2023.

    Action thresholds:
        0-29  → 🟢 APPROVED
        30-49 → 🟡 REVIEW
        50-69 → 🟠 HOLD
        70-100→ 🔴 BLOCK
    """
    score_final = min(100, max(behavioral_pts, aml_score))

    if score_final >= 70:
        risk_level = "BLOCK"
    elif score_final >= 50:
        risk_level = "HOLD"
    elif score_final >= 30:
        risk_level = "REVIEW"
    else:
        risk_level = "APPROVED"

    return score_final, risk_level


# ── TRACFIN Declaration Check ─────────────────────────────────────────────────

def check_tracfin_required(rule_results: List[Dict], df: pd.DataFrame) -> bool:
    """
    Check if TRACFIN declaration is mandatory.
    Criteria:
        □ > 20 dépôts < 10k€ / semaine
        □ Cycle virements > 50k€ < 48h
        □ Espèces > 15k€ sans justificatif
        □ Client REFUSE KYC
    """
    for r in rule_results:
        if r["rule"] == "STRUCTURING_SMURFING" and r["triggered"]:
            return True
        if r["rule"] == "LAYERING_CASCADE" and r["score"] >= 0.9:
            return True
        if r["rule"] == "OFAC_SANCTIONED" and r["triggered"]:
            return True

    # Check large cash without justification
    amount_col = "montant" if "montant" in df.columns else "amount"
    justif_col = "justificatif_present" if "justificatif_present" in df.columns else None

    if amount_col in df.columns and justif_col and justif_col in df.columns:
        amounts = pd.to_numeric(df[amount_col], errors="coerce")
        no_justif = df[justif_col].astype(str).str.lower().isin(["false", "0", "no", "none"])
        if ((amounts > 15_000) & no_justif).any():
            return True

    return False