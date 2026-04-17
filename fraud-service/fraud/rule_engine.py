"""
rule_engine.py — Moteur de règles dynamique.

Lit les règles ACTIVES depuis la base de données (table fraud_rules)
et les évalue sur un DataFrame de transactions.

Chaque règle DB contient : name, domain, trigger, points, severity, active.
Le moteur mappe le champ `trigger` vers une fonction d'évaluation Python
selon le domaine et les mots-clés détectés dans le trigger.

Mapping trigger → évaluateur :
  "amount > N"              → check_large_amount(df, threshold=N)
  "round"                   → check_round_amounts(df)
  "iban"                    → check_suspicious_iban(df)
  "structuring" / "850"     → check_structuring(df)
  "night" / "00:00"         → check_night_transactions(df)
  "ip" / "185.230"          → check_foreign_ip(df)
  "mcc"                     → check_high_risk_mcc(df)
  "balance" / "80%"         → check_balance_ratio(df)
  "alert"                   → check_repeated_alerts(df)
  (non reconnu)             → évaluateur générique basé sur le trigger text
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
# Évaluateurs métier (indépendants des points — les points viennent de la DB)
# ══════════════════════════════════════════════════════════════════════════════

def _eval_large_amount(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Amount > seuil extrait du trigger (ex: 'Amount > 3,000 TND')."""
    amounts = _safe_amounts(df)
    if amounts.empty:
        return _not_triggered(rule, "No amount data")

    # Extraire le seuil numérique du trigger
    threshold = _extract_number(rule.trigger) or 3_000
    large_mask = amounts > threshold
    count = int(large_mask.sum())

    if count == 0:
        return _not_triggered(rule, f"No amount > {threshold:,.0f}")

    return _triggered(rule, f"{count} transaction(s) > {threshold:,.0f} "
                             f"(max: {amounts[large_mask].max():,.2f})")


def _eval_round_amounts(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Montants ronds suspects."""
    amounts = _safe_amounts(df)
    if amounts.empty:
        return _not_triggered(rule, "No amount data")

    ROUND_SET = {999, 950, 1_000, 1_999, 1_950, 5_000, 9_999, 9_950}
    mask = amounts.dropna().apply(lambda x: round(x) in ROUND_SET)
    count = int(mask.sum())

    if count == 0:
        return _not_triggered(rule, "No suspicious round amounts")
    return _triggered(rule, f"{count} suspicious round amount(s)")


def _eval_suspicious_iban(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """IBAN client/contrepartie dans la blacklist."""
    # La blacklist est dans les détails du trigger ou dans une env var future
    # Pour l'instant : déclenche si l'IBAN client apparaît aussi en contrepartie
    # d'une autre transaction (auto-transfert suspect)
    triggered_parts = []

    if "client_iban" in df.columns and "counterparty_iban" in df.columns:
        client_ibans      = set(df["client_iban"].astype(str).str.upper().dropna())
        counterparty_ibans = set(df["counterparty_iban"].astype(str).str.upper().dropna())
        overlap = client_ibans & counterparty_ibans - {"NAN", "NONE", ""}
        if overlap:
            triggered_parts.append(f"Self-transfer detected ({len(overlap)} IBAN(s))")

    if not triggered_parts:
        return _not_triggered(rule, "No suspicious IBAN pattern")
    return _triggered(rule, " | ".join(triggered_parts))


def _eval_structuring(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """3+ transactions 850–950 dans une fenêtre de 24h (même IBAN)."""
    amounts = _safe_amounts(df)
    if amounts.empty or "timestamp" not in df.columns:
        return _not_triggered(rule, "Missing amount/timestamp data")

    # Extraire les bornes depuis le trigger si présentes
    lo, hi = _extract_range(rule.trigger) or (850, 950)
    min_count = _extract_number(rule.trigger_detail or "") or 3

    band_mask = amounts.between(lo, hi)
    band_df = df[band_mask].copy()
    if band_df.empty:
        return _not_triggered(rule, f"No transactions in {lo}–{hi} band")

    iban_col = "client_iban" if "client_iban" in df.columns else None
    band_df = band_df.sort_values("timestamp")
    groups = band_df.groupby(iban_col) if iban_col else [("_all", band_df)]

    max_count = 0
    flagged_iban: Any = None
    window = timedelta(hours=24)

    for iban_val, grp in groups:
        ts_list = grp.sort_values("timestamp")["timestamp"].tolist()
        for i, start_ts in enumerate(ts_list):
            count = sum(1 for ts in ts_list[i:] if (ts - start_ts) <= window)
            if count > max_count:
                max_count = count
                flagged_iban = iban_val

    if max_count < min_count:
        return _not_triggered(rule, f"Max {max_count} txs in band (need ≥ {min_count})")

    return _triggered(rule, f"{max_count} txs in {lo}–{hi} within 24h "
                             f"(IBAN: {str(flagged_iban)[:20]})")


def _eval_night_transactions(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Transferts P2P/INTL entre 00:00 et 05:00."""
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
        return _not_triggered(rule, "No night transactions")
    return _triggered(rule, f"{count} suspicious transfer(s) between 00:00–05:00")


def _eval_foreign_ip(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """IP étrangère (185.230.x.x) + montant > seuil."""
    if "ip_address" not in df.columns:
        return _not_triggered(rule, "No IP address column")

    # Extraire le préfixe depuis le trigger
    prefix_match = re.search(r"(\d{1,3}\.\d{1,3})", rule.trigger)
    prefix = prefix_match.group(1) if prefix_match else "185.230"

    foreign_mask = df["ip_address"].astype(str).str.startswith(prefix)
    count = int(foreign_mask.sum())
    if count == 0:
        return _not_triggered(rule, f"No IPs matching {prefix}.*")

    # Bonus si montant > seuil secondaire
    extra_threshold = _extract_number(rule.trigger_detail or "") or 2_000
    amounts = _safe_amounts(df)
    extra = 0
    if not amounts.empty:
        extra = int((foreign_mask & (amounts > extra_threshold)).sum())

    detail = f"{count} foreign IP transaction(s)"
    if extra:
        detail += f", {extra} also > {extra_threshold:,.0f}"
    return _triggered(rule, detail)


def _eval_high_risk_mcc(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """MCC à risque élevé avec montant > seuil."""
    if "merchant_mcc" not in df.columns:
        return _not_triggered(rule, "No MCC column")

    # Extraire MCCs du trigger (ex: "MCC 5541/5999/5311")
    mcc_nums = [int(m) for m in re.findall(r"\b(\d{4})\b", rule.trigger)]
    if not mcc_nums:
        mcc_nums = [5541, 5999, 5311]

    threshold = _extract_number(rule.trigger) or 1_500
    amounts = _safe_amounts(df)
    mcc_vals = pd.to_numeric(df["merchant_mcc"], errors="coerce")

    flagged = mcc_vals.isin(mcc_nums) & (amounts > threshold)
    count = int(flagged.sum())
    if count == 0:
        return _not_triggered(rule, f"No high-risk MCC with amount > {threshold:,.0f}")
    return _triggered(rule, f"{count} transaction(s) with risky MCC {mcc_nums} > {threshold:,.0f}")


def _eval_balance_ratio(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Montant > X% du solde."""
    amounts = _safe_amounts(df)
    if amounts.empty or "account_currentbalance" not in df.columns:
        return _not_triggered(rule, "Missing amount or balance data")

    # Extraire le ratio depuis le trigger (ex: "80%")
    ratio_match = re.search(r"(\d+)\s*%", rule.trigger)
    ratio = int(ratio_match.group(1)) / 100 if ratio_match else 0.80

    balances = pd.to_numeric(df["account_currentbalance"], errors="coerce")
    flagged = (balances > 0) & (amounts > ratio * balances)
    count = int(flagged.sum())
    if count == 0:
        return _not_triggered(rule, f"No amount > {int(ratio*100)}% of balance")
    return _triggered(rule, f"{count} transaction(s) exceed {int(ratio*100)}% of account balance")


def _eval_repeated_alerts(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Alertes répétées — colonne absente dans le dataset actuel."""
    return _not_triggered(rule, "No alert-status column in dataset (rule skipped)")


def _eval_generic(df: pd.DataFrame, rule: FraudRuleModel) -> dict:
    """Fallback : règle non reconnue → non déclenchée avec avertissement."""
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
        "points":    rule.points,      # ← points viennent de la DB
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
    """Extrait une plage 'N–M' ou 'N-M' depuis le texte."""
    m = re.search(r"(\d+)\s*[–-]\s*(\d+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Dispatch : trigger text → évaluateur
# ══════════════════════════════════════════════════════════════════════════════

def _pick_evaluator(rule: FraudRuleModel):
    """
    Sélectionne la fonction d'évaluation selon les mots-clés du trigger/domaine.
    Ordre : du plus spécifique au plus générique.
    """
    t = (rule.trigger + " " + (rule.trigger_detail or "")).lower()
    d = rule.domain.upper()

    if "structur" in t or ("850" in t and "950" in t):
        return _eval_structuring
    if "round" in t or "suspicious" in t and "amount" in t and "round" in t:
        return _eval_round_amounts
    if "night" in t or "00:00" in t or "01:00" in t:
        return _eval_night_transactions
    if "ip" in t and ("185" in t or "foreign" in t):
        return _eval_foreign_ip
    if "mcc" in t or "merchant" in t:
        return _eval_high_risk_mcc
    if "balance" in t or "%" in t and "balance" in t:
        return _eval_balance_ratio
    if "alert" in t and "repeated" in t:
        return _eval_repeated_alerts
    if "iban" in t and ("blacklist" in t or "suspicious" in t or "counterpart" in t):
        return _eval_suspicious_iban
    if "amount" in t and (">" in t or ">" in t):
        return _eval_large_amount

    # Fallback par domaine
    domain_map = {
        "LIMIT":      _eval_large_amount,
        "AML":        _eval_structuring,
        "VELOCITY":   _eval_night_transactions,
        "GEOGRAPHIC": _eval_foreign_ip,
        "BEHAVIORAL": _eval_balance_ratio,
    }
    return domain_map.get(d, _eval_generic)


# ══════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════

def run_rules_from_db(df: pd.DataFrame, db: Session) -> list[dict]:
    """
    Charge les règles ACTIVES depuis la DB et les évalue sur le DataFrame.

    Remplace run_all_rules() de rules.py.
    Compatible avec le format attendu par scoring.py et nodes.py.

    Args:
        df:  DataFrame des transactions filtrées par IBAN
        db:  Session SQLAlchemy

    Returns:
        Liste de dicts résultats, un par règle active.
    """
    active_rules: list[FraudRuleModel] = (
        db.query(FraudRuleModel)
        .filter(FraudRuleModel.active == True)   # noqa: E712
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
            result = evaluator(df, rule)
            results.append(result)
            if result["triggered"]:
                logger.info(f"[rule_engine] TRIGGERED {rule.id} ({rule.name}) "
                            f"— +{rule.points} pts")
        except Exception as exc:
            logger.error(f"[rule_engine] Error evaluating rule {rule.id}: {exc}")
            results.append(_not_triggered(rule, f"Evaluation error: {exc}"))

    return results
