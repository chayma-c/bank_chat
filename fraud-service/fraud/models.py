"""
ORM model for fraud detection rules.
Maps to the `fraud_rules` table in the database.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, Float, JSON
from .database import Base

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
