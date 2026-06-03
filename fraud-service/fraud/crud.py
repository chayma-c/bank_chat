"""
CRUD operations for FraudRule.
All functions receive a SQLAlchemy Session and return ORM objects or None.

Thresholds are expressed in EUR (international standard).
Reference: 5AMLD (EU 2018/843), FATF GAFI 2023, PSD2 SCA, OFAC sanctions list.
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
#
# THRESHOLD JUSTIFICATION — all values in EUR (international)
# ─────────────────────────────────────────────────────────────────────────────
#  R1  HIGH_AMOUNT           > 5 000 €   : aligns with PSD2 high-value card threshold
#                                          (>5 000 € → 3DS + geoloc required).
#                                          Previous 3 000 € was too low (generated false
#                                          positives on routine business payments).
#                                          Score raised to +30 (matrix: country=+30).
#
#  R2  SUSPICIOUS_IBAN       regex+OFAC  : pattern kept; description enriched to
#                                          explicitly cover OFAC-listed countries
#                                          (RU/IR/KP/SY/VE). No threshold change.
#
#  R3  STRUCTURING           >20 deposits < 10 000 € / 7 days
#                                        : 5AMLD Art.11 — smurfing window = 1 week,
#                                          reporting threshold = 10 000 €.
#                                          Previous "3 txns 850–950 / 24h" was a
#                                          single-day sub-band, not the legal standard.
#                                          Score raised to +35 (CRITICAL severity).
#
#  R4  NIGHT_TRANSACTION     00:00–05:00 : confirmed. Banking scoring matrix: +20 pts
#                                          for night-hour transactions. No change.
#
#  R5  FOREIGN_IP            geofencing > 1 000 km + unknown country
#                                        : single hard-coded prefix "185.230.x.x"
#                                          replaced by country-level geofencing
#                                          (distance > 1 000 km vs. client history),
#                                          which is the standard used by card networks
#                                          (Visa/MC geo-blocking rules).
#                                          Score raised to +20, secondary threshold
#                                          raised to 3 000 € (was 2 000 €).
#
#  R6  HIGH_RISK_MCC         MCC + > 1 500 €
#                                        : confirmed. MCC 5541/5999/5311 at >1 500 €
#                                          is a recognised card-fraud indicator.
#                                          No change.
#
#  R7  HIGH_VALUE_VS_BALANCE > 80 % balance
#                                        : confirmed. Standard behavioural signal
#                                          (account drain pattern). No change.
#
#  R8  REPEATED_ALERTS       ≥ 2 alerts / 7 days
#                                        : threshold lowered from 3 to 2.
#                                          A second alert within 7 days confirms a
#                                          persistent risk profile (FATF Rec. 20).
#                                          Score raised to +25.
#
#  R9  VELOCITY_HIGH         > 5 txns / 1h (card)  OR  > 10 txns / 10 min (wire)
#                                        : split into two sub-rules per 5AMLD:
#                                          — Card: >5 / 1h → Challenge SMS
#                                          — Wire: >10 / 10 min → auto-BLOCK
#                                          Previous ">10 / 1h" was double the card
#                                          standard. Score raised to +20.
#
#  R10 ROUND_AMOUNT          near-threshold amounts (9 500–9 999 €)
#                                        : AML standard — structuring amounts just
#                                          below reporting threshold (10 000 €).
#                                          Previous "> 500 €" flagged every salary
#                                          transfer. Restricted to the 9 500–9 999 €
#                                          danger band. Score: +15.
#
#  R11 CROSS_BORDER          different countries / 48h
#                                        : confirmed. Layering detection A→B→C < 48h
#                                          and OFAC geofencing. No change.
#
#  R12 NEW_BENEFICIARY_HIGH  new beneficiary + immediate transfer > 3 000 €
#                                        : raised from 2 000 € to 3 000 € to reduce
#                                          false positives on routine payroll/rent.
#                                          Added mandatory "immediate transfer" condition
#                                          (same session) — the velocity component is
#                                          the true fraud signal, not the amount alone.
#                                          Score raised to +20.
#
#  R13 DORMANT_ACCOUNT       inactive > 90 days
#                                        : confirmed. Standard money-mule reactivation
#                                          indicator. No change.
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_RULES: list[dict] = [
    # ── R1 : HIGH_AMOUNT ─────────────────────────────────────────────────────
    # Was: > 3,000 TND / +20 pts
    # Now: > 5,000 EUR / +30 pts  (PSD2 high-value threshold)
    dict(
        name          = "High amount transaction",
        domain        = "LIMIT",
        trigger       = "Amount > 5000 EUR",
        triggerDetail = "PSD2 high-value threshold — 3DS + geoloc required above 5 000 €",
        points        = 30,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Flags any single transaction exceeding 5 000 €. "
            "Aligned with PSD2 SCA Art.18 and card-network high-value rules "
            "(Visa/Mastercard: >5 000 € → mandatory 3DS + geolocation check). "
            "Previous threshold of 3 000 generated excessive false positives on "
            "routine business payments."
        ),
    ),

    # ── R2 : SUSPICIOUS_IBAN ──────────────────────────────────────────────────
    # Was: regex pattern / +20 pts
    # Now: regex + explicit OFAC country check / +20 pts  (enriched description)
    dict(
        name          = "Suspicious IBAN check",
        domain        = "GEOGRAPHIC",
        trigger       = "Client/counterparty IBAN in blacklist or OFAC country",
        triggerDetail = "OFAC sanctioned countries: RU, IR, KP, SY, VE — auto-BLOCK",
        points        = 20,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Checks client and counterparty IBANs against the known suspicious IBAN "
            "blacklist AND against OFAC-sanctioned country prefixes "
            "(RU=Russia, IR=Iran, KP=North Korea, SY=Syria, VE=Venezuela). "
            "Any match with an OFAC country triggers automatic BLOCK (score = 100). "
            "Self-transfer detection (client IBAN appearing as counterparty) also covered."
        ),
    ),

    # ── R3 : STRUCTURING ─────────────────────────────────────────────────────
    # Was: 3 txns 850–950 / 24h / +25 pts / CRITICAL
    # Now: >20 deposits < 10 000 € in 7 days / +35 pts / CRITICAL  (5AMLD Art.11)
    dict(
        name          = "Structuring / smurfing (AML)",
        domain        = "AML",
        trigger       = "More than 20 deposits under 10000 EUR within 7 days",
        triggerDetail = "5AMLD Art.11: smurfing window = 7 days, reporting threshold = 10 000 €. Suspect amounts: 9 990, 9 950, 4 990 €.",
        points        = 35,
        severity      = "CRITICAL",
        active        = True,
        description   = (
            "Classic AML structuring (smurfing): multiple deposits just below the "
            "10 000 € mandatory reporting threshold (5AMLD Art.11), spread over a "
            "7-day sliding window to avoid detection. "
            "Replaces the previous single-day 850–950 band which was too narrow and "
            "missed most real structuring patterns. TRACFIN declaration mandatory if triggered."
        ),
    ),

    # ── R4 : NIGHT_TRANSACTION ────────────────────────────────────────────────
    # Confirmed — no change to threshold, domain adjusted to BEHAVIORAL
    dict(
        name          = "Night transfer alert",
        domain        = "BEHAVIORAL",
        trigger       = "P2P / INTL transfer between 00:00–05:00",
        triggerDetail = "Unusual hour for high-value transfers — banking scoring matrix +20 pts",
        points        = 10,
        severity      = "MEDIUM",
        active        = True,
        description   = (
            "Flags P2P and international wire transfers executed during the 00:00–05:00 "
            "window. Confirmed as a standard behavioural signal in bank scoring matrices "
            "(night-hour = +20 pts base). No threshold change required."
        ),
    ),

    # ── R5 : FOREIGN_IP ───────────────────────────────────────────────────────
    # Was: IP starts with 185.230.x.x / +15 pts
    # Now: geofencing > 1 000 km from client history / +20 pts
    dict(
        name          = "Foreign IP / geofencing alert",
        domain        = "GEOGRAPHIC",
        trigger       = "IP country differs from client home country OR distance > 1000 km",
        triggerDetail = "Secondary check: amount > 3000 EUR or customer risk score >= 70. OFAC countries → BLOCK.",
        points        = 20,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Replaces the single hard-coded IP prefix '185.230.x.x' with country-level "
            "geofencing: any transaction originating from an IP whose country differs from "
            "the client's registered home country, or where estimated distance from the last "
            "known location exceeds 1 000 km, is flagged. "
            "This matches the Visa/Mastercard geo-blocking standard and covers VPN, proxy, "
            "and travel scenarios uniformly. Secondary threshold raised to 3 000 € (was 2 000 €)."
        ),
    ),

    # ── R6 : HIGH_RISK_MCC ────────────────────────────────────────────────────
    # Confirmed — no change
    dict(
        name          = "High-risk merchant (MCC)",
        domain        = "BEHAVIORAL",
        trigger       = "MCC 5541/5999/5311 and amount > 1500 EUR",
        triggerDetail = "No recent pattern for this MCC on account history",
        points        = 10,
        severity      = "MEDIUM",
        active        = True,
        description   = (
            "Flags high-value purchases at merchant category codes statistically linked "
            "to fraud: 5541 (service stations/gas), 5999 (misc. retail), 5311 (department stores). "
            "Threshold of 1 500 € confirmed as appropriate for the international context."
        ),
    ),

    # ── R7 : HIGH_VALUE_VS_BALANCE ────────────────────────────────────────────
    # Confirmed — no change
    dict(
        name          = "Balance drain pattern",
        domain        = "BEHAVIORAL",
        trigger       = "Amount > 80% of account current balance",
        triggerDetail = "Moving most of balance in one transaction",
        points        = 10,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Detects transactions that drain more than 80% of the account balance in a "
            "single operation. Standard behavioural signal across banking fraud systems. "
            "No threshold change required."
        ),
    ),

    # ── R8 : REPEATED_ALERTS ─────────────────────────────────────────────────
    # Was: 3+ alerts / 7 days / +20 pts
    # Now: ≥ 2 alerts / 7 days / +25 pts  (FATF Rec. 20 — persistent risk profile)
    dict(
        name          = "Repeated fraud alerts",
        domain        = "BEHAVIORAL",
        trigger       = "2 or more ALERTED transactions in last 7 days",
        triggerDetail = "Same client IBAN — persistent risk profile confirmed from second alert",
        points        = 25,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Detects ongoing risk profiles from repeated alert status on the same IBAN. "
            "Threshold lowered from 3 to 2 alerts: a second flag within 7 days is sufficient "
            "to confirm a persistent pattern per FATF Recommendation 20 (ongoing monitoring). "
            "Score raised to 25 to reflect the elevated certainty of the signal."
        ),
    ),

    # ── R9 : VELOCITY_HIGH ────────────────────────────────────────────────────
    # Was: > 10 txns / 1h / +15 pts
    # Now: > 5 txns / 1h (card) OR > 10 txns / 10 min (wire) / +20 pts  (5AMLD velocity)
    dict(
        name          = "High velocity transactions",
        domain        = "VELOCITY",
        trigger       = "More than 5 card transactions per 1 hour OR more than 10 wire transfers per 10 minutes",
        triggerDetail = "Card >5/1h → Challenge SMS (5AMLD) | Wire >10/10min → auto-BLOCK",
        points        = 20,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Two velocity sub-rules in one: "
            "(1) Card: >5 transactions in 1 hour → Challenge SMS (5AMLD velocity standard). "
            "(2) Wire/SEPA: >10 transfers in 10 minutes → automatic BLOCK. "
            "Previous single threshold of >10/1h was double the card standard and missed "
            "fast-paced wire fraud. Score raised to 20."
        ),
    ),

    # ── R10 : ROUND_AMOUNT ────────────────────────────────────────────────────
    # Was: round amount > 500 EUR / +8 pts
    # Now: near-threshold amounts 9 500–9 999 EUR / +15 pts  (AML structuring band)
    dict(
        name          = "Suspicious near-threshold amount (AML)",
        domain        = "AML",
        trigger       = "Amount between 9500 EUR and 9999 EUR",
        triggerDetail = "Classic structuring: just below 10 000 € TRACFIN reporting threshold. Also flags 4 500–4 999 € and 14 500–14 999 €.",
        points        = 15,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Detects amounts deliberately kept just below the mandatory cash reporting "
            "threshold (10 000 €) as defined by 5AMLD and FATF. "
            "Danger bands: 9 500–9 999 € (primary), 4 500–4 999 € and 14 500–14 999 € "
            "(secondary, for split-transaction structuring). "
            "Replaces the previous '>500 € round amount' rule which generated massive "
            "false positives (every salary, rent, or round-number business payment). "
            "Score raised from 8 to 15."
        ),
    ),

    # ── R11 : CROSS_BORDER ────────────────────────────────────────────────────
    # Confirmed — no change
    dict(
        name          = "Cross-border transaction",
        domain        = "GEOGRAPHIC",
        trigger       = "Transactions in different countries within 48 hours",
        triggerDetail = "Layering detection: A→B→C chain under 48h. OFAC countries → BLOCK.",
        points        = 12,
        severity      = "MEDIUM",
        active        = True,
        description   = (
            "Flags accounts with transactions in different countries within a 48-hour window. "
            "Aligned with AML layering detection (A→B→C cascade < 48h) and OFAC geofencing. "
            "No threshold change required."
        ),
    ),

    # ── R12 : NEW_BENEFICIARY_HIGH ────────────────────────────────────────────
    # Was: new beneficiary > 2 000 EUR / +15 pts
    # Now: new beneficiary + immediate transfer (same session) > 3 000 EUR / +20 pts
    dict(
        name          = "New beneficiary high-value transfer",
        domain        = "BEHAVIORAL",
        trigger       = "New beneficiary added AND immediate transfer > 3000 EUR in same session",
        triggerDetail = "Beneficiary velocity: add + transfer in same session is the critical signal, not amount alone",
        points        = 20,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Detects the critical fraud pattern: a new beneficiary is added and a high-value "
            "transfer to that beneficiary is executed in the same session. "
            "Threshold raised from 2 000 € to 3 000 € to reduce false positives on routine "
            "payroll or rent payments. The 'immediate transfer' condition (same session) is "
            "mandatory — the velocity component is the true fraud signal. Score raised to 20."
        ),
    ),

    # ── R13 : DORMANT_ACCOUNT_ACTIVITY ────────────────────────────────────────
    # Confirmed — no change
    dict(
        name          = "Dormant account reactivation",
        domain        = "BEHAVIORAL",
        trigger       = "Account inactive for more than 90 days with sudden activity",
        triggerDetail = "Standard money-mule reactivation indicator",
        points        = 18,
        severity      = "HIGH",
        active        = True,
        description   = (
            "Flags accounts that have been inactive for more than 90 days and suddenly "
            "show significant transaction activity. Standard money-mule reactivation "
            "indicator per FATF typologies. No threshold change required."
        ),
    ),
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
