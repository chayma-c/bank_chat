"""
loader.py — Transaction data access layer.

Supports two backends, selected at call time:
  • DB mode  (db= provided) — queries the `transactions` PostgreSQL table
                               with optional IBAN pre-filter and rolling window
  • File mode (db= None)    — reads the CSV / Excel file (legacy / seed fallback)

Public API is backward-compatible: all callers that don't pass `db=` keep
working exactly as before.

CSV schema (transactions.csv):
  Transaction_Amount, Timestamp, Geo_Location, IP_Address, Merchant_MCC,
  Account_CurrentBalance, Client_IBAN, Counterparty_IBAN, Transaction_Type

Amounts use comma as decimal separator (European format).
Geo_Location is a free-text field (e.g. "Frankfurt, Germany (50.1109, 8.6821)").
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Data locations ────────────────────────────────────────────────────────────
TRANSACTION_FILENAMES = ("transactions.csv", "transactions.xlsx", "transactions.xls")


def _candidate_data_directories() -> list[Path]:
    dirs: list[Path] = []

    env_dir = os.getenv("FRAUD_DATA_DIR", "").strip()
    if env_dir:
        dirs.append(Path(env_dir))

    dirs.append(Path("/app/data"))
    dirs.append(Path("/data"))

    for ancestor in Path(__file__).resolve().parents:
        dirs.append(ancestor / "backend" / "data")
        dirs.append(ancestor / "data")

    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            unique_dirs.append(d)
    return unique_dirs


def validate_iban(iban: str) -> bool:
    """Basic IBAN format validation."""
    cleaned = iban.replace(" ", "").upper()
    return bool(re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$", cleaned))


def find_transaction_file(excel_path: Optional[str] = None) -> Path:
    """
    Locate the transactions file (CSV preferred, then Excel).
    Priority: explicit path → FRAUD_DATA_DIR → /app/data → local backend/data
    """
    searched: list[Path] = []

    if excel_path:
        explicit = Path(excel_path)
        searched.append(explicit)
        if explicit.is_file():
            return explicit

    for directory in _candidate_data_directories():
        for filename in TRANSACTION_FILENAMES:
            candidate = directory / filename
            searched.append(candidate)
            if candidate.is_file():
                return candidate

    raise FileNotFoundError(
        "Transaction file not found. Looked in:\n"
        + "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(searched))
        + "\nSet FRAUD_DATA_DIR, or place the file in /app/data/transactions.csv."
    )


# Keep old name as alias for backward compatibility
find_excel_file = find_transaction_file


# ── Amount / geo helpers ──────────────────────────────────────────────────────

def _parse_amount(series: pd.Series) -> pd.Series:
    """Parse amounts that may use comma as decimal separator."""
    return (
        series.astype(str)
        .str.strip()
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )


def _extract_country_from_geo(geo_series: pd.Series) -> pd.Series:
    """
    Extract a short country label from a Geo_Location string.
    Examples:
      "Frankfurt, Germany (50.1109, 8.6821)"  → "Germany"
      "New York, USA (40.7128, -74.0060)"     → "USA"
      "Hong Kong (22.3193, 114.1694)"          → "Hong Kong"
    """
    def _parse(val: str) -> str:
        if pd.isna(val) or not str(val).strip():
            return ""
        val = str(val).strip()
        clean = re.sub(r"\s*\([-\d.,\s]+\)\s*$", "", val).strip()
        parts = [p.strip() for p in clean.split(",")]
        return parts[-1] if parts else clean

    return geo_series.apply(_parse)


# ══════════════════════════════════════════════════════════════════════════════
# Core loader — DB mode (primary) + File mode (fallback)
# ══════════════════════════════════════════════════════════════════════════════

def load_transactions(
    excel_path: Optional[str] = None,
    db: Optional[Session] = None,
    iban: Optional[str] = None,
    window_days: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load transactions into a DataFrame.

    Args:
        excel_path:  Path to CSV/Excel file (file mode only, legacy).
        db:          SQLAlchemy session. If provided, queries the DB.
        iban:        Pre-filter by client_iban OR counterparty_iban (DB mode only).
        window_days: Only return transactions from the last N days (DB mode only).
                     None = return all history.

    Returns:
        DataFrame with normalised column names matching the fraud rules engine.
    """
    if db is not None:
        return _load_from_db(db, iban=iban, window_days=window_days)
    return _load_from_file(excel_path)


def _load_from_db(
    db: Session,
    iban: Optional[str] = None,
    window_days: Optional[int] = None,
) -> pd.DataFrame:
    """Query the `transactions` table with optional IBAN and time filters."""
    conditions = ["1=1"]
    params: dict = {}

    if iban:
        iban_clean = iban.strip().upper()
        conditions.append(
            "(UPPER(client_iban) = :iban OR UPPER(counterparty_iban) = :iban)"
        )
        params["iban"] = iban_clean

    if window_days is not None:
        # timestamp column may be NULL for legacy seed rows — we skip those
        conditions.append(
            "timestamp >= NOW() - INTERVAL ':window days'"
            .replace(":window", str(int(window_days)))
        )

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT
            id, transaction_id, user_id,
            amount, currency, status, fraud_score, is_fraudulent,
            merchant_name, merchant_category,
            created_at, updated_at,
            transaction_amount, timestamp, geo_location, ip_address,
            merchant_mcc, account_current_balance,
            client_iban, counterparty_iban, transaction_type,
            ingested_at
        FROM transactions
        WHERE {where}
        ORDER BY timestamp ASC NULLS LAST
    """)

    try:
        result = db.execute(sql, params)
        rows = result.fetchall()
    except Exception as exc:
        logger.error(f"[loader] DB query failed: {exc}")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=list(result.keys()))

    # Ensure timestamp is timezone-aware datetime
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)

    # Derive country for geo-based rules
    if "geo_location" in df.columns and "country" not in df.columns:
        df["country"] = _extract_country_from_geo(df["geo_location"].fillna(""))

    logger.debug(f"[loader] DB returned {len(df)} rows (iban={iban}, window={window_days}d)")
    return df


def _load_from_file(excel_path: Optional[str] = None) -> pd.DataFrame:
    """Legacy: read CSV / Excel file and return normalised DataFrame."""
    path = find_transaction_file(excel_path)

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    # Normalize column names
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
    )

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    if "transaction_amount" in df.columns:
        df["transaction_amount"] = _parse_amount(df["transaction_amount"])

    # CSV uses 'account_currentbalance' (no underscore between current/balance)
    if "account_currentbalance" in df.columns:
        df["account_current_balance"] = _parse_amount(df["account_currentbalance"])

    if "geo_location" in df.columns and "country" not in df.columns:
        df["country"] = _extract_country_from_geo(df["geo_location"])

    return df


# ══════════════════════════════════════════════════════════════════════════════
# One-time CSV → DB seeding
# ══════════════════════════════════════════════════════════════════════════════

def seed_transactions_from_csv(db: Session) -> int:
    """
    Import all CSV rows into the `transactions` table on first startup.

    Idempotent: skips entirely if any rows with a non-null client_iban already
    exist (meaning the table has been seeded before).

    Returns:
        Number of rows inserted (0 if already seeded or file not found).
    """
    # Guard: already seeded?
    try:
        count = db.execute(
            text("SELECT COUNT(*) FROM transactions WHERE client_iban IS NOT NULL")
        ).scalar()
        if count and count > 0:
            logger.info(f"[seed] {count} CSV rows already in DB — skipping seed.")
            return 0
    except Exception as exc:
        logger.warning(f"[seed] Count check failed: {exc}")
        return 0

    # Locate the CSV file
    try:
        _path = find_transaction_file()
    except FileNotFoundError:
        logger.warning("[seed] Transaction file not found — skipping seed.")
        return 0

    # Load with file mode (no DB), get normalised DataFrame
    try:
        df = _load_from_file()
    except Exception as exc:
        logger.error(f"[seed] Failed to read transaction file: {exc}")
        return 0

    if df.empty:
        return 0

    # Column mapping: CSV normalised name → DB column name
    # CSV 'account_currentbalance' was already renamed to 'account_current_balance' by _load_from_file
    inserted = 0
    for _, row in df.iterrows():
        try:
            # Safe get helpers
            def _str(col: str) -> Optional[str]:
                v = row.get(col)
                return str(v).strip().upper() if v and pd.notna(v) else None

            def _float(col: str) -> Optional[float]:
                v = row.get(col)
                try:
                    return float(v) if v is not None and pd.notna(v) else None
                except (TypeError, ValueError):
                    return None

            def _int(col: str) -> Optional[int]:
                v = row.get(col)
                try:
                    return int(v) if v is not None and pd.notna(v) else None
                except (TypeError, ValueError):
                    return None

            ts = row.get("timestamp")
            ts_val = ts if pd.notna(ts) else None

            db.execute(text("""
                INSERT INTO transactions (
                    transaction_amount, timestamp, geo_location, ip_address,
                    merchant_mcc, account_current_balance,
                    client_iban, counterparty_iban, transaction_type,
                    ingested_at
                ) VALUES (
                    :transaction_amount, :timestamp, :geo_location, :ip_address,
                    :merchant_mcc, :account_current_balance,
                    :client_iban, :counterparty_iban, :transaction_type,
                    NOW()
                )
            """), {
                "transaction_amount":      _float("transaction_amount"),
                "timestamp":               ts_val,
                "geo_location":            row.get("geo_location"),
                "ip_address":              row.get("ip_address"),
                "merchant_mcc":            _int("merchant_mcc"),
                "account_current_balance": _float("account_current_balance"),
                "client_iban":             _str("client_iban"),
                "counterparty_iban":       _str("counterparty_iban"),
                "transaction_type":        row.get("transaction_type"),
            })
            inserted += 1
        except Exception as exc:
            logger.debug(f"[seed] Skipped row: {exc}")
            continue

    try:
        db.commit()
        logger.info(f"[seed] ✅ Seeded {inserted} transactions from CSV into DB.")
    except Exception as exc:
        db.rollback()
        logger.error(f"[seed] Commit failed: {exc}")
        return 0

    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# DataFrame-level helpers (unchanged public API)
# ══════════════════════════════════════════════════════════════════════════════

def filter_by_iban(df: pd.DataFrame, iban: str) -> pd.DataFrame:
    """
    Post-filter a DataFrame for a specific IBAN (client or counterparty).
    Used as a fallback when DB-level filtering wasn't applied.
    """
    iban_clean = iban.strip().upper()
    iban_cols = ["counterparty_iban", "client_iban"]

    masks = []
    for col in iban_cols:
        if col in df.columns:
            mask = df[col].astype(str).str.upper().str.contains(iban_clean, na=False)
            masks.append(mask)

    if not masks:
        raise ValueError(
            f"No IBAN column found. Available: {list(df.columns)}"
        )

    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m

    return df[combined].copy()


def get_account_summary(df: pd.DataFrame) -> dict:
    """Compute summary statistics for a filtered set of transactions."""
    if df.empty:
        return {
            "total_transactions": 0,
            "total_amount": 0.0,
            "avg_amount": 0.0,
            "max_amount": 0.0,
            "min_amount": 0.0,
            "currencies": [],
            "countries": [],
            "date_range": "N/A",
            "transaction_types": {},
        }

    amount_col = next(
        (c for c in ("transaction_amount", "amount") if c in df.columns), None
    )
    amounts = (
        pd.to_numeric(df[amount_col], errors="coerce")
        if amount_col else pd.Series(dtype=float)
    )

    country_col = next((c for c in ("country",) if c in df.columns), None)

    type_col = next(
        (c for c in ("transaction_type", "type_transaction", "type") if c in df.columns),
        None,
    )

    date_range = "N/A"
    if "timestamp" in df.columns and df["timestamp"].notna().any():
        min_date = df["timestamp"].min().strftime("%Y-%m-%d")
        max_date = df["timestamp"].max().strftime("%Y-%m-%d")
        date_range = f"{min_date} → {max_date}"

    return {
        "total_transactions": len(df),
        "total_amount":       round(float(amounts.sum()),  2) if not amounts.empty else 0.0,
        "avg_amount":         round(float(amounts.mean()), 2) if not amounts.empty else 0.0,
        "max_amount":         round(float(amounts.max()),  2) if not amounts.empty else 0.0,
        "min_amount":         round(float(amounts.min()),  2) if not amounts.empty else 0.0,
        "currencies":         [],  # No currency column in CSV schema
        "countries":          df[country_col].dropna().unique().tolist() if country_col else [],
        "date_range":         date_range,
        "transaction_types":  df[type_col].value_counts().to_dict() if type_col else {},
    }


def list_all_ibans(
    excel_path: Optional[str] = None,
    db: Optional[Session] = None,
) -> list[str]:
    """
    Return a list of all unique client IBANs.
    DB mode (preferred): queries transactions table.
    File mode (fallback): reads CSV/Excel.
    """
    if db is not None:
        try:
            rows = db.execute(
                text(
                    "SELECT DISTINCT client_iban FROM transactions "
                    "WHERE client_iban IS NOT NULL ORDER BY client_iban"
                )
            ).fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            logger.warning(f"[loader] list_all_ibans DB query failed: {exc}")

    # File fallback
    try:
        df = _load_from_file(excel_path)
        if "client_iban" in df.columns:
            return df["client_iban"].dropna().unique().tolist()
    except FileNotFoundError:
        pass
    return []