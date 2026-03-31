"""
Rule-based fraud detection engine.
Implements the 8 rules defined in fraud-service/README.md, working with
the current CSV schema:

  transaction_amount, timestamp, geo_location, ip_address, merchant_mcc,
  account_currentbalance, client_iban, counterparty_iban, transaction_type

Each rule returns a dict:
  {
    "rule":      str,   # rule identifier
    "triggered": bool,
    "points":    int,   # points added to final score (0 when not triggered)
    "details":   str,
    "severity":  "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  }

Point table (from README):
  Large amount (>3000)                        → +20
  Round/suspicious amount                     → +15
  Client IBAN in suspicious list              → +20
  Counterparty IBAN in suspicious list        → +15
  Structuring pattern (3+ txs 850–950 in 24h)→ +25
  Night transaction (00:00–05:00)             → +10
  Foreign IP (185.230.x.x)                   → +15
  Foreign IP + amount > 2000                  → +10 extra
  High-risk MCC + amount > 1500               → +10
  Amount > 80% of balance                     → +10
  Same IBAN ≥ 3 alerts in 7 days             → +20  (skipped: no alert column)
"""

import pandas as pd
from datetime import timedelta
from typing import List, Dict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _amount_col(df: pd.DataFrame) -> str:
    for col in ("transaction_amount", "montant", "amount"):
        if col in df.columns:
            return col
    return "transaction_amount"


def _type_col(df: pd.DataFrame) -> str:
    for col in ("transaction_type", "type_transaction", "type"):
        if col in df.columns:
            return col
    return "transaction_type"


def _safe_amounts(df: pd.DataFrame) -> pd.Series:
    col = _amount_col(df)
    if col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


# ── Constants

LARGE_AMOUNT_THRESHOLD = 3_000          # Rule 1 – large amount
ROUND_AMOUNTS          = {999, 950, 1_999, 1_950, 9_999, 9_950}  # Rule 1 – round amounts
SUSPICIOUS_IBANS: set  = set()          # Rule 2 – extend at config time if needed
STRUCTURING_MIN        = 850            # Rule 3 – structuring band low
STRUCTURING_MAX        = 950            # Rule 3 – structuring band high
STRUCTURING_MIN_COUNT  = 3             # Rule 3 – min txs in 24h
NIGHT_START            = 0             # Rule 4 – night hour start (inclusive)
NIGHT_END              = 5             # Rule 4 – night hour end (exclusive)
NIGHT_TYPES            = {"P2P_TRANSFER", "INTERNATIONAL_TRANSFER",
                           "WIRE_TRANSFER", "SWIFT", "SEPA"}
FOREIGN_IP_PREFIX      = "185.230"     # Rule 5 – foreign IP prefix
FOREIGN_IP_AMOUNT      = 2_000         # Rule 5 – extra pts threshold
HIGH_RISK_MCC          = {5541, 5999, 5311}  # Rule 6 – risky MCCs
HIGH_RISK_MCC_AMOUNT   = 1_500         # Rule 6 – MCC amount threshold
BALANCE_RATIO          = 0.80          # Rule 7 – amount vs balance ratio


# ── Individual Rule Functions ─────────────────────────────────────────────────

def check_large_or_round_amount(df: pd.DataFrame) -> Dict:
    """
    Rule 1 – Large or unusual transaction amount.
    +20 pts if any transaction > 3,000
    +15 pts if any transaction is a suspicious round number
    """
    amounts = _safe_amounts(df)
    if amounts.empty:
        return {"rule": "LARGE_OR_ROUND_AMOUNT", "triggered": False, "points": 0,
                "details": "No amount data", "severity": "LOW"}

    pts = 0
    details_parts = []

    large_mask = amounts > LARGE_AMOUNT_THRESHOLD
    large_count = int(large_mask.sum())
    if large_count > 0:
        pts += 20
        details_parts.append(
            f"{large_count} transaction(s) > {LARGE_AMOUNT_THRESHOLD:,} "
            f"(max: {amounts[large_mask].max():,.2f})"
        )

    # Round/suspicious amounts – nearest-integer check
    round_mask = amounts.dropna().apply(lambda x: round(x) in ROUND_AMOUNTS)
    round_count = int(round_mask.sum())
    if round_count > 0:
        pts += 15
        details_parts.append(f"{round_count} suspicious round amounts")

    triggered = pts > 0
    return {
        "rule":      "LARGE_OR_ROUND_AMOUNT",
        "triggered": triggered,
        "points":    pts,
        "details":   " | ".join(details_parts) if details_parts else "No large/round amounts",
        "severity":  "HIGH" if pts >= 30 else "MEDIUM" if triggered else "LOW",
    }


def check_suspicious_iban(df: pd.DataFrame) -> Dict:
    """
    Rule 2 – High-risk IBAN pattern.
    +20 pts if client_iban is in SUSPICIOUS_IBANS
    +15 pts if counterparty_iban is in SUSPICIOUS_IBANS
    """
    if not SUSPICIOUS_IBANS:
        return {"rule": "SUSPICIOUS_IBAN", "triggered": False, "points": 0,
                "details": "No suspicious IBANs configured", "severity": "LOW"}

    pts = 0
    details_parts = []

    if "client_iban" in df.columns:
        hits = df["client_iban"].astype(str).str.upper().isin(
            {i.upper() for i in SUSPICIOUS_IBANS}
        )
        if hits.any():
            pts += 20
            details_parts.append(f"Client IBAN suspicious ({int(hits.sum())} tx)")

    if "counterparty_iban" in df.columns:
        hits_cp = df["counterparty_iban"].astype(str).str.upper().isin(
            {i.upper() for i in SUSPICIOUS_IBANS}
        )
        if hits_cp.any():
            pts += 15
            details_parts.append(f"Counterparty IBAN suspicious ({int(hits_cp.sum())} tx)")

    triggered = pts > 0
    return {
        "rule":      "SUSPICIOUS_IBAN",
        "triggered": triggered,
        "points":    pts,
        "details":   " | ".join(details_parts) if details_parts else "No suspicious IBANs",
        "severity":  "CRITICAL" if pts >= 30 else "HIGH" if triggered else "LOW",
    }


def check_structuring(df: pd.DataFrame) -> Dict:
    """
    Rule 3 – Structured / layered transactions (AML structuring).
    Flag if, within any 24-hour window, the same client_iban has
    ≥ 3 transactions with amounts in [850, 950].
    +25 pts if triggered.
    """
    amounts = _safe_amounts(df)
    if amounts.empty or "timestamp" not in df.columns:
        return {"rule": "STRUCTURING", "triggered": False, "points": 0,
                "details": "Missing amount/timestamp data", "severity": "LOW"}

    iban_col = "client_iban" if "client_iban" in df.columns else None

    band_mask = amounts.between(STRUCTURING_MIN, STRUCTURING_MAX)
    band_df = df[band_mask].copy()
    band_df["_amount"] = amounts[band_mask]

    if band_df.empty:
        return {"rule": "STRUCTURING", "triggered": False, "points": 0,
                "details": f"No transactions in {STRUCTURING_MIN}–{STRUCTURING_MAX} band",
                "severity": "LOW"}

    band_df = band_df.sort_values("timestamp")

    # Slide a 24h window per IBAN (or globally if no IBAN col)
    groups = band_df.groupby(iban_col) if iban_col else [("_all", band_df)]
    max_count = 0
    flagged_iban = None

    for iban_val, grp in groups:
        grp = grp.sort_values("timestamp")
        ts_list = grp["timestamp"].tolist()
        window = timedelta(hours=24)
        for i, start_ts in enumerate(ts_list):
            count = sum(
                1 for ts in ts_list[i:]
                if (ts - start_ts) <= window
            )
            if count > max_count:
                max_count = count
                flagged_iban = iban_val

    triggered = max_count >= STRUCTURING_MIN_COUNT
    pts = 25 if triggered else 0

    return {
        "rule":      "STRUCTURING",
        "triggered": triggered,
        "points":    pts,
        "details":   (
            f"Max {max_count} transactions in {STRUCTURING_MIN}–{STRUCTURING_MAX} "
            f"within 24h (IBAN: {str(flagged_iban)[:20]}…)"
            if triggered else "No structuring pattern detected"
        ),
        "severity":  "CRITICAL" if triggered else "LOW",
    }


def check_night_transactions(df: pd.DataFrame) -> Dict:
    """
    Rule 4 – Unusual hour / night transactions.
    Flag P2P or international transfers between 00:00–05:00.
    +10 pts if triggered.
    """
    if "timestamp" not in df.columns:
        return {"rule": "NIGHT_TRANSACTIONS", "triggered": False, "points": 0,
                "details": "No timestamp data", "severity": "LOW"}

    hours = df["timestamp"].dt.hour
    night_mask = hours.between(NIGHT_START, NIGHT_END - 1, inclusive="both")

    type_col = _type_col(df)
    if type_col in df.columns:
        type_mask = df[type_col].astype(str).str.upper().isin(NIGHT_TYPES)
        flagged_mask = night_mask & type_mask
    else:
        flagged_mask = night_mask

    night_count = int(flagged_mask.sum())
    triggered = night_count > 0
    pts = 10 if triggered else 0

    return {
        "rule":      "NIGHT_TRANSACTIONS",
        "triggered": triggered,
        "points":    pts,
        "details":   (
            f"{night_count} suspicious transfer(s) between "
            f"{NIGHT_START:02d}:00–{NIGHT_END:02d}:00"
            if triggered else "No unusual night transactions"
        ),
        "severity":  "MEDIUM" if triggered else "LOW",
    }


def check_foreign_ip(df: pd.DataFrame) -> Dict:
    """
    Rule 5 – High-risk IP / geography.
    +15 pts if IP starts with 185.230
    +10 extra pts if that transaction also has amount > 2,000
    """
    if "ip_address" not in df.columns:
        return {"rule": "FOREIGN_IP", "triggered": False, "points": 0,
                "details": "No IP address column", "severity": "LOW"}

    foreign_mask = df["ip_address"].astype(str).str.startswith(FOREIGN_IP_PREFIX)
    foreign_count = int(foreign_mask.sum())

    if foreign_count == 0:
        return {"rule": "FOREIGN_IP", "triggered": False, "points": 0,
                "details": f"No IPs matching {FOREIGN_IP_PREFIX}.*", "severity": "LOW"}

    pts = 15
    amounts = _safe_amounts(df)
    extra_count = 0

    if not amounts.empty:
        high_amount_mask = amounts > FOREIGN_IP_AMOUNT
        extra_count = int((foreign_mask & high_amount_mask).sum())
        if extra_count > 0:
            pts += 10

    details = (
        f"{foreign_count} foreign IP ({FOREIGN_IP_PREFIX}.x) transaction(s)"
        + (f", {extra_count} also > {FOREIGN_IP_AMOUNT:,}" if extra_count > 0 else "")
    )

    return {
        "rule":      "FOREIGN_IP",
        "triggered": True,
        "points":    pts,
        "details":   details,
        "severity":  "HIGH" if pts >= 25 else "MEDIUM",
    }


def check_high_risk_mcc(df: pd.DataFrame) -> Dict:
    """
    Rule 6 – Unusual merchant / MCC.
    Flag transactions in MCC {5541, 5999, 5311} with amount > 1,500.
    +10 pts if triggered.
    """
    if "merchant_mcc" not in df.columns:
        return {"rule": "HIGH_RISK_MCC", "triggered": False, "points": 0,
                "details": "No MCC column", "severity": "LOW"}

    amounts = _safe_amounts(df)
    mcc_vals = pd.to_numeric(df["merchant_mcc"], errors="coerce")

    mcc_mask = mcc_vals.isin(HIGH_RISK_MCC)
    amount_mask = amounts > HIGH_RISK_MCC_AMOUNT if not amounts.empty else pd.Series(False, index=df.index)
    flagged = mcc_mask & amount_mask

    flagged_count = int(flagged.sum())
    triggered = flagged_count > 0
    pts = 10 if triggered else 0

    return {
        "rule":      "HIGH_RISK_MCC",
        "triggered": triggered,
        "points":    pts,
        "details":   (
            f"{flagged_count} high-risk MCC transaction(s) "
            f"(MCCs {sorted(HIGH_RISK_MCC)}) with amount > {HIGH_RISK_MCC_AMOUNT:,}"
            if triggered else "No high-risk MCC pattern"
        ),
        "severity":  "MEDIUM" if triggered else "LOW",
    }


def check_balance_ratio(df: pd.DataFrame) -> Dict:
    """
    Rule 7 – Low-balance vs high-value transaction.
    Flag if transaction_amount > 80% of account_currentbalance.
    +10 pts if triggered.
    """
    amounts = _safe_amounts(df)
    if amounts.empty or "account_currentbalance" not in df.columns:
        return {"rule": "BALANCE_RATIO", "triggered": False, "points": 0,
                "details": "Missing amount or balance data", "severity": "LOW"}

    balances = pd.to_numeric(df["account_currentbalance"], errors="coerce")
    valid = balances > 0
    flagged = valid & (amounts > BALANCE_RATIO * balances)
    flagged_count = int(flagged.sum())
    triggered = flagged_count > 0
    pts = 10 if triggered else 0

    return {
        "rule":      "BALANCE_RATIO",
        "triggered": triggered,
        "points":    pts,
        "details":   (
            f"{flagged_count} transaction(s) exceed {int(BALANCE_RATIO*100)}% of account balance"
            if triggered else "No balance-drain pattern detected"
        ),
        "severity":  "HIGH" if triggered else "LOW",
    }


def check_repeated_alerts(df: pd.DataFrame) -> Dict:
    """
    Rule 8 – Repeated alerts on the same IBAN.
    Not applicable: the current CSV has no 'Alert Status & Type' column.
    Returns 0 pts / not triggered (placeholder kept for completeness).
    """
    return {
        "rule":      "REPEATED_ALERTS",
        "triggered": False,
        "points":    0,
        "details":   "No alert-status column in current dataset (rule skipped)",
        "severity":  "LOW",
    }


# ── Master Rule Runner ────────────────────────────────────────────────────────

ALL_RULES = [
    check_large_or_round_amount,
    check_suspicious_iban,
    check_structuring,
    check_night_transactions,
    check_foreign_ip,
    check_high_risk_mcc,
    check_balance_ratio,
    check_repeated_alerts,
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
                "rule":      rule_fn.__name__,
                "triggered": False,
                "points":    0,
                "details":   f"Error: {str(e)}",
                "severity":  "LOW",
            })
    return results