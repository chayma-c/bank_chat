"""
ORM model for fraud detection rules.
Maps to the `fraud_rules` table in the database.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text
from .database import Base


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
