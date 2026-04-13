import sys
import os
import pandas as pd

# Add the current directory to sys.path so we can import from fraud
sys.path.append(os.getcwd())

from fraud.loader import load_transactions, filter_by_iban

try:
    df = load_transactions()
    print("Columns in DataFrame:", list(df.columns))
    print("Shape:", df.shape)
    print("\nFirst row values:")
    print(df.iloc[0].to_dict())
    
    # Try filtering by an IBAN we know exists
    iban = "DE85538483994235988126" # From line 2 of transactions.csv
    try:
        filtered = filter_by_iban(df, iban)
        print(f"Filtered rows for {iban}:", len(filtered))
    except Exception as e:
        print(f"Error filtering by IBAN: {e}")

except Exception as e:
    print(f"Error loading transactions: {e}")
