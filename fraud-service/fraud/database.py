"""
Database configuration — PostgreSQL via DATABASE_URL env var.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# ── Connection URL ────────────────────────────────────────────────────────────
# Docker:  postgresql://sql_user:sql_password@postgres:5432/banking_data
# Local:   postgresql://sql_user:sql_password@localhost:5432/banking_data
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://sql_user:sql_password@db:5432/banking_data"
)

# pool_pre_ping=True → reconnects automatically after PostgreSQL timeout
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# autocommit=False → explicit commits required (safe default)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a DB session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
