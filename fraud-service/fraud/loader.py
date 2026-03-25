"""
Excel transaction loader — reads and filters by IBAN (compte_dest).
"""

import os
import re
import pandas as pd
from pathlib import Path
from typing import Optional


# ── Data locations ───────────────────────────────────────────────────────────
# The service runs in Docker with the file mounted at /app/data.
# In local dev, the canonical file lives in backend/data/.
# We search both, plus an optional FRAUD_DATA_DIR override.
TRANSACTION_FILENAMES = ("transactions.xlsx", "transactions.xls", "transactions.csv")


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
    # IBAN: 2 letters + 2 digits + up to 30 alphanumeric
    return bool(re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$", cleaned))


def find_excel_file(excel_path: Optional[str] = None) -> Path:
    """
    Locate the transactions Excel file.
    Priority: explicit path → FRAUD_DATA_DIR → /app/data → local backend/data.
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
        + "\nSet FRAUD_DATA_DIR, or place the file in /app/data/transactions.xlsx "
        "(Docker) or backend/data/transactions.xlsx (local)."
    )


def load_transactions(excel_path: Optional[str] = None) -> pd.DataFrame:
    """
    Load the full transactions Excel file into a DataFrame.
    Normalizes column names and parses timestamps.
    """
    path = find_excel_file(excel_path)

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    # Normalize column names: strip whitespace, lowercase
    df.columns = df.columns.str.strip().str.lower()

    # Parse timestamp
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    return df


def filter_by_iban(df: pd.DataFrame, iban: str) -> pd.DataFrame:
    """
    Filter transactions for a specific IBAN.
    Searches in 'compte_dest' column (and variations).
    """
    iban_clean = iban.strip().upper()

    # Try multiple possible column names
    iban_columns = ["compte_dest", "iban", "iban_dest", "account", "compte"]
    target_col = None

    for col in iban_columns:
        if col in df.columns:
            target_col = col
            break

    if target_col is None:
        raise ValueError(
            f"No IBAN/account column found. Available columns: {list(df.columns)}"
        )

    # Filter: match exact or partial (for IBAN_XX123 format)
    mask = df[target_col].astype(str).str.upper().str.contains(iban_clean, na=False)
    filtered = df[mask].copy()

    return filtered


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

    amount_col = "montant" if "montant" in df.columns else "amount"
    amounts = pd.to_numeric(df.get(amount_col, pd.Series(dtype=float)), errors="coerce")

    currency_col = "devise" if "devise" in df.columns else "currency"
    country_col = "pays_dest" if "pays_dest" in df.columns else "country"
    type_col = "type_transaction" if "type_transaction" in df.columns else "type"

    date_range = "N/A"
    if "timestamp" in df.columns and df["timestamp"].notna().any():
        min_date = df["timestamp"].min().strftime("%Y-%m-%d")
        max_date = df["timestamp"].max().strftime("%Y-%m-%d")
        date_range = f"{min_date} → {max_date}"

    return {
        "total_transactions": len(df),
        "total_amount": round(float(amounts.sum()), 2),
        "avg_amount": round(float(amounts.mean()), 2),
        "max_amount": round(float(amounts.max()), 2),
        "min_amount": round(float(amounts.min()), 2),
        "currencies": df[currency_col].dropna().unique().tolist() if currency_col in df.columns else [],
        "countries": df[country_col].dropna().unique().tolist() if country_col in df.columns else [],
        "date_range": date_range,
        "transaction_types": df[type_col].value_counts().to_dict() if type_col in df.columns else {},
    }