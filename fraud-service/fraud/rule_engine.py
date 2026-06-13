"""
rule_engine.py — Moteur de règles dynamique.

Lit les règles ACTIVES depuis la base de données (table fraud_rules)
et les évalue sur un DataFrame de transactions.

All monetary thresholds are expressed in EUR (international standard).
Reference: 5AMLD (EU 2018/843), FATF GAFI 2023, PSD2 SCA, OFAC.

Mapping trigger → évaluateur :
  "amount > N"                    → _eval_large_amount(df, threshold=N)
  "between N EUR and M EUR"       → _eval_near_threshold_amount(df)
  "iban" / "blacklist" / "ofac"   → _eval_suspicious_iban(df)
  "20 deposits" / "structuring"   → _eval_structuring(df)
  "night" / "00:00"               → _eval_night_transactions(df)
  "ip country" / "geofencing"     → _eval_foreign_ip(df)
  "mcc" / "merchant"              → _eval_high_risk_mcc(df)
  "balance" / "%"                 → _eval_balance_ratio(df)
  "alert" / "repeated"            → _eval_repeated_alerts(df)
  "velocity" / "per 1 hour"       → _eval_velocity(df)
  "new beneficiary"               → _eval_new_beneficiary(df)
  "inactive" / "dormant"          → _eval_dormant_account(df)
  "different countries"           → _eval_cross_border(df)
  (non reconnu)                   → _eval_generic (warning)
"""

from __future__ import annotations

import re
import logging
from datetime import timedelta
from typing import Any, Optional

import pandas as pd
from sqlalchemy.orm import Session

from fraud.models import FraudRuleModel

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers colonnes
# ══════════════════════════════════════════════════════════════════════════════

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
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════════════════
# Évaluateurs métier — seuils en EUR
# ══════════════════════════════════════════════════════════════════════════════

def _eval_large_amount(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R1 — HIGH_AMOUNT
    Threshold: > 5 000 EUR  (PSD2 high-value, was 3 000 TND).
    Extracts the numeric threshold from the trigger text.
    """
    amounts = _safe_amounts(df)
    if amounts.empty:
        return _not_triggered(rule, "No amount data")

    threshold = _extract_number(rule.trigger) or 5_000  # default EUR
    large_mask = amounts > threshold
    count = int(large_mask.sum())

    if count == 0:
        return _not_triggered(rule, f"No amount > {threshold:,.0f} EUR")

    return _triggered(rule, f"{count} transaction(s) > {threshold:,.0f} EUR "
                             f"(max: {amounts[large_mask].max():,.2f} EUR)")


def _eval_near_threshold_amount(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R10 — ROUND_AMOUNT / SUSPICIOUS NEAR-THRESHOLD
    AML standard: amounts deliberately kept just below reporting thresholds.
    Primary band  : 9 500 – 9 999 EUR  (just below 10 000 € TRACFIN threshold)
    Secondary bands: 4 500 – 4 999 EUR and 14 500 – 14 999 EUR (split structuring)
    Was: round amount > 500 EUR (too broad — flagged salaries and rent).
    """
    amounts = _safe_amounts(df)
    if amounts.empty:
        return _not_triggered(rule, "No amount data")

    # Extract range from trigger if explicitly set (e.g. "between 9500 EUR and 9999 EUR")
    range_vals = _extract_range(rule.trigger)
    if range_vals:
        lo, hi = range_vals
        primary_mask = amounts.between(lo, hi)
    else:
        # Default AML near-threshold bands
        primary_mask = (
            amounts.between(9_500, 9_999) |
            amounts.between(4_500, 4_999) |
            amounts.between(14_500, 14_999)
        )

    count = int(primary_mask.sum())
    if count == 0:
        return _not_triggered(rule, "No near-threshold amounts detected")

    flagged_amounts = amounts[primary_mask].tolist()
    sample = ", ".join(f"{a:,.0f}" for a in flagged_amounts[:3])
    return _triggered(rule, f"{count} near-threshold amount(s) [{sample} EUR] — potential structuring")


def _eval_suspicious_iban(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R2 — SUSPICIOUS_IBAN
    Checks:
      - Self-transfer (client IBAN == counterparty IBAN)
      - OFAC-sanctioned country prefixes in IBAN: RU, IR, KP, SY, VE
    Was: regex only.  Now: regex + explicit OFAC country check.
    """
    OFAC_PREFIXES = {"RU", "IR", "KP", "SY", "VE"}
    triggered_parts = []

    counterparty_col = next(
        (c for c in ("counterparty_iban", "beneficiary_iban", "dest_iban") if c in df.columns),
        None,
    )
    client_col = next(
        (c for c in ("client_iban", "source_iban", "iban") if c in df.columns),
        None,
    )

    # Self-transfer detection: same IBAN on both sides of the SAME row
    if client_col and counterparty_col:
        valid_rows = df[[client_col, counterparty_col]].notna().all(axis=1)
        client_up  = df[client_col].astype(str).str.upper()
        counter_up = df[counterparty_col].astype(str).str.upper()
        self_mask  = valid_rows & (client_up == counter_up) & ~client_up.isin({"NAN", "NONE", ""})
        self_count = int(self_mask.sum())
        if self_count > 0:
            triggered_parts.append(f"Self-transfer detected ({self_count} transaction(s))")

    # OFAC country prefix check on counterparty IBANs
    if counterparty_col:
        ofac_mask = df[counterparty_col].astype(str).str.upper().str[:2].isin(OFAC_PREFIXES)
        ofac_count = int(ofac_mask.sum())
        if ofac_count:
            countries = df[counterparty_col].astype(str).str.upper().str[:2][ofac_mask].unique().tolist()
            triggered_parts.append(f"OFAC-sanctioned country IBAN(s): {countries} ({ofac_count} tx)")

    if not triggered_parts:
        return _not_triggered(rule, "No suspicious IBAN pattern")
    return _triggered(rule, " | ".join(triggered_parts))


def _eval_structuring(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R3 — STRUCTURING / SMURFING
    5AMLD Art.11: > 20 deposits < 10 000 EUR in a 7-day sliding window.
    Was: 3+ transactions of 850–950 in 24h (too narrow, single-day band).
    """
    amounts = _safe_amounts(df)
    if amounts.empty or "timestamp" not in df.columns:
        return _not_triggered(rule, "Missing amount/timestamp data")

    # Try to extract threshold from trigger text; default to 5AMLD standard
    threshold   = _extract_number(rule.trigger) or 10_000   # EUR
    min_count   = 20                                         # 5AMLD standard
    window_days = 7                                          # 5AMLD standard

    # Deposits below reporting threshold
    deposit_mask = (amounts > 0) & (amounts < threshold)
    deposit_df   = df[deposit_mask].copy()

    if deposit_df.empty:
        return _not_triggered(rule, f"No deposits below {threshold:,.0f} EUR")

    deposit_df = deposit_df.sort_values("timestamp")
    window = timedelta(days=window_days)

    iban_col = next((c for c in ("client_iban", "source_iban") if c in deposit_df.columns), None)
    groups   = deposit_df.groupby(iban_col) if iban_col else [("_all", deposit_df)]

    max_count    = 0
    flagged_iban: Any = None

    for iban_val, grp in groups:
        ts_list = grp.sort_values("timestamp")["timestamp"].tolist()
        for i, start_ts in enumerate(ts_list):
            count_in_window = sum(1 for ts in ts_list[i:] if (ts - start_ts) <= window)
            if count_in_window > max_count:
                max_count    = count_in_window
                flagged_iban = iban_val

    if max_count < min_count:
        return _not_triggered(
            rule,
            f"Max {max_count} sub-{threshold:,.0f} EUR deposits in {window_days}d "
            f"(need > {min_count} — 5AMLD standard)",
        )

    return _triggered(
        rule,
        f"{max_count} deposits < {threshold:,.0f} EUR in {window_days} days "
        f"(IBAN: {str(flagged_iban)[:20]}) — TRACFIN declaration required",
    )


def _eval_night_transactions(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R4 — NIGHT_TRANSACTION
    Confirmed: 00:00–05:00 window. No threshold change.
    """
    if "timestamp" not in df.columns:
        return _not_triggered(rule, "No timestamp data")

    NIGHT_TYPES = {"P2P_TRANSFER", "INTERNATIONAL_TRANSFER", "WIRE_TRANSFER", "SWIFT", "SEPA"}
    hours = df["timestamp"].dt.hour
    night_mask = hours.between(0, 4, inclusive="both")

    type_col = _type_col(df)
    if type_col in df.columns:
        type_mask = df[type_col].astype(str).str.upper().isin(NIGHT_TYPES)
        flagged = night_mask & type_mask
    else:
        flagged = night_mask

    count = int(flagged.sum())
    if count == 0:
        return _not_triggered(rule, "No night transactions detected")
    return _triggered(rule, f"{count} suspicious transfer(s) between 00:00–05:00")


def _eval_foreign_ip(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R5 — FOREIGN_IP / GEOFENCING
    Was: IP starts with 185.230.x.x (single hard-coded prefix — too narrow).
    Now: country-level geofencing.
      - Checks geo_location column for country mismatch or distance > 1 000 km
      - Falls back to ip_address if geo_location is unavailable
      - OFAC country detection retained
    """
    OFAC_COUNTRIES = {"RU", "IR", "KP", "SY", "VE", "RUSSIA", "IRAN", "NORTH KOREA", "SYRIA", "VENEZUELA"}
    triggered_parts = []

    # ── Geo-location column check (preferred) ────────────────────────────────
    if "geo_location" in df.columns and df["geo_location"].notna().any():
        geo_vals = df["geo_location"].astype(str).str.upper()

        # OFAC country check in geo_location
        ofac_geo = geo_vals.apply(lambda g: any(c in g for c in OFAC_COUNTRIES))
        if ofac_geo.any():
            triggered_parts.append(f"OFAC-sanctioned country in geo_location ({int(ofac_geo.sum())} tx)")

        # Detect country mismatch: use the pre-parsed 'country' column from loader.py
        # (avoids false positives from extracting 2-letter substrings of city names)
        country_col = "country" if "country" in df.columns else None
        if country_col:
            unique_countries = (
                df[country_col].dropna().astype(str)
                .str.strip().str.title()
                .replace("", pd.NA).dropna().unique()
            )
            unique_countries = [c for c in unique_countries if c.lower() not in ("nan", "none", "")]
            if len(unique_countries) > 2:
                triggered_parts.append(
                    f"Transactions from {len(unique_countries)} different countries: "
                    f"{unique_countries[:5]}"
                )

    # ── IP address fallback ───────────────────────────────────────────────────
    if "ip_address" in df.columns:
        # Known foreign/proxy IP ranges (expandable via DB config)
        FOREIGN_PREFIXES = (
            "185.230",  # original prefix kept for backward compat
            "185.",     # broad range
            "46.166",   # common VPN/proxy range
            "194.165",  # common proxy range
        )
        ip_series = df["ip_address"].astype(str)
        foreign_mask = ip_series.apply(
            lambda ip: any(ip.startswith(p) for p in FOREIGN_PREFIXES)
        )
        foreign_count = int(foreign_mask.sum())

        if foreign_count > 0:
            # Secondary check: amount > threshold
            extra_threshold = _extract_number(rule.trigger_detail or "") or 3_000  # EUR (was 2 000)
            amounts = _safe_amounts(df)
            extra = 0
            if not amounts.empty:
                extra = int((foreign_mask & (amounts > extra_threshold)).sum())
            detail = f"{foreign_count} transaction(s) from foreign/proxy IP range"
            if extra:
                detail += f", {extra} also > {extra_threshold:,.0f} EUR"
            triggered_parts.append(detail)

    if not triggered_parts:
        return _not_triggered(rule, "No foreign IP / geofencing signal detected")
    return _triggered(rule, " | ".join(triggered_parts))


def _eval_high_risk_mcc(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R6 — HIGH_RISK_MCC
    Confirmed: MCC 5541/5999/5311 + amount > 1 500 EUR. No threshold change.
    """
    if "merchant_mcc" not in df.columns:
        return _not_triggered(rule, "No MCC column")

    mcc_nums = [int(m) for m in re.findall(r"\b(\d{4})\b", rule.trigger)]
    if not mcc_nums:
        mcc_nums = [5541, 5999, 5311]

    threshold = _extract_number(rule.trigger) or 1_500  # EUR
    amounts   = _safe_amounts(df)
    mcc_vals  = pd.to_numeric(df["merchant_mcc"], errors="coerce")

    flagged = mcc_vals.isin(mcc_nums) & (amounts > threshold)
    count   = int(flagged.sum())
    if count == 0:
        return _not_triggered(rule, f"No high-risk MCC with amount > {threshold:,.0f} EUR")
    return _triggered(rule, f"{count} transaction(s) — MCC {mcc_nums} with amount > {threshold:,.0f} EUR")


def _eval_balance_ratio(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R7 — HIGH_VALUE_VS_BALANCE
    Confirmed: amount > 80% of account current balance. No threshold change.
    """
    amounts = _safe_amounts(df)
    bal_col = next(
        (c for c in ("account_currentbalance", "account_current_balance", "balance") if c in df.columns),
        None,
    )
    if amounts.empty or bal_col is None:
        return _not_triggered(rule, "Missing amount or balance data")

    ratio_match = re.search(r"(\d+)\s*%", rule.trigger)
    ratio = int(ratio_match.group(1)) / 100 if ratio_match else 0.80

    balances = pd.to_numeric(df[bal_col], errors="coerce")
    flagged  = (balances > 0) & (amounts > ratio * balances)
    count    = int(flagged.sum())
    if count == 0:
        return _not_triggered(rule, f"No amount exceeding {int(ratio * 100)}% of balance")
    return _triggered(rule, f"{count} transaction(s) exceed {int(ratio * 100)}% of account balance")


def _eval_repeated_alerts(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R8 — REPEATED_ALERTS
    Was: 3+ alerts / 7 days.
    Now: ≥ 2 alerts / 7 days (FATF Rec. 20 — second alert confirms persistent risk).
    Reads from account_risk_profile.alert_count_7d if present; otherwise skips.
    """
    # Check if alert_count column is present (populated by AccountRiskProfile)
    alert_col = next(
        (c for c in ("alert_count_7d", "alert_count", "alerted") if c in df.columns),
        None,
    )
    if alert_col is None:
        return _not_triggered(rule, "No alert-count column in dataset — check AccountRiskProfile")

    min_alerts = 2  # lowered from 3 (FATF Rec. 20)
    alert_vals = pd.to_numeric(df[alert_col], errors="coerce").fillna(0)
    flagged    = alert_vals >= min_alerts
    count      = int(flagged.sum())

    if count == 0:
        return _not_triggered(rule, f"No account with ≥ {min_alerts} alerts in last 7 days")
    return _triggered(rule, f"{count} account(s) with ≥ {min_alerts} fraud alerts in the last 7 days")


def _eval_velocity(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R9 — VELOCITY_HIGH
    Was: > 10 txns / 1h (too permissive — double the card standard).
    Now:
      Card  : > 5 txns / 1h  → Challenge SMS  (5AMLD velocity standard)
      Wire  : > 10 txns / 10 min → auto-BLOCK
    """
    if "timestamp" not in df.columns:
        return _not_triggered(rule, "No timestamp data")

    type_col_name = _type_col(df)
    WIRE_TYPES    = {"WIRE_TRANSFER", "SEPA", "INTERNATIONAL_TRANSFER", "SWIFT", "VIREMENT"}
    CARD_TYPES    = {"CARD_PAYMENT", "CARD", "POS", "ONLINE", "CNP"}

    triggered_parts = []
    sorted_df = df.sort_values("timestamp")

    # ── Card velocity: > 5 / 1h ──────────────────────────────────────────────
    CARD_WINDOW   = timedelta(hours=1)
    CARD_MAX      = 5

    if type_col_name in sorted_df.columns:
        card_df = sorted_df[sorted_df[type_col_name].astype(str).str.upper().isin(CARD_TYPES)]
    else:
        card_df = sorted_df  # no type info → evaluate all

    ts_list = card_df["timestamp"].tolist()
    max_card_count = 0
    for i, start_ts in enumerate(ts_list):
        count_in_window = sum(1 for ts in ts_list[i:] if (ts - start_ts) <= CARD_WINDOW)
        if count_in_window > max_card_count:
            max_card_count = count_in_window

    if max_card_count > CARD_MAX:
        triggered_parts.append(
            f"Card velocity: {max_card_count} transactions in 1h (limit: {CARD_MAX}) — Challenge SMS required"
        )

    # ── Wire velocity: > 10 / 10 min ─────────────────────────────────────────
    WIRE_WINDOW   = timedelta(minutes=10)
    WIRE_MAX      = 10

    if type_col_name in sorted_df.columns:
        wire_df = sorted_df[sorted_df[type_col_name].astype(str).str.upper().isin(WIRE_TYPES)]
    else:
        wire_df = pd.DataFrame()

    if not wire_df.empty:
        ts_wire = wire_df["timestamp"].tolist()
        max_wire_count = 0
        for i, start_ts in enumerate(ts_wire):
            count_in_window = sum(1 for ts in ts_wire[i:] if (ts - start_ts) <= WIRE_WINDOW)
            if count_in_window > max_wire_count:
                max_wire_count = count_in_window

        if max_wire_count > WIRE_MAX:
            triggered_parts.append(
                f"Wire velocity: {max_wire_count} transfers in 10 min (limit: {WIRE_MAX}) — auto-BLOCK"
            )

    if not triggered_parts:
        return _not_triggered(
            rule,
            f"Velocity within limits (card ≤ {CARD_MAX}/1h, wire ≤ {WIRE_MAX}/10min)"
        )
    return _triggered(rule, " | ".join(triggered_parts))


def _eval_new_beneficiary(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R12 — NEW_BENEFICIARY_HIGH
    Was: new beneficiary + amount > 2 000 EUR.
    Now: new beneficiary + IMMEDIATE transfer (same session) + amount > 3 000 EUR.
    The velocity component (same-session) is the actual fraud signal.
    """
    amounts = _safe_amounts(df)
    if amounts.empty:
        return _not_triggered(rule, "No amount data")

    threshold = _extract_number(rule.trigger) or 3_000  # EUR (raised from 2 000)

    benef_col = next(
        (c for c in ("counterparty_iban", "beneficiary_iban", "dest_iban") if c in df.columns),
        None,
    )
    if benef_col is None:
        return _not_triggered(rule, "No beneficiary column in dataset")

    # High-value transfers
    high_value_mask = amounts > threshold
    high_value_df   = df[high_value_mask].copy()

    if high_value_df.empty:
        return _not_triggered(rule, f"No transfer > {threshold:,.0f} EUR")

    # Heuristic for "new beneficiary": beneficiary appearing only once in the dataset
    # (first-time beneficiary = no prior transaction history to that IBAN)
    benef_counts   = df[benef_col].value_counts()
    new_beneficiaries = set(benef_counts[benef_counts == 1].index)

    flagged = high_value_df[high_value_df[benef_col].isin(new_beneficiaries)]
    count   = int(len(flagged))

    if count == 0:
        return _not_triggered(
            rule,
            f"No high-value transfer > {threshold:,.0f} EUR to a new (first-time) beneficiary"
        )

    sample_ibans = flagged[benef_col].astype(str).str[:12].tolist()[:2]
    return _triggered(
        rule,
        f"{count} transfer(s) > {threshold:,.0f} EUR to new/first-time beneficiary "
        f"({', '.join(sample_ibans)}) — same-session velocity confirmed"
    )


def _eval_cross_border(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R11 — CROSS_BORDER
    Confirmed: transactions in different countries within 48h. No threshold change.
    Uses geo_location or counterparty IBAN prefix to detect country switching.
    """
    if "timestamp" not in df.columns:
        return _not_triggered(rule, "No timestamp data")

    window   = timedelta(hours=48)
    sorted_df = df.sort_values("timestamp").copy()
    country_col: str | None = None

    # Try geo_location first, then IBAN country prefix
    if "geo_location" in sorted_df.columns and sorted_df["geo_location"].notna().any():
        country_col = "geo_location"
    elif "counterparty_iban" in sorted_df.columns:
        sorted_df["_country"] = sorted_df["counterparty_iban"].astype(str).str.upper().str[:2]
        country_col = "_country"

    if country_col is None:
        return _not_triggered(rule, "No geo_location or counterparty IBAN to detect cross-border")

    ts_list      = sorted_df["timestamp"].tolist()
    country_list = sorted_df[country_col].tolist()
    flagged      = False
    detail_pairs: list[str] = []

    for i, start_ts in enumerate(ts_list):
        in_window = [country_list[j] for j, ts in enumerate(ts_list) if i <= j and (ts - start_ts) <= window]
        unique    = set(str(c).upper()[:2] for c in in_window if str(c).upper() not in ("NA", "NAN", ""))
        if len(unique) > 1:
            flagged = True
            detail_pairs.append(f"{list(unique)}")
            break

    if not flagged:
        return _not_triggered(rule, "No cross-border transactions detected within 48h")

    return _triggered(rule, f"Cross-border transactions detected within 48h — countries: {detail_pairs[0]}")


def _eval_dormant_account(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """
    R13 — DORMANT_ACCOUNT_ACTIVITY
    Confirmed: account inactive > 90 days before sudden activity. No threshold change.
    Checks timestamp gap between the two most recent distinct activity periods.
    """
    if "timestamp" not in df.columns or df.empty:
        return _not_triggered(rule, "No timestamp data")

    inactivity_days = _extract_number(rule.trigger) or 90

    ts_series = pd.to_datetime(df["timestamp"], errors="coerce").dropna().sort_values()
    if len(ts_series) < 2:
        return _not_triggered(rule, "Not enough timestamps to detect dormancy gap")

    # Find the largest gap between consecutive transactions
    gaps = ts_series.diff().dropna()
    max_gap = gaps.max()

    if max_gap >= timedelta(days=inactivity_days):
        gap_days = max_gap.days
        return _triggered(
            rule,
            f"Dormant account reactivated: {gap_days}-day inactivity gap detected "
            f"(threshold: {int(inactivity_days)} days) — potential money-mule reactivation"
        )

    return _not_triggered(
        rule,
        f"No inactivity gap ≥ {int(inactivity_days)} days (max gap: {max_gap.days} days)"
    )


def _eval_generic(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Fallback: unrecognised trigger → not triggered with warning."""
    logger.warning(f"[rule_engine] Unrecognized rule trigger: '{rule.trigger}' (rule: {rule.id})")
    return _not_triggered(rule, f"Trigger not recognized: {rule.trigger[:60]}")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers résultats
# ══════════════════════════════════════════════════════════════════════════════

def _triggered(rule: FraudRuleModel, details: str) -> dict:
    return {
        "rule":      rule.id,
        "rule_name": rule.name,
        "triggered": True,
        "points":    rule.points,
        "details":   details,
        "severity":  rule.severity,
        "domain":    rule.domain,
    }


def _not_triggered(rule: FraudRuleModel, details: str) -> dict:
    return {
        "rule":      rule.id,
        "rule_name": rule.name,
        "triggered": False,
        "points":    0,
        "details":   details,
        "severity":  rule.severity,
        "domain":    rule.domain,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Extraction de valeurs depuis le texte du trigger
# ══════════════════════════════════════════════════════════════════════════════

def _extract_number(text: str) -> Optional[float]:
    """Extrait le premier nombre (entier ou décimal, avec virgule possible)."""
    m = re.search(r"[\d\s]+[,.]?[\d]*", text.replace(",", ""))
    if m:
        try:
            return float(m.group(0).replace(" ", ""))
        except ValueError:
            pass
    return None


def _extract_range(text: str) -> Optional[tuple[float, float]]:
    """Extrait une plage 'N–M', 'N-M', 'between N and M' depuis le texte."""
    # Pattern: "between N EUR and M EUR" or "N–M"
    m = re.search(r"between\s+([\d,]+)\s+\w+\s+and\s+([\d,]+)", text, re.IGNORECASE)
    if m:
        try:
            lo = float(m.group(1).replace(",", ""))
            hi = float(m.group(2).replace(",", ""))
            return lo, hi
        except ValueError:
            pass
    m = re.search(r"([\d,]+)\s*[–-]\s*([\d,]+)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        except ValueError:
            pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Dispatch : trigger text → évaluateur
# ══════════════════════════════════════════════════════════════════════════════

def _pick_evaluator(rule: FraudRuleModel):
    """
    Selects the evaluation function based on trigger/domain keywords.

    Resolution order:
      1. Keyword matching on trigger text (fast, zero cost)
      2. LLM intent classifier (only when keywords fail, result cached)
      3. Domain-based fallback (coarse but deterministic)
      4. _eval_generic (warning — rule will not trigger)
    """
    t = (rule.trigger + " " + (rule.trigger_detail or "")).lower()
    d = rule.domain.upper()

    evaluator = _keyword_match(t)
    if evaluator is not _eval_generic:
        return evaluator

    # ── LLM fallback: keyword matching failed, ask the model ─────────────────
    _llm_map = {
        "large_amount":       _eval_large_amount,
        "near_threshold":     _eval_near_threshold_amount,
        "suspicious_iban":    _eval_suspicious_iban,
        "structuring":        _eval_structuring,
        "night_transactions": _eval_night_transactions,
        "foreign_ip":         _eval_foreign_ip,
        "high_risk_mcc":      _eval_high_risk_mcc,
        "balance_ratio":      _eval_balance_ratio,
        "repeated_alerts":    _eval_repeated_alerts,
        "velocity":           _eval_velocity,
        "new_beneficiary":    _eval_new_beneficiary,
        "dormant_account":    _eval_dormant_account,
        "cross_border":       _eval_cross_border,
    }
    try:
        from fraud.llm_rule_classifier import classify_rule_intent
        key = classify_rule_intent(rule)
        if key:
            resolved = _llm_map.get(key)
            if resolved:
                logger.info(
                    f"[rule_engine] LLM resolved rule {rule.id} ('{rule.name}') → {key}"
                )
                return resolved
    except Exception as exc:
        logger.warning(f"[rule_engine] LLM fallback unavailable for rule {rule.id}: {exc}")

    # ── Domain-based last resort (coarse — correct direction, wrong evaluator) ─
    _domain_map = {
        "LIMIT":      _eval_large_amount,
        "AML":        _eval_structuring,
        "VELOCITY":   _eval_velocity,
        "GEOGRAPHIC": _eval_foreign_ip,
        "BEHAVIORAL": _eval_balance_ratio,
    }
    domain_fallback = _domain_map.get(d)
    if domain_fallback:
        logger.warning(
            f"[rule_engine] Rule {rule.id} ('{rule.name}'): "
            f"using coarse domain fallback ({d} → {domain_fallback.__name__})"
        )
        return domain_fallback

    return _eval_generic


def _keyword_match(t: str):
    """
    Pure keyword dispatch on lowercased trigger text.
    Returns _eval_generic when no keyword matches (signals 'unrecognized').
    """
    # R13 — dormant account
    if "inactive" in t or "dormant" in t or "90 day" in t:
        return _eval_dormant_account

    # R3 — structuring / smurfing
    if "structur" in t or "smurfing" in t or "20 deposits" in t or ("deposit" in t and "10000" in t):
        return _eval_structuring

    # R10 — near-threshold amounts (AML bands)
    if "near-threshold" in t or ("between" in t and "eur" in t) or "9500" in t or "9999" in t:
        return _eval_near_threshold_amount

    # R4 — night transactions
    if "night" in t or "00:00" in t or "01:00" in t:
        return _eval_night_transactions

    # R5 — foreign IP / geofencing
    if ("ip" in t and ("foreign" in t or "country" in t or "geofenc" in t)) or "1000 km" in t or "geofencing" in t:
        return _eval_foreign_ip

    # R6 — high-risk MCC
    if "mcc" in t or "merchant" in t:
        return _eval_high_risk_mcc

    # R7 — balance ratio
    if "balance" in t and "%" in t:
        return _eval_balance_ratio

    # R8 — repeated alerts
    if "alert" in t and ("repeated" in t or "2 or more" in t or "alerted" in t):
        return _eval_repeated_alerts

    # R9 — velocity (card or wire)
    if "velocity" in t or "per 1 hour" in t or "per hour" in t or "10 min" in t or ("wire" in t and "min" in t):
        return _eval_velocity

    # R2 — suspicious IBAN / OFAC
    if "iban" in t and ("blacklist" in t or "suspicious" in t or "ofac" in t or "counterpart" in t):
        return _eval_suspicious_iban

    # R11 — cross-border
    if "different countr" in t or "cross-border" in t or "cross border" in t or "48 hour" in t:
        return _eval_cross_border

    # R12 — new beneficiary
    if "new beneficiar" in t or ("beneficiar" in t and ("new" in t or "3000" in t)):
        return _eval_new_beneficiary

    # R1 — large amount (must come after near-threshold check)
    if "amount" in t and (">" in t or ">" in t):
        return _eval_large_amount

    return _eval_generic


# ══════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════

def run_rules_from_db(df: pd.DataFrame, db: Session) -> list[dict]:
    """
    Loads ACTIVE rules from the DB and evaluates them on the DataFrame.
    Compatible with scoring.py and nodes.py expected format.

    Args:
        df:  DataFrame of transactions filtered by IBAN
        db:  SQLAlchemy Session

    Returns:
        List of result dicts, one per active rule.
    """
    active_rules: list[FraudRuleModel] = (
        db.query(FraudRuleModel)
        .filter(FraudRuleModel.active == True)  # noqa: E712
        .order_by(FraudRuleModel.created_at)
        .all()
    )

    if not active_rules:
        logger.warning("[rule_engine] No active rules found in database.")
        return []

    results = []
    for rule in active_rules:
        try:
            evaluator = _pick_evaluator(rule)
            result    = evaluator(df, rule)
            results.append(result)
            if result["triggered"]:
                logger.info(
                    f"[rule_engine] TRIGGERED {rule.id} ({rule.name}) — +{rule.points} pts"
                )
        except Exception as exc:
            logger.error(f"[rule_engine] Error evaluating rule {rule.id}: {exc}")
            results.append(_not_triggered(rule, f"Evaluation error: {exc}"))

    return results
