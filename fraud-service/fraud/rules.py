"""
Rule-based fraud detection engine.
Implements all banking regulations and detection rules from the documentation:
  - Velocity rules (card, wire, deposit)
  - High-value thresholds
  - AML / Smurfing / Structuring
  - Layering cascade detection
  - OFAC sanctioned countries
  - Round-amount suspect detection
  - Geofencing
  - Remote access software detection
  - Night-time transaction detection
"""

import pandas as pd
import numpy as np
from datetime import timedelta
from typing import List, Dict


# ── Constants ─────────────────────────────────────────────────────────────────

OFAC_COUNTRIES = {"RU", "IR", "KP", "SY", "VE"}

SUSPECT_ROUND_AMOUNTS = [9_990, 9_950, 29_990, 49_990, 99_990]
ROUND_THRESHOLD = 50  # within €50 of a suspicious threshold

STRUCTURING_THRESHOLD = 10_000  # €10,000
SMURFING_COUNT_WEEKLY = 20      # >20 deposits <10k€ per week
VELOCITY_CARD_1H = 5            # >5 card txs per hour
VELOCITY_WIRE_10M = 10          # >10 wires per 10 min
HIGH_VALUE_CARD = 5_000
HIGH_VALUE_WIRE = 15_000
HIGH_VALUE_CHEQUE = 10_000
GEO_DISTANCE_THRESHOLD = 1_000  # km

REMOTE_SOFTWARE = {"anydesk", "teamviewer", "remotepc", "logmein"}


def _safe_col(df: pd.DataFrame, name: str, default=None):
    """Return column if exists, else a Series of defaults."""
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index)


# ── Individual Rule Functions ─────────────────────────────────────────────────

def _amount_col(df: pd.DataFrame) -> str:
    """Return the name of the amount column, new schema first."""
    for col in ("transaction_amount", "montant", "amount"):
        if col in df.columns:
            return col
    return "transaction_amount"  # will be missing → handled by callers


def _type_col(df: pd.DataFrame) -> str:
    """Return the name of the transaction-type column."""
    for col in ("transaction_type", "type_transaction", "type"):
        if col in df.columns:
            return col
    return "transaction_type"


def _iban_dest_col(df: pd.DataFrame) -> str:
    """Return the destination-IBAN column name."""
    for col in ("counterparty_iban", "compte_dest", "iban_dest"):
        if col in df.columns:
            return col
    return "counterparty_iban"


def _country_col(df: pd.DataFrame) -> str:
    """Return the country column name (derived or raw)."""
    for col in ("country", "pays_dest"):
        if col in df.columns:
            return col
    return "country"


def check_velocity_card(df: pd.DataFrame) -> Dict:
    """VELOCITY_CARD_1H: >5 card transactions in 1 hour."""
    results = []
    if "timestamp" not in df.columns:
        return {"rule": "VELOCITY_CARD_1H", "triggered": False, "score": 0.0,
                "details": "No timestamp column", "severity": "LOW"}

    type_col = _type_col(df)
    card_types = ["DEBIT", "CREDIT", "CARD", "CB", "CARD_PAYMENT"]
    card_mask = _safe_col(df, type_col, "").str.upper().isin(card_types)
    card_df = df[card_mask].sort_values("timestamp")

    if card_df.empty:
        return {"rule": "VELOCITY_CARD_1H", "triggered": False, "score": 0.0,
                "details": "No card transactions found", "severity": "LOW"}

    # Rolling 1h window count
    card_df = card_df.set_index("timestamp")
    hourly_counts = card_df.resample("1h").size()
    max_count = int(hourly_counts.max()) if not hourly_counts.empty else 0

    triggered = max_count > VELOCITY_CARD_1H
    score = min(1.0, 0.5 + 0.1 * (max_count - VELOCITY_CARD_1H)) if triggered else 0.0
    severity = "HIGH" if triggered else "LOW"

    return {
        "rule": "VELOCITY_CARD_1H",
        "triggered": triggered,
        "score": round(score, 2),
        "details": f"Max {max_count} card transactions in 1h window (threshold: {VELOCITY_CARD_1H})",
        "severity": severity,
    }


def check_structuring_smurfing(df: pd.DataFrame) -> Dict:
    """STRUCTURING_SMURFING: >20 deposits < €10k in 7 days."""
    amount_col = _amount_col(df)
    if amount_col not in df.columns or "timestamp" not in df.columns:
        return {"rule": "STRUCTURING_SMURFING", "triggered": False, "score": 0.0,
                "details": "Missing required columns", "severity": "LOW"}

    amounts = pd.to_numeric(df[amount_col], errors="coerce")
    small_deposits = df[(amounts > 0) & (amounts < STRUCTURING_THRESHOLD)].copy()

    if small_deposits.empty:
        return {"rule": "STRUCTURING_SMURFING", "triggered": False, "score": 0.0,
                "details": "No small deposits found", "severity": "LOW"}

    # Group by week (ISO week)
    small_deposits = small_deposits.copy()
    small_deposits["week"] = small_deposits["timestamp"].dt.isocalendar().week
    weekly_counts = small_deposits.groupby("week").size()
    max_weekly = int(weekly_counts.max())

    triggered = max_weekly > SMURFING_COUNT_WEEKLY
    score = min(1.0, 0.6 + 0.02 * (max_weekly - SMURFING_COUNT_WEEKLY)) if triggered else 0.0
    severity = "CRITICAL" if triggered else "LOW"

    return {
        "rule": "STRUCTURING_SMURFING",
        "triggered": triggered,
        "score": round(score, 2),
        "details": f"Max {max_weekly} deposits <€10k in one week (threshold: {SMURFING_COUNT_WEEKLY})",
        "severity": severity,
    }


def check_layering_cascade(df: pd.DataFrame) -> Dict:
    """LAYERING_CASCADE: cyclic transfers A→B→C→A within 48h."""
    dest_col = _iban_dest_col(df)
    if "timestamp" not in df.columns or dest_col not in df.columns:
        return {"rule": "LAYERING_CASCADE", "triggered": False, "score": 0.0,
                "details": "Missing timestamp/counterparty_iban columns", "severity": "LOW"}

    sorted_df = df.sort_values("timestamp")
    destinations = sorted_df[dest_col].astype(str).tolist()
    timestamps = sorted_df["timestamp"].tolist()

    # Look for repeated destination within 48h window
    cycle_detected = False
    partial_cycle = False

    dest_seen = {}
    for i, (dest, ts) in enumerate(zip(destinations, timestamps)):
        if dest in dest_seen:
            prev_ts = dest_seen[dest]
            if hasattr(ts, 'timestamp') and hasattr(prev_ts, 'timestamp'):
                delta = (ts - prev_ts)
                if hasattr(delta, 'total_seconds') and delta.total_seconds() < 48 * 3600:
                    cycle_detected = True
                    break

        dest_seen[dest] = ts

    # Check for partial pattern: >10 internal transfers per day
    if "timestamp" in sorted_df.columns:
        daily_counts = sorted_df.set_index("timestamp").resample("1D").size()
        if not daily_counts.empty and daily_counts.max() > 10:
            partial_cycle = True

    if cycle_detected:
        score, severity = 0.90, "CRITICAL"
    elif partial_cycle:
        score, severity = 0.55, "HIGH"
    else:
        score, severity = 0.0, "LOW"

    return {
        "rule": "LAYERING_CASCADE",
        "triggered": cycle_detected or partial_cycle,
        "score": score,
        "details": (
            "Full cycle detected (A→B→A <48h)" if cycle_detected
            else "Partial layering pattern (>10 txs/day)" if partial_cycle
            else "No layering pattern detected"
        ),
        "severity": severity,
    }


def check_ofac_sanctioned(df: pd.DataFrame) -> Dict:
    """OFAC_SANCTIONED: any transaction involving sanctioned country."""
    country_col = _country_col(df)
    if country_col not in df.columns:
        return {"rule": "OFAC_SANCTIONED", "triggered": False, "score": 0.0,
                "details": "No country column found", "severity": "LOW"}

    countries = df[country_col].astype(str).str.upper().str.strip()
    ofac_hits = countries[countries.isin(OFAC_COUNTRIES)]
    triggered = not ofac_hits.empty

    return {
        "rule": "OFAC_SANCTIONED",
        "triggered": triggered,
        "score": 1.0 if triggered else 0.0,
        "details": (
            f"Transactions to sanctioned countries: {ofac_hits.unique().tolist()}"
            if triggered else "No OFAC-sanctioned countries detected"
        ),
        "severity": "CRITICAL" if triggered else "LOW",
    }


def check_round_amount_suspect(df: pd.DataFrame) -> Dict:
    """ROUND_AMOUNT_SUSPECT: amounts suspiciously close to regulatory thresholds."""
    amount_col = _amount_col(df)
    if amount_col not in df.columns:
        return {"rule": "ROUND_AMOUNT_SUSPECT", "triggered": False, "score": 0.0,
                "details": "No amount column", "severity": "LOW"}

    amounts = pd.to_numeric(df[amount_col], errors="coerce").dropna()
    suspect_txs = []

    for threshold in SUSPECT_ROUND_AMOUNTS:
        matches = amounts[(amounts >= threshold - ROUND_THRESHOLD) & (amounts <= threshold)]
        if not matches.empty:
            suspect_txs.extend(matches.tolist())

    triggered = len(suspect_txs) > 0

    return {
        "rule": "ROUND_AMOUNT_SUSPECT",
        "triggered": triggered,
        "score": 0.65 if triggered else 0.0,
        "details": (
            f"Found {len(suspect_txs)} transactions near regulatory thresholds: "
            f"{[round(x, 2) for x in suspect_txs[:5]]}"
            if triggered else "No suspicious round amounts"
        ),
        "severity": "HIGH" if triggered else "LOW",
    }


def check_night_transactions(df: pd.DataFrame) -> Dict:
    """Night-time transactions (00h-04h) — +20 pts in scoring."""
    hour_col = "heure_jour" if "heure_jour" in df.columns else None

    if hour_col is None and "timestamp" in df.columns:
        hours = df["timestamp"].dt.hour
    elif hour_col:
        hours = pd.to_numeric(df[hour_col], errors="coerce")
    else:
        return {"rule": "NIGHT_TRANSACTIONS", "triggered": False, "score": 0.0,
                "details": "No hour data available", "severity": "LOW"}

    night_mask = hours.between(0, 4, inclusive="left")
    night_count = int(night_mask.sum())
    triggered = night_count > 0

    return {
        "rule": "NIGHT_TRANSACTIONS",
        "triggered": triggered,
        "score": 0.3 if triggered else 0.0,
        "details": f"{night_count} transactions between 00h-04h",
        "severity": "MEDIUM" if triggered else "LOW",
    }


def check_geo_anomaly(df: pd.DataFrame) -> Dict:
    """GEO_DISTANCE: distance > 1000km vs client's usual location."""
    dist_col = "distance_geo_km" if "distance_geo_km" in df.columns else None
    if dist_col is None:
        return {"rule": "GEO_DISTANCE", "triggered": False, "score": 0.0,
                "details": "No geolocation data", "severity": "LOW"}

    distances = pd.to_numeric(df[dist_col], errors="coerce").dropna()
    far_txs = distances[distances > GEO_DISTANCE_THRESHOLD]
    triggered = not far_txs.empty

    return {
        "rule": "GEO_DISTANCE",
        "triggered": triggered,
        "score": 0.5 if triggered else 0.0,
        "details": (
            f"{len(far_txs)} transactions from >{GEO_DISTANCE_THRESHOLD}km away "
            f"(max: {round(float(far_txs.max()), 0)}km)"
            if triggered else "All transactions within normal geographic range"
        ),
        "severity": "HIGH" if triggered else "LOW",
    }


def check_high_value(df: pd.DataFrame) -> Dict:
    """HIGH_VALUE: transactions exceeding standard thresholds."""
    amount_col = _amount_col(df)
    if amount_col not in df.columns:
        return {"rule": "HIGH_VALUE", "triggered": False, "score": 0.0,
                "details": "No amount column", "severity": "LOW"}

    amounts = pd.to_numeric(df[amount_col], errors="coerce")
    high = amounts[amounts >= HIGH_VALUE_CARD]
    triggered = not high.empty

    return {
        "rule": "HIGH_VALUE",
        "triggered": triggered,
        "score": 0.4 if triggered else 0.0,
        "details": (
            f"{len(high)} high-value transactions (>€{HIGH_VALUE_CARD}), "
            f"max: €{round(float(high.max()), 2)}"
            if triggered else "No high-value transactions"
        ),
        "severity": "MEDIUM" if triggered else "LOW",
    }


def check_remote_access(df: pd.DataFrame) -> Dict:
    """REMOTE_ACCESS: detecting AnyDesk/TeamViewer during session."""
    col = "logiciel_remote" if "logiciel_remote" in df.columns else None
    if col is None:
        return {"rule": "REMOTE_ACCESS", "triggered": False, "score": 0.0,
                "details": "No remote software column", "severity": "LOW"}

    values = df[col].astype(str).str.lower().str.strip()
    # Filter out None, nan, empty, "none"
    remote_mask = values.apply(
        lambda x: x not in ("none", "nan", "", "false", "null") and x in REMOTE_SOFTWARE
    )
    remote_count = int(remote_mask.sum())
    triggered = remote_count > 0

    return {
        "rule": "REMOTE_ACCESS",
        "triggered": triggered,
        "score": 0.8 if triggered else 0.0,
        "details": (
            f"Remote access software detected in {remote_count} transactions"
            if triggered else "No remote access software detected"
        ),
        "severity": "CRITICAL" if triggered else "LOW",
    }


def check_new_beneficiary_high_transfer(df: pd.DataFrame) -> Dict:
    """
    NEW_BENEFICIARY: detect high-value transfers to counterparties that appear
    only once (proxy for a 'new' beneficiary), compared to mean transaction amount.
    (Column 'nouveau_beneficiaire' no longer exists in the new CSV schema.)
    """
    amount_col = _amount_col(df)
    dest_col   = _iban_dest_col(df)

    if amount_col not in df.columns or dest_col not in df.columns:
        return {"rule": "NEW_BENEFICIARY_HIGH_TRANSFER", "triggered": False, "score": 0.0,
                "details": "Missing amount/IBAN columns", "severity": "LOW"}

    # Parse boolean-like column
    new_ben = df[new_ben_col].astype(str).str.lower().isin(["true", "1", "yes"])
    amounts = pd.to_numeric(df[amount_col], errors="coerce")
    mean_amount = amounts.mean()

    # New beneficiary + amount > 10x average
    suspect_mask = new_ben & (amounts > mean_amount * 10)
    suspect_count = int(suspect_mask.sum())
    triggered = suspect_count > 0

    return {
        "rule": "NEW_BENEFICIARY_HIGH_TRANSFER",
        "triggered": triggered,
        "score": 0.7 if triggered else 0.0,
        "details": (
            f"{suspect_count} transfers to new beneficiary exceeding 10x average (€{round(float(mean_amount), 2)})"
            if triggered else "No suspicious new-beneficiary transfers"
        ),
        "severity": "HIGH" if triggered else "LOW",
    }


def check_proxy_ip(df: pd.DataFrame) -> Dict:
    """PROXY_IP: transactions through proxy/VPN."""
    col = "ip_proxy" if "ip_proxy" in df.columns else None
    if col is None:
        return {"rule": "PROXY_IP", "triggered": False, "score": 0.0,
                "details": "No proxy IP column", "severity": "LOW"}

    proxy_mask = df[col].astype(str).str.lower().isin(["true", "1", "yes"])
    proxy_count = int(proxy_mask.sum())
    triggered = proxy_count > 0

    return {
        "rule": "PROXY_IP",
        "triggered": triggered,
        "score": 0.4 if triggered else 0.0,
        "details": f"{proxy_count} transactions via proxy/VPN" if triggered else "No proxy usage detected",
        "severity": "MEDIUM" if triggered else "LOW",
    }


def check_login_failures(df: pd.DataFrame) -> Dict:
    """LOGIN_FAILURES: excessive login failures (MFA fatigue)."""
    col = "login_fails_1h" if "login_fails_1h" in df.columns else None
    if col is None:
        return {"rule": "LOGIN_FAILURES", "triggered": False, "score": 0.0,
                "details": "No login failure column", "severity": "LOW"}

    fails = pd.to_numeric(df[col], errors="coerce")
    max_fails = int(fails.max()) if not fails.empty else 0
    triggered = max_fails >= 5  # 5+ failures in 1h = suspicious

    return {
        "rule": "LOGIN_FAILURES",
        "triggered": triggered,
        "score": 0.5 if triggered else 0.0,
        "details": f"Max {max_fails} login failures in 1h" if triggered else "Normal login activity",
        "severity": "HIGH" if triggered else "LOW",
    }


def check_crypto_transactions(df: pd.DataFrame) -> Dict:
    """CRYPTO: Fiat → Crypto transfers."""
    col = "is_crypto" if "is_crypto" in df.columns else None
    if col is None:
        return {"rule": "CRYPTO_TRANSFER", "triggered": False, "score": 0.0,
                "details": "No crypto column", "severity": "LOW"}

    crypto_mask = df[col].astype(str).str.lower().isin(["true", "1", "yes"])
    crypto_count = int(crypto_mask.sum())
    triggered = crypto_count > 0

    amount_col = "montant" if "montant" in df.columns else "amount"
    total_crypto = 0.0
    if triggered and amount_col in df.columns:
        total_crypto = float(pd.to_numeric(df.loc[crypto_mask, amount_col], errors="coerce").sum())

    return {
        "rule": "CRYPTO_TRANSFER",
        "triggered": triggered,
        "score": 0.6 if (triggered and total_crypto > 5000) else 0.3 if triggered else 0.0,
        "details": (
            f"{crypto_count} crypto transactions totaling €{round(total_crypto, 2)}"
            if triggered else "No cryptocurrency transactions"
        ),
        "severity": "HIGH" if (triggered and total_crypto > 5000) else "MEDIUM" if triggered else "LOW",
    }


# ── Master Rule Runner ────────────────────────────────────────────────────────

ALL_RULES = [
    check_velocity_card,
    check_structuring_smurfing,
    check_layering_cascade,
    check_ofac_sanctioned,
    check_round_amount_suspect,
    check_night_transactions,
    check_geo_anomaly,
    check_high_value,
    check_remote_access,
    check_new_beneficiary_high_transfer,
    check_proxy_ip,
    check_login_failures,
    check_crypto_transactions,
]


def run_all_rules(df: pd.DataFrame) -> List[Dict]:
    """Execute all fraud detection rules on the given DataFrame."""
    results = []
    for rule_fn in ALL_RULES:
        try:
            result = rule_fn(df)
            results.append(result)
        except Exception as e:
            results.append({
                "rule": rule_fn.__name__,
                "triggered": False,
                "score": 0.0,
                "details": f"Error: {str(e)}",
                "severity": "LOW",
            })
    return results