"""
CSV transaction loader — reads and filters by IBAN (counterparty_iban or client_iban).

CSV schema (transactions.csv):
  Transaction_Amount, Timestamp, Geo_Location, IP_Address, Merchant_MCC,
  Account_CurrentBalance, Client_IBAN, Counterparty_IBAN, Transaction_Type

Amounts use comma as decimal separator (European format).
Geo_Location is a free-text field (e.g. "Frankfurt, Germany (50.1109, 8.6821)").
"""

import os
import re
import pandas as pd
from pathlib import Path
from typing import Optional


# ── Data locations ───────────────────────────────────────────────────────────
# The service runs in Docker with the file mounted at /app/data.

TRANSACTION_FILENAMES = ("transactions.csv", "transactions.xlsx", "transactions.xls")


def _candidate_data_directories() -> list[Path]:
    dirs: list[Path] = []

    env_dir = os.getenv("FRAUD_DATA_DIR", "").strip()
    if env_dir:
        dirs.append(Path(env_dir))

    # Standard Docker mount used by docker-compose.yml
    dirs.append(Path("/app/data"))

    # Direct mount fallback when the container is started with /data
    dirs.append(Path("/data"))

    # Local repository layout: .../bank_chat/backend/data
    for ancestor in Path(__file__).resolve().parents:
        dirs.append(ancestor / "backend" / "data")
        dirs.append(ancestor / "data")

    # Remove duplicates while preserving order
    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            unique_dirs.append(directory)
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
        f"Transaction file not found. Looked in:\n"
        + "\n".join(f"  {idx + 1}. {path}" for idx, path in enumerate(searched))
        + "\nSet FRAUD_DATA_DIR, or place the file in /app/data/transactions.csv "
        "(Docker) or backend/data/transactions.csv (local)."
    )


# Keep old name as alias for backward compatibility
find_excel_file = find_transaction_file

#clean and convert amount values
def _parse_amount(series: pd.Series) -> pd.Series:
    """
    Parse amounts that may use comma as decimal separator.
    E.g. '12554,38' → 12554.38  |  '2068' → 2068.0
    """
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
        # Remove coordinate suffix like "(50.1109, 8.6821)"
        clean = re.sub(r"\s*\([-\d.,\s]+\)\s*$", "", val).strip()
        # If there is a comma, the part after the last comma is the country
        parts = [p.strip() for p in clean.split(",")]
        return parts[-1] if parts else clean

    return geo_series.apply(_parse)


def load_transactions(excel_path: Optional[str] = None) -> pd.DataFrame:
    """
    Load the full transactions file into a DataFrame.
    Normalizes column names, parses timestamps, and fixes decimal separators.
    """
    path = find_transaction_file(excel_path)

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    # Normalize column names: strip whitespace, lowercase, replace spaces with _
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
    )

    # ── Parse timestamp ───────────────────────────────────────────────────────
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # ── Fix amount decimal separator (comma → dot) ────────────────────────────
    if "transaction_amount" in df.columns:
        df["transaction_amount"] = _parse_amount(df["transaction_amount"])

    if "account_currentbalance" in df.columns:
        df["account_currentbalance"] = _parse_amount(df["account_currentbalance"])

    # ── Derive a 'country' column from geo_location for rules / scoring ───────
    if "geo_location" in df.columns and "country" not in df.columns:
        df["country"] = _extract_country_from_geo(df["geo_location"])

    return df


def filter_by_iban(df: pd.DataFrame, iban: str) -> pd.DataFrame:
    """
    Filter transactions for a specific IBAN.

    Searches in all IBAN-like columns:
      - counterparty_iban  (new schema — destination account)
      - client_iban        (new schema — source account)
    """
    iban_clean = iban.strip().upper()

    iban_columns = [
        "counterparty_iban",
        "client_iban",
    ]

    masks = []
    for col in iban_columns:
        if col in df.columns:
            mask = df[col].astype(str).str.upper().str.contains(iban_clean, na=False)
            masks.append(mask)

    if not masks:
        raise ValueError(
            f"No IBAN/account column found. Available columns: {list(df.columns)}"
        )

    combined_mask = masks[0]
    for m in masks[1:]:
        combined_mask = combined_mask | m

    return df[combined_mask].copy()


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

    # Amount: new schema uses transaction_amount; fall back to old names
    for col in ("transaction_amount"):
        if col in df.columns:
            amounts = pd.to_numeric(df[col], errors="coerce")
            break
    else:
        amounts = pd.Series(dtype=float)

    # Country: derived from geo_location or old column names
    for col in ("country"):
        if col in df.columns:
            country_col = col
            break
    else:
        country_col = None

    # Transaction type
    for col in ("transaction_type", "type_transaction", "type"):
        if col in df.columns:
            type_col = col
            break
    else:
        type_col = None

    date_range = "N/A"
    if "timestamp" in df.columns and df["timestamp"].notna().any():
        min_date = df["timestamp"].min().strftime("%Y-%m-%d")
        max_date = df["timestamp"].max().strftime("%Y-%m-%d")
        date_range = f"{min_date} → {max_date}"

    return {
        "total_transactions": len(df),
        "total_amount": round(float(amounts.sum()), 2) if not amounts.empty else 0.0,
        "avg_amount": round(float(amounts.mean()), 2) if not amounts.empty else 0.0,
        "max_amount": round(float(amounts.max()), 2) if not amounts.empty else 0.0,
        "min_amount": round(float(amounts.min()), 2) if not amounts.empty else 0.0,
        "currencies": [],  # No currency column in new schema
        "countries": (
            df[country_col].dropna().unique().tolist() if country_col else []
        ),
        "date_range": date_range,
        "transaction_types": (
            df[type_col].value_counts().to_dict() if type_col else {}
        ),
    }