"""
Excel transaction loader — reads and filters by IBAN (compte_dest).
"""

import os
import re
import pandas as pd
from pathlib import Path
from typing import Tuple, Optional


# ── Default data directory ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


def validate_iban(iban: str) -> bool:
    """Basic IBAN format validation."""
    cleaned = iban.replace(" ", "").upper()
    # IBAN: 2 letters + 2 digits + up to 30 alphanumeric
    return bool(re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$", cleaned))


def find_excel_file(excel_path: Optional[str] = None) -> Path:
    """
    Locate the transactions Excel file.
    Priority: explicit path → DATA_DIR/transactions.xlsx
    """
    if excel_path and os.path.isfile(excel_path):
        return Path(excel_path)

    default_path = DATA_DIR / "transactions.xlsx"
    if default_path.exists():
        return default_path

    # Also check for .xls
    default_xls = DATA_DIR / "transactions.xls"
    if default_xls.exists():
        return default_xls

    raise FileNotFoundError(
        f"Transaction file not found. Looked in:\n"
        f"  1. {excel_path}\n"
        f"  2. {default_path}\n"
        f"Place your Excel file in backend/data/transactions.xlsx"
    )


def load_transactions(excel_path: Optional[str] = None) -> pd.DataFrame:
    """
    Load the full transactions Excel file into a DataFrame.
    Normalizes column names and parses timestamps.
    """
    path = find_excel_file(excel_path)
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