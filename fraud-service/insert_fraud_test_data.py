"""
Script to insert high-risk transactions into the database to trigger fraud rules.
This simulates a user doing multiple rapid high-value transfers to high-risk countries.
"""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from fraud.database import SessionLocal
from fraud.models import TransactionModel

# IBAN to target for testing
TARGET_IBAN = "DE85538483994235988126"

def insert_fraudulent_transactions():
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        
        # 1. Very High Value Transaction (Rule: LARGE_TRANSFER)
        tx1 = TransactionModel(
            transaction_id=str(uuid.uuid4()),
            timestamp=now - timedelta(minutes=10),
            client_iban=TARGET_IBAN,
            transaction_amount=45000.00,
            transaction_type="WIRE_TRANSFER",
            counterparty_iban="CH9300000000000000000"
        )
        
        # 2. Transfer to High-Risk Country (Rule: HIGH_RISK_COUNTRY)
        tx2 = TransactionModel(
            transaction_id=str(uuid.uuid4()),
            timestamp=now - timedelta(minutes=8),
            client_iban=TARGET_IBAN,
            transaction_amount=2500.00,
            transaction_type="WIRE_TRANSFER",
            counterparty_iban="SY9300000000000000000"
        )

        # 3. Rapid succession of small transactions (Rule: RAPID_TRANSFERS)
        tx3 = TransactionModel(
            transaction_id=str(uuid.uuid4()),
            timestamp=now - timedelta(minutes=2),
            client_iban=TARGET_IBAN,
            transaction_amount=950.00,
            transaction_type="CARD_PAYMENT",
            counterparty_iban="NL9300000000000000000"
        )
        
        tx4 = TransactionModel(
            transaction_id=str(uuid.uuid4()),
            timestamp=now - timedelta(minutes=1),
            client_iban=TARGET_IBAN,
            transaction_amount=950.00,
            transaction_type="CARD_PAYMENT",
            counterparty_iban="NL9300000000000000000"
        )
        
        tx5 = TransactionModel(
            transaction_id=str(uuid.uuid4()),
            timestamp=now - timedelta(seconds=30),
            client_iban=TARGET_IBAN,
            transaction_amount=950.00,
            transaction_type="CARD_PAYMENT",
            counterparty_iban="NL9300000000000000000"
        )

        # Insert all
        db.add_all([tx1, tx2, tx3, tx4, tx5])
        db.commit()
        print(f"Successfully inserted 5 fraudulent transactions for IBAN {TARGET_IBAN}.")
        
    except Exception as e:
        db.rollback()
        print(f"Error inserting transactions: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    insert_fraudulent_transactions()
