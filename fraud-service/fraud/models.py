"""
ORM models for the fraud detection service.

Tables owned by this service (banking_data database):
  - fraud_rules          — configurable detection rules (CRUD via admin UI)
  - fraud_decision_logs  — immutable audit log of every analysis run
  - transactions         — unified transaction store (legacy + CSV-aligned columns)
  - account_risk_profile — rolling-memory table, one row per client IBAN
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text, Float,
    JSON, Numeric, DECIMAL,
)
from sqlalchemy.dialects.postgresql import JSONB
from .database import Base

# Convenience alias — maps to TIMESTAMPTZ in PostgreSQL, datetime-aware in Python
TZ = lambda: DateTime(timezone=True)

class FraudDecisionLog(Base):
    """
    Trace complète de chaque analyse fraude déclenchée.
    Créé par nodes.py (generate_summary) après chaque analyse.
    """
    __tablename__ = "fraud_decision_logs"

    id               = Column(String(64),   primary_key=True, index=True)
    # ── Qui / quand ──────────────────────────────────────────────────────────
    created_at       = Column(DateTime,     default=lambda: datetime.now(timezone.utc), index=True)
    user_id          = Column(String(128),  nullable=True,  index=True)
    session_id       = Column(String(128),  nullable=True,  index=True)
    # ── IBAN & transaction ────────────────────────────────────────────────────
    iban             = Column(String(64),   nullable=False, index=True)
    transactions_count = Column(Integer,    nullable=True,  default=0)
    date_range       = Column(String(64),   nullable=True)   # "2024-01-01 → 2024-04-23"
    # ── Scores ───────────────────────────────────────────────────────────────
    score_behavioral = Column(Integer,      nullable=True,  default=0)
    score_aml        = Column(Integer,      nullable=True,  default=0)
    score_final      = Column(Integer,      nullable=True,  default=0)
    risk_level       = Column(String(32),   nullable=True)   # APPROVED | REVIEW | HOLD | BLOCK
    tracfin_required = Column(Boolean,      nullable=False,  default=False)
    # ── Règles déclenchées ────────────────────────────────────────────────────
    rules_triggered  = Column(Integer,      nullable=True,  default=0)
    rules_evaluated  = Column(Integer,      nullable=True,  default=0)
    triggered_rules_detail = Column(JSON,   nullable=True)   # liste des règles déclenchées
    # ── Rapport ───────────────────────────────────────────────────────────────
    report_path      = Column(String(512),  nullable=True)
    download_url     = Column(String(512),  nullable=True)
    # ── Mail ──────────────────────────────────────────────────────────────────
    mail_sent        = Column(Boolean,      nullable=False,  default=False)
    mail_recipient   = Column(String(255),  nullable=True)
    mail_template    = Column(String(64),   nullable=True)   # critical_alert | fraud_alert
    mail_status      = Column(String(16),   nullable=True)   # sent | failed | null
    mail_id          = Column(String(64),   nullable=True)   # UUID from mail-service
    # ── Résumé LLM ───────────────────────────────────────────────────────────
    llm_summary      = Column(Text,         nullable=True)
    # ── Erreur éventuelle ─────────────────────────────────────────────────────
    error            = Column(Text,         nullable=True)

    def to_dict(self) -> dict:
        return {
            "id":                    self.id,
            "created_at":            self.created_at.isoformat() if self.created_at else None,
            "user_id":               self.user_id,
            "session_id":            self.session_id,
            "iban":                  self.iban,
            "transactions_count":    self.transactions_count,
            "date_range":            self.date_range,
            "score_behavioral":      self.score_behavioral,
            "score_aml":             self.score_aml,
            "score_final":           self.score_final,
            "risk_level":            self.risk_level,
            "tracfin_required":      self.tracfin_required,
            "rules_triggered":       self.rules_triggered,
            "rules_evaluated":       self.rules_evaluated,
            "triggered_rules_detail":self.triggered_rules_detail or [],
            "report_path":           self.report_path,
            "download_url":          self.download_url,
            "mail_sent":             self.mail_sent,
            "mail_recipient":        self.mail_recipient,
            "mail_template":         self.mail_template,
            "mail_status":           self.mail_status,
            "mail_id":               self.mail_id,
            "llm_summary":           self.llm_summary,
            "error":                 self.error,
        }
    

class FraudRuleModel(Base):
    __tablename__ = "fraud_rules"

    id            = Column(String(64),  primary_key=True, index=True)
    name          = Column(String(255), nullable=False)
    domain        = Column(String(64),  nullable=False)   # VELOCITY | LIMIT | GEOGRAPHIC | AML | BEHAVIORAL
    trigger       = Column(String(512), nullable=False)
    trigger_detail = Column(String(512), nullable=True, default="")
    points        = Column(Integer,     nullable=False, default=10)
    severity      = Column(String(32),  nullable=False, default="MEDIUM")  # LOW | MEDIUM | HIGH | CRITICAL
    active        = Column(Boolean,     nullable=False, default=True)
    description   = Column(Text,        nullable=True, default="")
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    # ── Audit trail ───────────────────────────────────────────────────────────
    created_by    = Column(String(128),  nullable=True)
    updated_by    = Column(String(128),  nullable=True)
    deleted_at    = Column(DateTime,     nullable=True, default=None)
    deleted_by    = Column(String(128),  nullable=True)

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "name":          self.name,
            "domain":        self.domain,
            "trigger":       self.trigger,
            "triggerDetail": self.trigger_detail,
            "points":        self.points,
            "severity":      self.severity,
            "active":        self.active,
            "description":   self.description or "",
            "createdAt":     self.created_at.isoformat() if self.created_at else None,
            "updatedAt":     self.updated_at.isoformat() if self.updated_at else None,
        }


# ══════════════════════════════════════════════════════════════════════════
# TransactionModel
# Mirrors the unified `transactions` table.
# Legacy columns kept for backward compatibility with the text-to-sql agent
# and existing seed data.  CSV-aligned columns are what the fraud engine reads.
# ══════════════════════════════════════════════════════════════════════════

class TransactionModel(Base):
    """
    Unified transaction record.

    Legacy columns (transaction_id, user_id, amount, currency, status,
    fraud_score, is_fraudulent, merchant_name, merchant_category) are kept
    so that the 60 seed rows and the text-to-sql agent keep working without
    any changes.

    CSV-aligned columns (transaction_amount, timestamp, geo_location,
    ip_address, merchant_mcc, account_current_balance, client_iban,
    counterparty_iban, transaction_type) are what loader.py and the fraud
    rules engine read.

    ingested_at is set to NOW() on every INSERT and is used by the
    incremental ingestion pipeline (Phase 2) to find rows added since the
    last run.
    """
    __tablename__ = "transactions"

    # ── Primary key ────────────────────────────────────────────────────────────
    id                      = Column(Integer,       primary_key=True, autoincrement=True)

    # ── Legacy columns ───────────────────────────────────────────────────────
    transaction_id          = Column(String(255),   unique=True,  nullable=True)
    user_id                 = Column(String(255),   nullable=True,  index=True)
    amount                  = Column(DECIMAL(15, 2), nullable=True)
    currency                = Column(String(3),     nullable=True,  default="EUR")
    status                  = Column(String(20),    nullable=True,  default="pending", index=True)
    fraud_score             = Column(DECIMAL(5, 4), nullable=True,  index=True)
    is_fraudulent           = Column(Boolean,       nullable=True,  default=False, index=True)
    merchant_name           = Column(String(255),   nullable=True)
    merchant_category       = Column(String(100),   nullable=True)
    created_at              = Column(TZ(),          default=lambda: datetime.now(timezone.utc), index=True)
    updated_at              = Column(TZ(),          default=lambda: datetime.now(timezone.utc),
                                    onupdate=lambda: datetime.now(timezone.utc))

    # ── CSV-aligned columns (fraud detection engine) ───────────────────────
    transaction_amount      = Column(DECIMAL(15, 2), nullable=True)
    timestamp               = Column(TZ(),          nullable=True,  index=True)
    geo_location            = Column(Text,           nullable=True)
    ip_address              = Column(String(45),     nullable=True)
    merchant_mcc            = Column(Integer,        nullable=True)
    account_current_balance = Column(DECIMAL(15, 2), nullable=True)
    client_iban             = Column(String(64),     nullable=True,  index=True)
    counterparty_iban       = Column(String(64),     nullable=True,  index=True)
    transaction_type        = Column(String(50),     nullable=True)

    # ── Ingestion tracking ─────────────────────────────────────────────────
    ingested_at             = Column(TZ(),          default=lambda: datetime.now(timezone.utc), index=True)

    def to_dict(self) -> dict:
        return {
            "id":                     self.id,
            "transaction_id":         self.transaction_id,
            "user_id":                self.user_id,
            # legacy amount / legacy status
            "amount":                 float(self.amount) if self.amount is not None else None,
            "currency":               self.currency,
            "status":                 self.status,
            "fraud_score":            float(self.fraud_score) if self.fraud_score is not None else None,
            "is_fraudulent":          self.is_fraudulent,
            "merchant_name":          self.merchant_name,
            "merchant_category":      self.merchant_category,
            "created_at":             self.created_at.isoformat() if self.created_at else None,
            "updated_at":             self.updated_at.isoformat() if self.updated_at else None,
            # CSV-aligned
            "transaction_amount":     float(self.transaction_amount) if self.transaction_amount is not None else None,
            "timestamp":              self.timestamp.isoformat() if self.timestamp else None,
            "geo_location":           self.geo_location,
            "ip_address":             self.ip_address,
            "merchant_mcc":           self.merchant_mcc,
            "account_current_balance":float(self.account_current_balance) if self.account_current_balance is not None else None,
            "client_iban":            self.client_iban,
            "counterparty_iban":      self.counterparty_iban,
            "transaction_type":       self.transaction_type,
            # ingestion
            "ingested_at":            self.ingested_at.isoformat() if self.ingested_at else None,
        }


# ══════════════════════════════════════════════════════════════════════════
# AccountRiskProfile
# Rolling-memory table — one row per client IBAN.
# Written by the fraud engine after every analysis run.
# ══════════════════════════════════════════════════════════════════════════

class AccountRiskProfile(Base):
    """
    Persistent rolling-memory for a single client IBAN.

    Written / upserted by the fraud engine after every analysis run.
    Enables:
      - Skipping accounts not touched in the rolling window
      - Populating Rule 8 (repeated alerts) without an alert-status column
      - Providing a quick last-known risk level to the chat agent
    """
    __tablename__ = "account_risk_profile"

    id                      = Column(Integer,       primary_key=True, autoincrement=True)

    # ── Identity ───────────────────────────────────────────────────────────
    client_iban             = Column(String(64),    unique=True, nullable=False, index=True)

    # ── Last analysis snapshot ───────────────────────────────────────────
    last_analyzed_at        = Column(TZ(),          nullable=True,  index=True)
    last_score_final        = Column(Integer,       nullable=True,  default=0, index=True)
    last_risk_level         = Column(String(32),    nullable=True,  default="APPROVED", index=True)
    last_tracfin            = Column(Boolean,       nullable=True,  default=False)

    # ── Rolling window aggregates ────────────────────────────────────────
    rolling_tx_count        = Column(Integer,       nullable=True,  default=0)
    rolling_total_amount    = Column(DECIMAL(15, 2), nullable=True, default=0)
    rolling_avg_amount      = Column(DECIMAL(15, 2), nullable=True, default=0)
    rolling_max_amount      = Column(DECIMAL(15, 2), nullable=True, default=0)
    rolling_window_days     = Column(Integer,       nullable=True,  default=7)

    # ── Historical alert tracking (for Rule 8) ───────────────────────────
    alert_count_7d          = Column(Integer,       nullable=True,  default=0)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at              = Column(TZ(),          default=lambda: datetime.now(timezone.utc))
    updated_at              = Column(TZ(),          default=lambda: datetime.now(timezone.utc),
                                    onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        def _f(v):  # safe float conversion
            return float(v) if v is not None else None

        return {
            "id":                   self.id,
            "client_iban":          self.client_iban,
            # last analysis
            "last_analyzed_at":     self.last_analyzed_at.isoformat() if self.last_analyzed_at else None,
            "last_score_final":     self.last_score_final,
            "last_risk_level":      self.last_risk_level,
            "last_tracfin":         self.last_tracfin,
            # rolling aggregates
            "rolling_tx_count":     self.rolling_tx_count,
            "rolling_total_amount": _f(self.rolling_total_amount),
            "rolling_avg_amount":   _f(self.rolling_avg_amount),
            "rolling_max_amount":   _f(self.rolling_max_amount),
            "rolling_window_days":  self.rolling_window_days,
            # alert tracking
            "alert_count_7d":       self.alert_count_7d,
            # timestamps
            "created_at":           self.created_at.isoformat() if self.created_at else None,
            "updated_at":           self.updated_at.isoformat() if self.updated_at else None,
        }
