"""
CRUD operations for FraudRule.
All functions receive a SQLAlchemy Session and return ORM objects or None.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from fraud.models  import FraudRuleModel
from fraud.schemas import FraudRuleCreate, FraudRuleUpdate


# ── Read ──────────────────────────────────────────────────────────────────────

def get_all_rules(db: Session) -> list[FraudRuleModel]:
    return db.query(FraudRuleModel).order_by(FraudRuleModel.created_at).all()


def get_rule(db: Session, rule_id: str) -> Optional[FraudRuleModel]:
    return db.query(FraudRuleModel).filter(FraudRuleModel.id == rule_id).first()


# ── Create ────────────────────────────────────────────────────────────────────

def create_rule(db: Session, data: FraudRuleCreate) -> FraudRuleModel:
    # Auto-generate an ID from the name initials + uuid fragment
    initials = "".join(w[0] for w in data.name.split() if w)[:3].upper()
    rule_id  = f"RL-{initials}-{uuid.uuid4().hex[:6].upper()}"

    rule = FraudRuleModel(
        id             = rule_id,
        name           = data.name,
        domain         = data.domain,
        trigger        = data.trigger,
        trigger_detail = data.triggerDetail,
        points         = data.points,
        severity       = data.severity,
        active         = data.active,
        description    = data.description,
        created_at     = datetime.now(timezone.utc),
        updated_at     = datetime.now(timezone.utc),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


# ── Update (full replace) ─────────────────────────────────────────────────────

def update_rule(db: Session, rule_id: str, data: FraudRuleUpdate) -> Optional[FraudRuleModel]:
    rule = get_rule(db, rule_id)
    if not rule:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Map camelCase → snake_case for the ORM field
    if "triggerDetail" in update_data:
        update_data["trigger_detail"] = update_data.pop("triggerDetail")

    for field, value in update_data.items():
        setattr(rule, field, value)

    rule.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rule)
    return rule


# ── Patch (partial — used for toggle active) ──────────────────────────────────

def patch_rule(db: Session, rule_id: str, active: bool) -> Optional[FraudRuleModel]:
    rule = get_rule(db, rule_id)
    if not rule:
        return None
    rule.active     = active
    rule.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rule)
    return rule


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_rule(db: Session, rule_id: str) -> bool:
    rule = get_rule(db, rule_id)
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


# ── Seed defaults (called once on startup) ────────────────────────────────────

DEFAULT_RULES = [
    dict(name="Large or round amount",    domain="LIMIT",      trigger="Amount > 3,000 TND",                        triggerDetail="Or suspicious round amounts (999, 1000, 5000…)",         points=35, severity="HIGH",     active=True,  description="Flags transactions above threshold or with suspicious round amounts used in fraud."),
    dict(name="Suspicious IBAN check",    domain="AML",        trigger="Client/counterparty IBAN in blacklist",      triggerDetail="Known structuring or ML-prone accounts",                  points=35, severity="HIGH",     active=True,  description="Checks client and counterparty IBANs against the configured suspicious IBAN list."),
    dict(name="Structuring pattern (AML)",domain="AML",        trigger="3+ txs of 850–950 TND within 24h",          triggerDetail="Same client IBAN in 24-hour sliding window",              points=25, severity="CRITICAL",active=True,  description="Classic AML structuring detection — multiple near-threshold amounts to avoid reporting."),
    dict(name="Night transfer alert",     domain="VELOCITY",   trigger="P2P / INTL transfer between 00:00–05:00",    triggerDetail="Unusual hour for high-value transfers",                   points=10, severity="MEDIUM",   active=True,  description="Flags P2P and international transfers made during night hours."),
    dict(name="Foreign IP detection",     domain="GEOGRAPHIC", trigger="IP starts with 185.230.x.x",                triggerDetail="+ amount > 2,000 TND or customer risk score ≥ 70",        points=25, severity="HIGH",     active=True,  description="Detects transactions from known foreign IP ranges with additional risk context."),
    dict(name="High-risk merchant (MCC)", domain="BEHAVIORAL", trigger="MCC 5541/5999/5311 and amount > 1,500 TND", triggerDetail="No recent pattern for this MCC on account",               points=10, severity="MEDIUM",   active=True,  description="Flags high-value purchases at merchant category codes linked to fraud."),
    dict(name="Balance drain pattern",    domain="BEHAVIORAL", trigger="Amount > 80% of account current balance",   triggerDetail="Moving most of balance in one transaction",               points=10, severity="HIGH",     active=True,  description="Detects transactions that drain most of the account balance in a single operation."),
    dict(name="Repeated alerts",          domain="VELOCITY",   trigger="3+ ALERTED transactions in last 7 days",    triggerDetail="Same client IBAN recurring flags",                        points=20, severity="HIGH",     active=False, description="Detects ongoing risk profiles from repeated alert status on the same IBAN."),
]


def seed_default_rules(db: Session) -> None:
    """Insert default rules only if the table is empty."""
    count = db.query(FraudRuleModel).count()
    if count > 0:
        return  # Already seeded — skip

    for rule_data in DEFAULT_RULES:
        initials = "".join(w[0] for w in rule_data["name"].split() if w)[:3].upper()
        rule_id  = f"RL-{initials}-{uuid.uuid4().hex[:6].upper()}"
        rule = FraudRuleModel(
            id             = rule_id,
            name           = rule_data["name"],
            domain         = rule_data["domain"],
            trigger        = rule_data["trigger"],
            trigger_detail = rule_data["triggerDetail"],
            points         = rule_data["points"],
            severity       = rule_data["severity"],
            active         = rule_data["active"],
            description    = rule_data["description"],
            created_at     = datetime.now(timezone.utc),
            updated_at     = datetime.now(timezone.utc),
        )
        db.add(rule)
    db.commit()
