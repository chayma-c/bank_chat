import os
import pandas as pd
from pathlib import Path

# Mock find_transaction_file logic or just use the local path
csv_path = r"c:\Users\chayma\Desktop\bank_chat\backend\data\transactions.csv"

def _parse_amount(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )

def load_transactions(path):
    df = pd.read_csv(path)
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
    )
    if "transaction_amount" in df.columns:
        df["transaction_amount"] = _parse_amount(df["transaction_amount"])
    if "account_currentbalance" in df.columns:
        df["account_currentbalance"] = _parse_amount(df["account_currentbalance"])
    return df

df = load_transactions(csv_path)
print(f"Columns: {df.columns.tolist()}")
print(f"Number of rows: {len(df)}")
print("\nFirst row data:")
print(df.iloc[0])

IBAN_BURST = "DE22965687203229836394"
print(f"\nSearching for IBAN: {IBAN_BURST}")
if "client_iban" in df.columns:
    found = df[df["client_iban"].astype(str).str.contains(IBAN_BURST, na=False)]
    print(f"Found in client_iban: {len(found)}")
if "counterparty_iban" in df.columns:
    found = df[df["counterparty_iban"].astype(str).str.contains(IBAN_BURST, na=False)]
    print(f"Found in counterparty_iban: {len(found)}")
