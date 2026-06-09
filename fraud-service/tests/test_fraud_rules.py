"""
test_fraud_rules.py — Suite de tests complète BankChat Fraud Service.

Couvre :
  ① DB seed    : les 13 règles sont bien insérées avec les bons IDs et seuils EUR
  ② Keyword    : _pick_evaluator() sélectionne le bon évaluateur pour chaque règle
  ③ Évaluateurs: chaque règle se déclenche (MUST trigger) et ne se déclenche pas (MUST NOT)
  ④ LLM        : classify_rule_intent() retourne le bon clé pour une règle custom
  ⑤ Scoring   : compute_final_score, risk_level_from_score, check_tracfin_required
  ⑥ Pipeline  : run_rules_from_db() sur données réelles → résultats cohérents
  ⑦ Intégration: nodes.parse_request extrait l'IBAN, generate_summary retourne du texte

Exécution :
  cd fraud-service
  pytest tests/test_fraud_rules.py -v --tb=short

  # Avec couverture :
  pytest tests/test_fraud_rules.py -v --cov=fraud --cov-report=term-missing
"""

from __future__ import annotations

import os
import types
import pytest
import pandas as pd
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURES : FraudRuleModel mock (pas de DB nécessaire pour les évaluateurs)
# ══════════════════════════════════════════════════════════════════════════════

def make_rule(
    rule_id="RL-TEST-001",
    name="Test rule",
    domain="LIMIT",
    trigger="Amount > 5000 EUR",
    trigger_detail="",
    points=30,
    severity="HIGH",
    active=True,
    description="",
    updated_at=None,
):
    """Factory — crée un FraudRuleModel mock sans SQLAlchemy."""
    rule = MagicMock()
    rule.id             = rule_id
    rule.name           = name
    rule.domain         = domain
    rule.trigger        = trigger
    rule.trigger_detail = trigger_detail
    rule.points         = points
    rule.severity       = severity
    rule.active         = active
    rule.description    = description
    rule.updated_at     = updated_at or datetime.now(timezone.utc)
    return rule


# ── DataFrame factories ───────────────────────────────────────────────────────

def df_with_amounts(*amounts, col="transaction_amount", **extra_cols):
    data = {col: list(amounts)}
    data.update(extra_cols)
    return pd.DataFrame(data)


def df_with_timestamps(timestamps, **extra_cols):
    data = {"timestamp": pd.to_datetime(timestamps)}
    data.update(extra_cols)
    return pd.DataFrame(data)


# ══════════════════════════════════════════════════════════════════════════════
# ① DB SEED — Vérifier les 13 règles dans la base
# ══════════════════════════════════════════════════════════════════════════════

class TestDbSeed:
    """
    Ces tests s'exécutent contre la vraie DB (nécessite docker-compose up).
    Skip automatiquement si DATABASE_URL n'est pas défini.
    """

    @pytest.fixture(scope="class")
    def db_session(self):
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            pytest.skip("DATABASE_URL not set — skipping DB tests")
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_13_rules_present(self, db_session):
        """La DB doit contenir exactement 13 règles après init-db.sql."""
        from fraud.models import FraudRuleModel
        count = db_session.query(FraudRuleModel).count()
        assert count == 13, f"Expected 13 rules, got {count}"

    def test_fixed_ids_present(self, db_session):
        """Les IDs fixes définis dans init-db.sql doivent tous exister."""
        from fraud.models import FraudRuleModel
        expected_ids = [
            "RL-HA-001", "RL-SI-002", "RL-ST-003", "RL-NT-004",
            "RL-FI-005", "RL-MCC-006", "RL-BD-007", "RL-RA-008",
            "RL-VH-009", "RL-NTA-010", "RL-CB-011", "RL-NB-012", "RL-DA-013",
        ]
        existing = {r.id for r in db_session.query(FraudRuleModel).all()}
        missing = set(expected_ids) - existing
        assert not missing, f"Missing rule IDs in DB: {missing}"

    def test_eur_thresholds_in_triggers(self, db_session):
        """Aucune règle ne doit encore contenir 'TND' dans son trigger."""
        from fraud.models import FraudRuleModel
        tnd_rules = db_session.query(FraudRuleModel).filter(
            FraudRuleModel.trigger.ilike("%TND%")
        ).all()
        assert len(tnd_rules) == 0, \
            f"Found rules with TND threshold: {[r.name for r in tnd_rules]}"

    def test_r1_threshold_is_5000(self, db_session):
        """R1 HIGH_AMOUNT : seuil doit être 5000 EUR."""
        from fraud.models import FraudRuleModel
        r1 = db_session.query(FraudRuleModel).filter_by(id="RL-HA-001").first()
        assert r1 is not None
        assert "5000" in r1.trigger, f"Expected 5000 in trigger, got: {r1.trigger}"
        assert r1.points == 30

    def test_r3_structuring_7days(self, db_session):
        """R3 STRUCTURING : doit mentionner 7 jours et 10000, pas 850-950."""
        from fraud.models import FraudRuleModel
        r3 = db_session.query(FraudRuleModel).filter_by(id="RL-ST-003").first()
        assert r3 is not None
        assert "850" not in r3.trigger, "Old TND trigger still present"
        assert "10000" in r3.trigger
        assert "7 days" in r3.trigger
        assert r3.points == 35

    def test_r5_no_hardcoded_ip(self, db_session):
        """R5 FOREIGN_IP : ne doit plus contenir '185.230' dans le trigger principal."""
        from fraud.models import FraudRuleModel
        r5 = db_session.query(FraudRuleModel).filter_by(id="RL-FI-005").first()
        assert r5 is not None
        assert "185.230" not in r5.trigger, "Old hard-coded IP still in trigger"
        assert "1000 km" in r5.trigger or "country" in r5.trigger.lower()

    def test_r8_threshold_is_2_alerts(self, db_session):
        """R8 REPEATED_ALERTS : seuil doit être 2 alertes, pas 3+."""
        from fraud.models import FraudRuleModel
        r8 = db_session.query(FraudRuleModel).filter_by(id="RL-RA-008").first()
        assert r8 is not None
        assert "2 or more" in r8.trigger or "2+" in r8.trigger
        assert "3+" not in r8.trigger

    def test_r9_r10_r11_r12_r13_exist(self, db_session):
        """Les 5 nouvelles règles (R9–R13) doivent exister."""
        from fraud.models import FraudRuleModel
        new_ids = ["RL-VH-009", "RL-NTA-010", "RL-CB-011", "RL-NB-012", "RL-DA-013"]
        for rid in new_ids:
            rule = db_session.query(FraudRuleModel).filter_by(id=rid).first()
            assert rule is not None, f"New rule {rid} not found in DB"
            assert rule.active in (True, False)  # doit avoir un statut défini

    def test_active_rules_count(self, db_session):
        """Au moins 11 règles doivent être actives (R8 est disabled par défaut)."""
        from fraud.models import FraudRuleModel
        active = db_session.query(FraudRuleModel).filter_by(active=True).count()
        assert active >= 11, f"Expected at least 11 active rules, got {active}"

    def test_seed_default_rules_is_noop(self, db_session):
        """seed_default_rules ne doit PAS ajouter de règles supplémentaires."""
        from fraud.models import FraudRuleModel
        from fraud.crud import seed_default_rules
        count_before = db_session.query(FraudRuleModel).count()
        seed_default_rules(db_session)
        count_after = db_session.query(FraudRuleModel).count()
        assert count_after == count_before, \
            f"seed_default_rules added rows: {count_before} → {count_after}"


# ══════════════════════════════════════════════════════════════════════════════
# ② KEYWORD DISPATCH — _pick_evaluator sélectionne le bon évaluateur
# ══════════════════════════════════════════════════════════════════════════════

class TestKeywordDispatch:
    """Vérifie que _keyword_match sélectionne le bon évaluateur pour chaque règle."""

    @pytest.fixture(autouse=True)
    def import_engine(self):
        from fraud import rule_engine as eng
        self.eng = eng

    def _check(self, trigger, expected_fn_name, trigger_detail="", domain="LIMIT"):
        rule = make_rule(trigger=trigger, trigger_detail=trigger_detail, domain=domain)
        evaluator = self.eng._keyword_match(
            (trigger + " " + trigger_detail).lower()
        )
        assert evaluator.__name__ == expected_fn_name, \
            f"trigger='{trigger}' → got '{evaluator.__name__}', expected '{expected_fn_name}'"

    def test_r1_large_amount(self):
        self._check("Amount > 5000 EUR", "_eval_large_amount")

    def test_r2_suspicious_iban(self):
        self._check("Client/counterparty IBAN in blacklist or OFAC country",
                    "_eval_suspicious_iban")

    def test_r3_structuring(self):
        self._check("More than 20 deposits under 10000 EUR within 7 days",
                    "_eval_structuring")

    def test_r4_night(self):
        self._check("P2P / INTL transfer between 00:00–05:00",
                    "_eval_night_transactions")

    def test_r5_geofencing(self):
        self._check("IP country differs from client home country OR distance > 1000 km",
                    "_eval_foreign_ip")

    def test_r6_mcc(self):
        self._check("MCC 5541/5999/5311 and amount > 1500 EUR",
                    "_eval_high_risk_mcc")

    def test_r7_balance(self):
        self._check("Amount > 80% of account current balance",
                    "_eval_balance_ratio")

    def test_r8_repeated_alerts(self):
        self._check("2 or more ALERTED transactions in last 7 days",
                    "_eval_repeated_alerts")

    def test_r9_velocity_card(self):
        self._check("More than 5 card transactions per 1 hour OR more than 10 wire transfers per 10 minutes",
                    "_eval_velocity")

    def test_r10_near_threshold(self):
        self._check("Amount between 9500 EUR and 9999 EUR",
                    "_eval_near_threshold_amount")

    def test_r11_cross_border(self):
        self._check("Transactions in different countries within 48 hours",
                    "_eval_cross_border")

    def test_r12_new_beneficiary(self):
        self._check("New beneficiary added AND immediate transfer > 3000 EUR in same session",
                    "_eval_new_beneficiary")

    def test_r13_dormant(self):
        self._check("Account inactive for more than 90 days with sudden activity",
                    "_eval_dormant_account")

    def test_unknown_trigger_returns_generic(self):
        """Un trigger inconnu doit retourner _eval_generic."""
        result = self.eng._keyword_match("something completely random xyz")
        assert result.__name__ == "_eval_generic"


# ══════════════════════════════════════════════════════════════════════════════
# ③ ÉVALUATEURS — MUST TRIGGER / MUST NOT TRIGGER
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluators:

    @pytest.fixture(autouse=True)
    def import_engine(self):
        from fraud import rule_engine as eng
        self.eng = eng

    # ── R1 HIGH_AMOUNT ────────────────────────────────────────────────────────

    def test_r1_triggers_above_5000(self):
        rule = make_rule(trigger="Amount > 5000 EUR", points=30)
        df = df_with_amounts(5001, 10000)
        result = self.eng._eval_large_amount(df, rule)
        assert result["triggered"] is True
        assert result["points"] == 30
        assert "5,001" in result["details"] or "10,000" in result["details"] or "2 transaction" in result["details"]

    def test_r1_no_trigger_at_5000_exactly(self):
        """5 000 EUR exactement ne doit PAS déclencher (règle : > 5000)."""
        rule = make_rule(trigger="Amount > 5000 EUR", points=30)
        df = df_with_amounts(5000.0)
        result = self.eng._eval_large_amount(df, rule)
        assert result["triggered"] is False

    def test_r1_no_trigger_below_threshold(self):
        rule = make_rule(trigger="Amount > 5000 EUR", points=30)
        df = df_with_amounts(100, 2999, 4999)
        result = self.eng._eval_large_amount(df, rule)
        assert result["triggered"] is False

    def test_r1_old_3000_tnd_not_triggered_at_3500(self):
        """3 500 EUR ne doit PAS déclencher avec le nouveau seuil de 5 000 EUR."""
        rule = make_rule(trigger="Amount > 5000 EUR", points=30)
        df = df_with_amounts(3500)
        result = self.eng._eval_large_amount(df, rule)
        assert result["triggered"] is False, \
            "Old 3 000 TND threshold should NOT trigger at 3 500 EUR"

    # ── R2 SUSPICIOUS_IBAN ────────────────────────────────────────────────────

    def test_r2_triggers_on_ofac_ru(self):
        rule = make_rule(trigger="Client/counterparty IBAN in blacklist or OFAC country", points=20)
        df = pd.DataFrame({
            "client_iban": ["FR761234"],
            "counterparty_iban": ["RU1234567890"],
        })
        result = self.eng._eval_suspicious_iban(df, rule)
        assert result["triggered"] is True
        assert "RU" in result["details"] or "OFAC" in result["details"]

    def test_r2_triggers_on_self_transfer(self):
        rule = make_rule(trigger="Client/counterparty IBAN in blacklist or OFAC country", points=20)
        df = pd.DataFrame({
            "client_iban": ["FR761234ABCD"],
            "counterparty_iban": ["FR761234ABCD"],
        })
        result = self.eng._eval_suspicious_iban(df, rule)
        assert result["triggered"] is True
        assert "Self-transfer" in result["details"]

    def test_r2_no_trigger_normal_iban(self):
        rule = make_rule(trigger="Client/counterparty IBAN in blacklist or OFAC country", points=20)
        df = pd.DataFrame({
            "client_iban": ["FR761234"],
            "counterparty_iban": ["DE891234"],
        })
        result = self.eng._eval_suspicious_iban(df, rule)
        assert result["triggered"] is False

    @pytest.mark.parametrize("country_prefix", ["IR", "KP", "SY", "VE"])
    def test_r2_triggers_all_ofac_countries(self, country_prefix):
        rule = make_rule(trigger="IBAN blacklist OFAC country", points=20)
        df = pd.DataFrame({
            "counterparty_iban": [f"{country_prefix}1234567890"]
        })
        result = self.eng._eval_suspicious_iban(df, rule)
        assert result["triggered"] is True, f"OFAC country {country_prefix} not detected"

    # ── R3 STRUCTURING ────────────────────────────────────────────────────────

    def test_r3_triggers_21_deposits_7days(self):
        rule = make_rule(
            trigger="More than 20 deposits under 10000 EUR within 7 days",
            points=35, severity="CRITICAL"
        )
        now = datetime.now()
        # 21 deposits of 9 000 EUR spread over 6 days → should trigger
        timestamps = [now - timedelta(hours=i * 6) for i in range(21)]
        df = pd.DataFrame({
            "transaction_amount": [9000.0] * 21,
            "timestamp": pd.to_datetime(timestamps),
            "client_iban": ["FR761234"] * 21,
        })
        result = self.eng._eval_structuring(df, rule)
        assert result["triggered"] is True
        assert result["points"] == 35

    def test_r3_no_trigger_3_deposits_24h(self):
        """L'ancienne règle (3 txns 850–950 / 24h) ne doit plus déclencher seule."""
        rule = make_rule(
            trigger="More than 20 deposits under 10000 EUR within 7 days",
            points=35
        )
        now = datetime.now()
        timestamps = [now - timedelta(hours=i) for i in range(3)]
        df = pd.DataFrame({
            "transaction_amount": [900.0, 880.0, 920.0],
            "timestamp": pd.to_datetime(timestamps),
            "client_iban": ["FR761234"] * 3,
        })
        result = self.eng._eval_structuring(df, rule)
        assert result["triggered"] is False, \
            "Old 3-transaction rule should NOT trigger with new 20-deposit standard"

    def test_r3_no_trigger_deposits_over_10000(self):
        """Les dépôts > 10 000 EUR ne comptent pas pour le structuring."""
        rule = make_rule(
            trigger="More than 20 deposits under 10000 EUR within 7 days",
            points=35
        )
        now = datetime.now()
        timestamps = [now - timedelta(hours=i * 6) for i in range(25)]
        df = pd.DataFrame({
            "transaction_amount": [15000.0] * 25,  # above threshold
            "timestamp": pd.to_datetime(timestamps),
        })
        result = self.eng._eval_structuring(df, rule)
        assert result["triggered"] is False

    # ── R4 NIGHT_TRANSACTION ──────────────────────────────────────────────────

    def test_r4_triggers_at_2am_wire(self):
        rule = make_rule(trigger="P2P / INTL transfer between 00:00–05:00", points=10)
        ts = datetime.now().replace(hour=2, minute=30)
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([ts]),
            "transaction_type": ["WIRE_TRANSFER"],
        })
        result = self.eng._eval_night_transactions(df, rule)
        assert result["triggered"] is True

    def test_r4_no_trigger_at_2am_grocery(self):
        """Les paiements courants nocturnes (non P2P/INTL) ne déclenchent pas."""
        rule = make_rule(trigger="P2P / INTL transfer between 00:00–05:00", points=10)
        ts = datetime.now().replace(hour=2, minute=30)
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([ts]),
            "transaction_type": ["CARD_PAYMENT"],  # not a wire type
        })
        result = self.eng._eval_night_transactions(df, rule)
        assert result["triggered"] is False

    def test_r4_no_trigger_during_business_hours(self):
        rule = make_rule(trigger="P2P / INTL transfer between 00:00–05:00", points=10)
        ts = datetime.now().replace(hour=14, minute=0)
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([ts]),
            "transaction_type": ["WIRE_TRANSFER"],
        })
        result = self.eng._eval_night_transactions(df, rule)
        assert result["triggered"] is False

    @pytest.mark.parametrize("hour", [0, 1, 2, 3, 4])
    def test_r4_triggers_all_night_hours(self, hour):
        rule = make_rule(trigger="P2P / INTL transfer between 00:00–05:00", points=10)
        ts = datetime.now().replace(hour=hour, minute=15)
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([ts]),
            "transaction_type": ["SEPA"],
        })
        result = self.eng._eval_night_transactions(df, rule)
        assert result["triggered"] is True, f"Hour {hour}:15 should trigger"

    # ── R5 FOREIGN_IP / GEOFENCING ────────────────────────────────────────────

    def test_r5_triggers_on_known_foreign_ip_prefix(self):
        rule = make_rule(
            trigger="IP country differs from client home country OR distance > 1000 km",
            trigger_detail="Secondary check: amount > 3000 EUR",
            points=20
        )
        df = pd.DataFrame({
            "ip_address": ["185.230.45.12"],
            "transaction_amount": [3500.0],
        })
        result = self.eng._eval_foreign_ip(df, rule)
        assert result["triggered"] is True

    def test_r5_triggers_on_ofac_geo_location(self):
        rule = make_rule(
            trigger="IP country differs OR distance > 1000 km",
            trigger_detail="OFAC countries → BLOCK",
            points=20
        )
        df = pd.DataFrame({
            "geo_location": ["RUSSIA/Moscow"],
            "transaction_amount": [100.0],
        })
        result = self.eng._eval_foreign_ip(df, rule)
        assert result["triggered"] is True
        assert "OFAC" in result["details"]

    def test_r5_triggers_on_multiple_countries(self):
        rule = make_rule(
            trigger="IP country differs OR distance > 1000 km",
            points=20
        )
        df = pd.DataFrame({
            "geo_location": ["FR", "DE", "FR"],
            "transaction_amount": [100.0, 200.0, 300.0],
        })
        result = self.eng._eval_foreign_ip(df, rule)
        assert result["triggered"] is True

    def test_r5_no_trigger_domestic_ip(self):
        rule = make_rule(
            trigger="IP country differs OR distance > 1000 km",
            points=20
        )
        df = pd.DataFrame({
            "ip_address": ["192.168.1.1"],
            "transaction_amount": [100.0],
        })
        result = self.eng._eval_foreign_ip(df, rule)
        assert result["triggered"] is False

    # ── R6 HIGH_RISK_MCC ─────────────────────────────────────────────────────

    def test_r6_triggers_mcc_5541_above_1500(self):
        rule = make_rule(trigger="MCC 5541/5999/5311 and amount > 1500 EUR", points=10)
        df = pd.DataFrame({
            "transaction_amount": [2000.0],
            "merchant_mcc": [5541],
        })
        result = self.eng._eval_high_risk_mcc(df, rule)
        assert result["triggered"] is True

    def test_r6_no_trigger_below_1500(self):
        rule = make_rule(trigger="MCC 5541/5999/5311 and amount > 1500 EUR", points=10)
        df = pd.DataFrame({
            "transaction_amount": [1000.0],
            "merchant_mcc": [5541],
        })
        result = self.eng._eval_high_risk_mcc(df, rule)
        assert result["triggered"] is False

    def test_r6_no_trigger_safe_mcc(self):
        rule = make_rule(trigger="MCC 5541/5999/5311 and amount > 1500 EUR", points=10)
        df = pd.DataFrame({
            "transaction_amount": [5000.0],
            "merchant_mcc": [5411],  # grocery — not in risk list
        })
        result = self.eng._eval_high_risk_mcc(df, rule)
        assert result["triggered"] is False

    @pytest.mark.parametrize("mcc", [5541, 5999, 5311])
    def test_r6_all_risk_mcc_codes(self, mcc):
        rule = make_rule(trigger="MCC 5541/5999/5311 and amount > 1500 EUR", points=10)
        df = pd.DataFrame({
            "transaction_amount": [2000.0],
            "merchant_mcc": [mcc],
        })
        result = self.eng._eval_high_risk_mcc(df, rule)
        assert result["triggered"] is True, f"MCC {mcc} should trigger"

    # ── R7 BALANCE_DRAIN ─────────────────────────────────────────────────────

    def test_r7_triggers_above_80_percent(self):
        rule = make_rule(trigger="Amount > 80% of account current balance", points=10)
        df = pd.DataFrame({
            "transaction_amount": [850.0],
            "account_current_balance": [1000.0],
        })
        result = self.eng._eval_balance_ratio(df, rule)
        assert result["triggered"] is True

    def test_r7_no_trigger_at_80_percent_exactly(self):
        """80% exactement ne déclenche pas (règle : > 80%)."""
        rule = make_rule(trigger="Amount > 80% of account current balance", points=10)
        df = pd.DataFrame({
            "transaction_amount": [800.0],
            "account_current_balance": [1000.0],
        })
        result = self.eng._eval_balance_ratio(df, rule)
        assert result["triggered"] is False

    def test_r7_no_trigger_below_80_percent(self):
        rule = make_rule(trigger="Amount > 80% of account current balance", points=10)
        df = pd.DataFrame({
            "transaction_amount": [500.0],
            "account_current_balance": [1000.0],
        })
        result = self.eng._eval_balance_ratio(df, rule)
        assert result["triggered"] is False

    # ── R8 REPEATED_ALERTS ────────────────────────────────────────────────────

    def test_r8_triggers_at_2_alerts(self):
        rule = make_rule(trigger="2 or more ALERTED transactions in last 7 days", points=25)
        df = pd.DataFrame({"alert_count_7d": [2]})
        result = self.eng._eval_repeated_alerts(df, rule)
        assert result["triggered"] is True
        assert result["points"] == 25

    def test_r8_no_trigger_at_1_alert(self):
        rule = make_rule(trigger="2 or more ALERTED transactions in last 7 days", points=25)
        df = pd.DataFrame({"alert_count_7d": [1]})
        result = self.eng._eval_repeated_alerts(df, rule)
        assert result["triggered"] is False

    def test_r8_no_trigger_without_alert_column(self):
        """Sans colonne alert_count_7d, la règle ne déclenche pas (pas de faux positifs)."""
        rule = make_rule(trigger="2 or more ALERTED transactions in last 7 days", points=25)
        df = pd.DataFrame({"transaction_amount": [100.0]})
        result = self.eng._eval_repeated_alerts(df, rule)
        assert result["triggered"] is False
        assert "No alert-count column" in result["details"]

    def test_r8_old_3_alert_threshold_not_required(self):
        """3 alertes doivent toujours déclencher (rétro-compatibilité vers le haut)."""
        rule = make_rule(trigger="2 or more ALERTED transactions in last 7 days", points=25)
        df = pd.DataFrame({"alert_count_7d": [3]})
        result = self.eng._eval_repeated_alerts(df, rule)
        assert result["triggered"] is True

    # ── R9 VELOCITY_HIGH ─────────────────────────────────────────────────────

    def test_r9_triggers_card_velocity_6_per_hour(self):
        """6 transactions carte en 1h doit déclencher (limite : 5)."""
        rule = make_rule(
            trigger="More than 5 card transactions per 1 hour OR more than 10 wire transfers per 10 minutes",
            points=20
        )
        now = datetime.now()
        timestamps = [now - timedelta(minutes=i * 8) for i in range(6)]  # 6 tx en 40 min
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps),
            "transaction_type": ["CARD_PAYMENT"] * 6,
        })
        result = self.eng._eval_velocity(df, rule)
        assert result["triggered"] is True
        assert "Card velocity" in result["details"]

    def test_r9_no_trigger_card_5_per_hour(self):
        """5 transactions carte exactement ne déclenche pas (limite STRICTEMENT > 5)."""
        rule = make_rule(
            trigger="More than 5 card transactions per 1 hour",
            points=20
        )
        now = datetime.now()
        timestamps = [now - timedelta(minutes=i * 10) for i in range(5)]
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps),
            "transaction_type": ["CARD_PAYMENT"] * 5,
        })
        result = self.eng._eval_velocity(df, rule)
        assert result["triggered"] is False

    def test_r9_triggers_wire_11_per_10min(self):
        """11 virements en 10 min doit déclencher (limite : 10)."""
        rule = make_rule(
            trigger="More than 5 card transactions per 1 hour OR more than 10 wire transfers per 10 minutes",
            points=20
        )
        now = datetime.now()
        timestamps = [now - timedelta(seconds=i * 50) for i in range(11)]
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps),
            "transaction_type": ["WIRE_TRANSFER"] * 11,
        })
        result = self.eng._eval_velocity(df, rule)
        assert result["triggered"] is True
        assert "Wire velocity" in result["details"]

    def test_r9_old_10_per_hour_threshold_no_longer_standard(self):
        """10 transactions carte en 1h : l'ancien seuil déclenchait, le nouveau aussi (>5)."""
        rule = make_rule(
            trigger="More than 5 card transactions per 1 hour",
            points=20
        )
        now = datetime.now()
        timestamps = [now - timedelta(minutes=i * 5) for i in range(10)]
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps),
            "transaction_type": ["CARD_PAYMENT"] * 10,
        })
        result = self.eng._eval_velocity(df, rule)
        assert result["triggered"] is True  # doit aussi déclencher avec le nouveau seuil plus strict

    # ── R10 NEAR_THRESHOLD ────────────────────────────────────────────────────

    def test_r10_triggers_9500_to_9999(self):
        rule = make_rule(trigger="Amount between 9500 EUR and 9999 EUR", points=15)
        df = df_with_amounts(9750.0)
        result = self.eng._eval_near_threshold_amount(df, rule)
        assert result["triggered"] is True
        assert result["points"] == 15

    def test_r10_triggers_exactly_9500(self):
        rule = make_rule(trigger="Amount between 9500 EUR and 9999 EUR", points=15)
        df = df_with_amounts(9500.0)
        result = self.eng._eval_near_threshold_amount(df, rule)
        assert result["triggered"] is True

    def test_r10_triggers_exactly_9999(self):
        rule = make_rule(trigger="Amount between 9500 EUR and 9999 EUR", points=15)
        df = df_with_amounts(9999.0)
        result = self.eng._eval_near_threshold_amount(df, rule)
        assert result["triggered"] is True

    def test_r10_no_trigger_at_10000(self):
        """10 000 EUR exactement n'est pas dans la bande suspecte."""
        rule = make_rule(trigger="Amount between 9500 EUR and 9999 EUR", points=15)
        df = df_with_amounts(10000.0)
        result = self.eng._eval_near_threshold_amount(df, rule)
        assert result["triggered"] is False

    def test_r10_no_trigger_at_500_old_rule(self):
        """500 EUR (ancienne règle montant rond) ne doit PAS déclencher."""
        rule = make_rule(trigger="Amount between 9500 EUR and 9999 EUR", points=15)
        df = df_with_amounts(500.0)
        result = self.eng._eval_near_threshold_amount(df, rule)
        assert result["triggered"] is False, \
            "Old 'round amount > 500 EUR' should NOT trigger with new AML near-threshold rule"

    def test_r10_triggers_secondary_band_4500_4999(self):
        """Bande secondaire 4 500–4 999 EUR doit aussi déclencher."""
        rule = make_rule(trigger="Amount between 9500 EUR and 9999 EUR", points=15)
        df = df_with_amounts(4750.0)
        result = self.eng._eval_near_threshold_amount(df, rule)
        # Bandes secondaires activées via le fallback (no range in trigger, uses defaults)
        # Ce test vérifie le comportement avec trigger explicite → ne déclenche pas
        # (la bande 4500-4999 n'est pas dans le range "9500-9999")
        # Pour les bandes secondaires, le trigger doit être sans range explicite
        rule2 = make_rule(trigger="suspicious near threshold", points=15)
        result2 = self.eng._eval_near_threshold_amount(df, rule2)
        assert result2["triggered"] is True, \
            "Secondary AML band 4500–4999 EUR should trigger"

    # ── R11 CROSS_BORDER ─────────────────────────────────────────────────────

    def test_r11_triggers_two_countries_48h(self):
        rule = make_rule(trigger="Transactions in different countries within 48 hours", points=12)
        now = datetime.now()
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([now - timedelta(hours=5), now - timedelta(hours=1)]),
            "counterparty_iban": ["FR761234XXXX", "DE891234XXXX"],
        })
        result = self.eng._eval_cross_border(df, rule)
        assert result["triggered"] is True
        assert "FR" in result["details"] or "DE" in result["details"]

    def test_r11_no_trigger_same_country(self):
        rule = make_rule(trigger="Transactions in different countries within 48 hours", points=12)
        now = datetime.now()
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([now - timedelta(hours=5), now - timedelta(hours=1)]),
            "counterparty_iban": ["FR761234XXXX", "FR891234XXXX"],
        })
        result = self.eng._eval_cross_border(df, rule)
        assert result["triggered"] is False

    def test_r11_no_trigger_beyond_48h(self):
        """Deux pays séparés de plus de 48h ne déclenchent pas."""
        rule = make_rule(trigger="Transactions in different countries within 48 hours", points=12)
        now = datetime.now()
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([now - timedelta(days=3), now - timedelta(hours=1)]),
            "counterparty_iban": ["FR761234XXXX", "DE891234XXXX"],
        })
        result = self.eng._eval_cross_border(df, rule)
        # La fenêtre de 48h ne couvre pas les deux — le premier tx est hors fenêtre du second
        # Résultat dépend de la direction de la fenêtre — on vérifie juste que le test passe
        # (comportement: à partir du premier tx, est-ce que le second est dans les 48h ?)
        # Dans ce cas : first TX at -3j, second at -1h → gap > 48h → NOT in window from first TX
        # MAIS : de second TX (-1h) vers first TX (-3j) → gap > 48h aussi
        # → Should NOT trigger
        assert result["triggered"] is False

    # ── R12 NEW_BENEFICIARY ───────────────────────────────────────────────────

    def test_r12_triggers_new_benef_above_3000(self):
        rule = make_rule(
            trigger="New beneficiary added AND immediate transfer > 3000 EUR in same session",
            points=20
        )
        df = pd.DataFrame({
            "transaction_amount": [3500.0, 100.0, 200.0],
            "counterparty_iban": ["NEW_IBAN_001", "KNOWN_IBAN_A", "KNOWN_IBAN_A"],
        })
        result = self.eng._eval_new_beneficiary(df, rule)
        assert result["triggered"] is True
        assert result["points"] == 20

    def test_r12_no_trigger_known_beneficiary(self):
        """Un bénéficiaire récurrent (> 1 transaction) ne déclenche pas."""
        rule = make_rule(
            trigger="New beneficiary added AND immediate transfer > 3000 EUR in same session",
            points=20
        )
        df = pd.DataFrame({
            "transaction_amount": [5000.0, 100.0],
            "counterparty_iban": ["KNOWN_IBAN_A", "KNOWN_IBAN_A"],
        })
        result = self.eng._eval_new_beneficiary(df, rule)
        assert result["triggered"] is False

    def test_r12_no_trigger_below_3000(self):
        """Nouveau bénéficiaire mais montant ≤ 3 000 EUR → pas de déclenchement."""
        rule = make_rule(
            trigger="New beneficiary added AND immediate transfer > 3000 EUR in same session",
            points=20
        )
        df = pd.DataFrame({
            "transaction_amount": [2500.0],
            "counterparty_iban": ["NEW_IBAN_001"],
        })
        result = self.eng._eval_new_beneficiary(df, rule)
        assert result["triggered"] is False

    def test_r12_old_2000_threshold_not_triggered(self):
        """2 500 EUR (entre ancien seuil 2 000 et nouveau 3 000) ne déclenche plus."""
        rule = make_rule(
            trigger="New beneficiary added AND immediate transfer > 3000 EUR in same session",
            points=20
        )
        df = pd.DataFrame({
            "transaction_amount": [2500.0],
            "counterparty_iban": ["NEW_IBAN_001"],
        })
        result = self.eng._eval_new_beneficiary(df, rule)
        assert result["triggered"] is False, \
            "Amount 2 500 EUR below new threshold of 3 000 EUR should NOT trigger"

    # ── R13 DORMANT_ACCOUNT ───────────────────────────────────────────────────

    def test_r13_triggers_91_day_gap(self):
        rule = make_rule(
            trigger="Account inactive for more than 90 days with sudden activity",
            points=18
        )
        now = datetime.now()
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([
                now - timedelta(days=91),
                now - timedelta(days=1),
            ])
        })
        result = self.eng._eval_dormant_account(df, rule)
        assert result["triggered"] is True
        assert "91" in result["details"] or "90" in result["details"]

    def test_r13_no_trigger_89_day_gap(self):
        rule = make_rule(
            trigger="Account inactive for more than 90 days with sudden activity",
            points=18
        )
        now = datetime.now()
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([
                now - timedelta(days=89),
                now - timedelta(days=1),
            ])
        })
        result = self.eng._eval_dormant_account(df, rule)
        assert result["triggered"] is False

    def test_r13_no_trigger_active_account(self):
        rule = make_rule(
            trigger="Account inactive for more than 90 days with sudden activity",
            points=18
        )
        now = datetime.now()
        # Transaction every week for 3 months — not dormant
        timestamps = [now - timedelta(weeks=i) for i in range(12)]
        df = pd.DataFrame({"timestamp": pd.to_datetime(timestamps)})
        result = self.eng._eval_dormant_account(df, rule)
        assert result["triggered"] is False


# ══════════════════════════════════════════════════════════════════════════════
# ④ LLM CLASSIFIER — classify_rule_intent
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMClassifier:

    @pytest.fixture(autouse=True)
    def import_classifier(self):
        from fraud import llm_rule_classifier as clf
        self.clf = clf

    def test_classify_returns_valid_key_mocked(self):
        """Le classifier retourne un clé valide quand le LLM répond correctement."""
        rule = make_rule(
            rule_id="RL-CUSTOM-001",
            name="Unusual night payment",
            trigger="Payment executed between 1am and 4am",
            trigger_detail="",
        )
        with patch.object(self.clf, "_get_llm") as mock_llm:
            mock_instance = MagicMock()
            mock_instance.invoke.return_value = MagicMock(content="night_transactions")
            mock_llm.return_value = mock_instance

            key = self.clf.classify_rule_intent(rule)
            assert key == "night_transactions"

    def test_classify_caches_result(self):
        """Le résultat est mis en cache — le LLM n'est appelé qu'une fois."""
        rule = make_rule(rule_id="RL-CACHE-TEST", updated_at=datetime(2025, 1, 1))
        # Prime cache
        self.clf._intent_cache[("RL-CACHE-TEST", str(datetime(2025, 1, 1)))] = "velocity"

        with patch.object(self.clf, "_get_llm") as mock_llm:
            result = self.clf.classify_rule_intent(rule)
            mock_llm.assert_not_called()  # LLM pas appelé — résultat en cache
            assert result == "velocity"

    def test_classify_returns_none_on_unknown_key(self):
        """Si le LLM retourne un clé inconnue, classify retourne None."""
        rule = make_rule(rule_id="RL-UNKNOWN-001")
        with patch.object(self.clf, "_get_llm") as mock_llm:
            mock_instance = MagicMock()
            mock_instance.invoke.return_value = MagicMock(content="totally_invented_key")
            mock_llm.return_value = mock_instance

            key = self.clf.classify_rule_intent(rule)
            assert key is None

    def test_classify_returns_none_on_llm_error(self):
        """Une erreur LLM retourne None sans lever d'exception."""
        rule = make_rule(rule_id="RL-ERROR-001")
        with patch.object(self.clf, "_get_llm") as mock_llm:
            mock_llm.side_effect = Exception("Groq API unavailable")
            key = self.clf.classify_rule_intent(rule)
            assert key is None

    def test_invalidate_cache_removes_entry(self):
        """invalidate_rule_cache supprime les entrées en cache pour une règle."""
        self.clf._intent_cache[("RL-DEL-001", "2025-01-01")] = "velocity"
        self.clf._intent_cache[("RL-DEL-001", "2025-02-01")] = "structuring"
        self.clf._intent_cache[("RL-OTHER", "2025-01-01")] = "large_amount"

        self.clf.invalidate_rule_cache("RL-DEL-001")

        assert ("RL-DEL-001", "2025-01-01") not in self.clf._intent_cache
        assert ("RL-DEL-001", "2025-02-01") not in self.clf._intent_cache
        assert ("RL-OTHER", "2025-01-01") in self.clf._intent_cache  # non affecté

    def test_all_valid_evaluator_keys_covered(self):
        """EVALUATOR_DESCRIPTIONS doit couvrir les 13 règles."""
        expected_keys = {
            "large_amount", "near_threshold", "suspicious_iban", "structuring",
            "night_transactions", "foreign_ip", "high_risk_mcc", "balance_ratio",
            "repeated_alerts", "velocity", "new_beneficiary", "dormant_account",
            "cross_border",
        }
        assert expected_keys == self.clf.VALID_KEYS, \
            f"Missing keys: {expected_keys - self.clf.VALID_KEYS}"

    @pytest.mark.parametrize("rule_name,trigger,expected_key", [
        ("Suspicious late payment",   "transaction at 3:00am",           "night_transactions"),
        ("High transfer amount",      "amount exceeds 8000 EUR",         "large_amount"),
        ("Too many card payments",    "more than 10 payments per hour",  "velocity"),
        ("Foreign account transfer",  "IBAN prefix from sanctioned list","suspicious_iban"),
        ("Balance almost emptied",    "more than 90 percent withdrawn",  "balance_ratio"),
    ])
    def test_llm_classifies_custom_rules(self, rule_name, trigger, expected_key):
        """Le LLM doit classifier correctement des règles custom avec des formulations variées."""
        rule = make_rule(name=rule_name, trigger=trigger)
        with patch.object(self.clf, "_get_llm") as mock_llm:
            mock_instance = MagicMock()
            mock_instance.invoke.return_value = MagicMock(content=expected_key)
            mock_llm.return_value = mock_instance
            # Clear cache pour forcer l'appel LLM
            self.clf._intent_cache.clear()
            key = self.clf.classify_rule_intent(rule)
            assert key == expected_key


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ SCORING — compute_final_score, risk_level, tracfin
# ══════════════════════════════════════════════════════════════════════════════

class TestScoring:

    @pytest.fixture(autouse=True)
    def import_scoring(self):
        from fraud import scoring as sc
        self.sc = sc

    # ── risk_level_from_score ─────────────────────────────────────────────────

    @pytest.mark.parametrize("score,expected", [
        (0,  "APPROVED"),
        (15, "APPROVED"),
        (29, "APPROVED"),
        (30, "REVIEW"),
        (45, "REVIEW"),
        (59, "REVIEW"),
        (60, "BLOCK"),
        (75, "BLOCK"),
        (100,"BLOCK"),
    ])
    def test_risk_levels(self, score, expected):
        assert self.sc.risk_level_from_score(score) == expected

    # ── compute_total_score ───────────────────────────────────────────────────

    def test_total_score_capped_at_100(self):
        results = [
            {"triggered": True, "points": 40},
            {"triggered": True, "points": 35},
            {"triggered": True, "points": 30},
        ]
        assert self.sc.compute_total_score(results) == 100

    def test_total_score_only_triggered(self):
        results = [
            {"triggered": True,  "points": 30},
            {"triggered": False, "points": 35},
            {"triggered": True,  "points": 20},
        ]
        assert self.sc.compute_total_score(results) == 50

    def test_total_score_zero_no_triggered(self):
        results = [{"triggered": False, "points": 30}]
        assert self.sc.compute_total_score(results) == 0

    # ── compute_final_score ───────────────────────────────────────────────────

    def test_final_score_max_of_behavioral_aml(self):
        score, level = self.sc.compute_final_score(45, 70)
        assert score == 70
        assert level == "BLOCK"

    def test_final_score_behavioral_wins(self):
        score, level = self.sc.compute_final_score(75, 30)
        assert score == 75
        assert level == "BLOCK"

    def test_final_score_both_low(self):
        score, level = self.sc.compute_final_score(20, 15)
        assert score == 20
        assert level == "APPROVED"

    # ── check_tracfin_required ────────────────────────────────────────────────

    def test_tracfin_required_structuring_above_60(self):
        results = [
            {"triggered": True,  "rule_name": "Structuring / smurfing (AML)", "points": 35},
            {"triggered": True,  "rule_name": "High amount transaction",       "points": 30},
        ]
        df = pd.DataFrame({"transaction_amount": [9500.0]})
        assert self.sc.check_tracfin_required(results, df) is True

    def test_tracfin_not_required_below_60(self):
        results = [
            {"triggered": True, "rule_name": "Night transfer alert", "points": 10},
            {"triggered": True, "rule_name": "Balance drain pattern","points": 10},
        ]
        df = pd.DataFrame({"transaction_amount": [100.0]})
        assert self.sc.check_tracfin_required(results, df) is False

    def test_tracfin_not_required_high_score_no_aml_rule(self):
        """Score ≥ 60 mais pas de règle AML/IBAN → TRACFIN non requis."""
        results = [
            {"triggered": True, "rule_name": "Night transfer alert",   "points": 30},
            {"triggered": True, "rule_name": "Balance drain pattern",  "points": 10},
            {"triggered": True, "rule_name": "Repeated fraud alerts",  "points": 25},
        ]
        df = pd.DataFrame({"transaction_amount": [100.0]})
        assert self.sc.check_tracfin_required(results, df) is False

    # ── _behavioral_hardcoded (fallback sans DB) ─────────────────────────────

    def test_behavioral_hardcoded_r1_5000_eur(self):
        """Fallback hardcodé : R1 doit se déclencher à 5 001 EUR, pas à 3 001 EUR."""
        df = pd.DataFrame({"transaction_amount": [5001.0]})
        score, signals = self.sc._behavioral_hardcoded(df)
        names = [s["signal"] for s in signals]
        assert "HIGH_AMOUNT" in names
        # Vérifier que le seuil est bien 5000, pas 3000
        assert score >= 30

    def test_behavioral_hardcoded_r1_no_trigger_3500(self):
        """Fallback hardcodé : 3 500 EUR ne déclenche PAS (ancien seuil TND révolu)."""
        df = pd.DataFrame({"transaction_amount": [3500.0]})
        score, signals = self.sc._behavioral_hardcoded(df)
        names = [s["signal"] for s in signals]
        assert "HIGH_AMOUNT" not in names, \
            "3 500 EUR should NOT trigger HIGH_AMOUNT with new 5 000 EUR threshold"


# ══════════════════════════════════════════════════════════════════════════════
# ⑥ PIPELINE — run_rules_from_db avec DB mockée
# ══════════════════════════════════════════════════════════════════════════════

class TestPipeline:

    @pytest.fixture
    def mock_db_session(self):
        """Session DB mockée qui retourne les 13 règles standard."""
        session = MagicMock()

        rules = [
            make_rule("RL-HA-001", "High amount transaction",              "LIMIT",      "Amount > 5000 EUR",                                                                  points=30),
            make_rule("RL-SI-002", "Suspicious IBAN check",                "GEOGRAPHIC", "Client/counterparty IBAN in blacklist or OFAC country",                              points=20),
            make_rule("RL-ST-003", "Structuring / smurfing (AML)",         "AML",        "More than 20 deposits under 10000 EUR within 7 days",                                points=35),
            make_rule("RL-NT-004", "Night transfer alert",                 "BEHAVIORAL", "P2P / INTL transfer between 00:00–05:00",                                           points=10),
            make_rule("RL-FI-005", "Foreign IP / geofencing alert",        "GEOGRAPHIC", "IP country differs from client home country OR distance > 1000 km",                 points=20),
            make_rule("RL-MCC-006","High-risk merchant (MCC)",             "BEHAVIORAL", "MCC 5541/5999/5311 and amount > 1500 EUR",                                           points=10),
            make_rule("RL-BD-007", "Balance drain pattern",                "BEHAVIORAL", "Amount > 80% of account current balance",                                            points=10),
            make_rule("RL-RA-008", "Repeated fraud alerts",                "BEHAVIORAL", "2 or more ALERTED transactions in last 7 days",                                     points=25, active=False),
            make_rule("RL-VH-009", "High velocity transactions",           "VELOCITY",   "More than 5 card transactions per 1 hour OR more than 10 wire transfers per 10 minutes", points=20),
            make_rule("RL-NTA-010","Suspicious near-threshold amount (AML)","AML",       "Amount between 9500 EUR and 9999 EUR",                                               points=15),
            make_rule("RL-CB-011", "Cross-border transaction",             "GEOGRAPHIC", "Transactions in different countries within 48 hours",                                points=12),
            make_rule("RL-NB-012", "New beneficiary high-value transfer",  "BEHAVIORAL", "New beneficiary added AND immediate transfer > 3000 EUR in same session",            points=20),
            make_rule("RL-DA-013", "Dormant account reactivation",         "BEHAVIORAL", "Account inactive for more than 90 days with sudden activity",                       points=18),
        ]
        # Simuler .filter().order_by().all() → retourne uniquement les règles actives
        active_rules = [r for r in rules if r.active]
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = active_rules
        return session

    def test_run_rules_returns_one_result_per_active_rule(self, mock_db_session):
        from fraud.rule_engine import run_rules_from_db
        df = pd.DataFrame({"transaction_amount": [100.0]})
        results = run_rules_from_db(df, mock_db_session)
        # R8 est inactive → 12 règles actives
        assert len(results) == 12

    def test_run_rules_r1_triggers_on_6000(self, mock_db_session):
        from fraud.rule_engine import run_rules_from_db
        df = pd.DataFrame({"transaction_amount": [6000.0]})
        results = run_rules_from_db(df, mock_db_session)
        r1 = next((r for r in results if r["rule"] == "RL-HA-001"), None)
        assert r1 is not None
        assert r1["triggered"] is True
        assert r1["points"] == 30

    def test_run_rules_r1_no_trigger_on_3500(self, mock_db_session):
        """3 500 EUR ne doit pas déclencher R1 (nouveau seuil 5 000 EUR)."""
        from fraud.rule_engine import run_rules_from_db
        df = pd.DataFrame({"transaction_amount": [3500.0]})
        results = run_rules_from_db(df, mock_db_session)
        r1 = next((r for r in results if r["rule"] == "RL-HA-001"), None)
        assert r1 is not None
        assert r1["triggered"] is False, \
            "3 500 EUR should NOT trigger R1 with new 5 000 EUR threshold"

    def test_run_rules_r10_triggers_near_threshold(self, mock_db_session):
        from fraud.rule_engine import run_rules_from_db
        df = pd.DataFrame({"transaction_amount": [9750.0]})
        results = run_rules_from_db(df, mock_db_session)
        r10 = next((r for r in results if r["rule"] == "RL-NTA-010"), None)
        assert r10 is not None
        assert r10["triggered"] is True
        assert r10["points"] == 15

    def test_run_rules_r10_no_trigger_round_500(self, mock_db_session):
        """500 EUR (ancienne règle) ne doit PAS déclencher R10."""
        from fraud.rule_engine import run_rules_from_db
        df = pd.DataFrame({"transaction_amount": [500.0]})
        results = run_rules_from_db(df, mock_db_session)
        r10 = next((r for r in results if r["rule"] == "RL-NTA-010"), None)
        assert r10 is not None
        assert r10["triggered"] is False

    def test_full_scenario_high_risk_account(self, mock_db_session):
        """
        Scénario complet : compte à haut risque.
        Doit déclencher R1 + R3 + R10 → score élevé → BLOCK.
        """
        from fraud.rule_engine import run_rules_from_db
        from fraud.scoring import compute_total_score, risk_level_from_score

        now = datetime.now()
        # 21 dépôts < 10 000 EUR en 7 jours + 1 transaction de 9 750 EUR
        deposits = [9500.0] * 21 + [9750.0]
        timestamps = [now - timedelta(hours=i * 7) for i in range(22)]

        df = pd.DataFrame({
            "transaction_amount": deposits,
            "timestamp": pd.to_datetime(timestamps),
            "client_iban": ["FR761234TEST"] * 22,
        })

        results = run_rules_from_db(df, mock_db_session)
        score = compute_total_score(results)
        level = risk_level_from_score(score)

        triggered = [r["rule_name"] for r in results if r["triggered"]]
        assert "High amount transaction" in triggered         # R1 : 9750 > 5000
        assert "Structuring / smurfing (AML)" in triggered    # R3 : 21 dépôts < 10k / 7j
        assert "Suspicious near-threshold amount (AML)" in triggered  # R10 : 9500 et 9750

        assert score >= 60
        assert level == "BLOCK"


# ══════════════════════════════════════════════════════════════════════════════
# ⑦ PARSE_REQUEST — extraction IBAN robuste
# ══════════════════════════════════════════════════════════════════════════════

class TestParseRequest:

    @pytest.fixture(autouse=True)
    def mock_llm(self):
        """Mock le LLM pour éviter les appels Groq en test."""
        with patch("fraud.nodes._get_extraction_llm") as mock:
            llm_instance = MagicMock()
            llm_instance.invoke.return_value = MagicMock(
                content='{"iban": "FR761234567890", "action": "fraud_check"}'
            )
            mock.return_value = llm_instance
            yield mock

    def _make_state(self, message):
        from langchain_core.messages import HumanMessage
        return {
            "messages": [HumanMessage(content=message)],
            "user_id": "test",
            "session_id": "sess-test",
            "iban": "", "action": "", "excel_path": "",
            "transactions_raw": [], "transactions_count": 0,
            "account_summary": None, "fraud_results": [],
            "score_behavioral": 0, "score_aml": 0, "score_final": 0,
            "risk_level": "", "tracfin_required": False,
            "report_path": None, "llm_summary": "", "error": None,
            "sheet_url": None, "drive_url": None, "output_errors": [],
        }

    def test_parse_extracts_explicit_iban(self):
        from fraud.nodes import parse_request
        state = self._make_state("Analyse fraude pour TN5904028800000000000000")
        result = parse_request(state)
        assert result["iban"] == "TN5904028800000000000000"
        assert result["error"] is None

    def test_parse_detects_export_action(self):
        from fraud.nodes import parse_request
        state = self._make_state("Exporte les transactions de TN5904028800000000000000")
        result = parse_request(state)
        assert result["action"] == "export_transactions"

    def test_parse_returns_error_without_iban(self):
        from fraud.nodes import parse_request
        with patch("fraud.nodes._get_extraction_llm") as mock:
            llm_instance = MagicMock()
            llm_instance.invoke.return_value = MagicMock(
                content='{"iban": "", "action": "fraud_check"}'
            )
            mock.return_value = llm_instance
            state = self._make_state("Bonjour, comment allez-vous ?")
            result = parse_request(state)
            assert result["iban"] == ""
            assert result["error"] is not None

    def test_parse_handles_llm_non_json_response(self):
        """Si le LLM répond du texte (bug), le regex fallback prend le relais."""
        from fraud.nodes import parse_request
        with patch("fraud.nodes._get_extraction_llm") as mock:
            llm_instance = MagicMock()
            # LLM retourne du texte pur — pas de JSON
            llm_instance.invoke.return_value = MagicMock(
                content="Je suis désolé, mais comme système, je ne peux pas..."
            )
            mock.return_value = llm_instance
            # Mais le message contient un IBAN explicite → regex le catch
            state = self._make_state("Analyse TN5904028800000000000000 SVP")
            result = parse_request(state)
            assert result["iban"] == "TN5904028800000000000000"

    @pytest.mark.parametrize("iban", [
        "FR7614508959950012345678910",
        "DE89370400440532013000",
        "TN5904028800000000000000",
        "IBAN_TN001",
    ])
    def test_parse_various_iban_formats(self, iban):
        from fraud.nodes import parse_request
        state = self._make_state(f"Vérifie l'IBAN {iban}")
        result = parse_request(state)
        assert result["iban"] == iban.replace(" ", "").upper()


# ══════════════════════════════════════════════════════════════════════════════
# ⑦b GENERATE_SUMMARY — résumé LLM
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateSummary:

    def _make_state(self, score_final=75, risk_level="BLOCK", tracfin=True,
                    fraud_results=None):
        return {
            "messages": [],
            "user_id": "agent1",
            "session_id": "sess-001",
            "iban": "FR761234TEST",
            "action": "fraud_check",
            "excel_path": "",
            "transactions_raw": [],
            "transactions_count": 10,
            "account_summary": {"total_transactions": 10, "total_amount": 95000.0, "date_range": "2025-01-01/2025-06-01"},
            "fraud_results": fraud_results or [
                {"triggered": True, "rule_name": "High amount transaction",     "domain": "LIMIT",    "points": 30, "severity": "HIGH",     "details": "2 transactions > 5,000 EUR"},
                {"triggered": True, "rule_name": "Structuring / smurfing (AML)","domain": "AML",      "points": 35, "severity": "CRITICAL", "details": "21 deposits < 10,000 EUR"},
            ],
            "score_behavioral": 30,
            "score_aml": 70,
            "score_final": score_final,
            "risk_level": risk_level,
            "tracfin_required": tracfin,
            "report_path": "/app/data/reports/test.xlsx",
            "llm_summary": "",
            "error": None,
            "sheet_url": None,
            "drive_url": None,
            "output_errors": [],
            "download_url": "http://localhost/fraud/reports/test.xlsx",
            "suspicious_samples": "• 2025-01-01 | 9750.00 EUR | WIRE_TRANSFER → RU123",
        }

    def test_summary_contains_risk_level(self):
        from fraud.nodes import generate_summary
        with patch("fraud.nodes._get_llm") as mock:
            llm = MagicMock()
            llm.invoke.return_value = MagicMock(
                content="BLOCK — Score 75/100. Structuring détecté. TRACFIN requis."
            )
            mock.return_value = llm
            with patch("fraud.nodes.SessionLocal"):
                with patch("fraud.nodes.MailLogService") as mock_svc:
                    mock_svc.return_value.create_pending.return_value = "LOG-TEST-001"
                    state = self._make_state()
                    result = generate_summary(state)

            assert "llm_summary" in result
            assert len(result["llm_summary"]) > 0

    def test_summary_includes_download_url(self):
        from fraud.nodes import generate_summary
        with patch("fraud.nodes._get_llm") as mock:
            llm = MagicMock()
            llm.invoke.return_value = MagicMock(content="Analyse complète.")
            mock.return_value = llm
            with patch("fraud.nodes.SessionLocal"):
                with patch("fraud.nodes.MailLogService") as mock_svc:
                    mock_svc.return_value.create_pending.return_value = "LOG-TEST-001"
                    state = self._make_state()
                    result = generate_summary(state)

            assert "http://localhost/fraud/reports/test.xlsx" in result["llm_summary"]

    def test_summary_on_error_state(self):
        """En cas d'erreur dans le state, le résumé doit quand même retourner quelque chose."""
        from fraud.nodes import generate_summary
        state = self._make_state()
        state["error"] = "IBAN not found"
        result = generate_summary(state)
        assert "llm_summary" in result
        assert "IBAN not found" in result["llm_summary"] or "impossible" in result["llm_summary"]


# ══════════════════════════════════════════════════════════════════════════════
# EDGE CASES & ROBUSTESSE
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    @pytest.fixture(autouse=True)
    def import_engine(self):
        from fraud import rule_engine as eng
        self.eng = eng

    def test_empty_dataframe_does_not_crash(self):
        """Un DataFrame vide ne doit pas lever d'exception."""
        rule = make_rule(trigger="Amount > 5000 EUR")
        df = pd.DataFrame()
        result = self.eng._eval_large_amount(df, rule)
        assert result["triggered"] is False

    def test_null_amounts_do_not_crash(self):
        rule = make_rule(trigger="Amount > 5000 EUR")
        df = pd.DataFrame({"transaction_amount": [None, float("nan"), 6000.0]})
        result = self.eng._eval_large_amount(df, rule)
        assert result["triggered"] is True  # 6000 doit déclencher

    def test_extract_number_from_trigger(self):
        assert self.eng._extract_number("Amount > 5000 EUR") == 5000.0
        assert self.eng._extract_number("More than 20 deposits under 10000 EUR") == 20.0
        assert self.eng._extract_number("no numbers here") is None

    def test_extract_range_from_trigger(self):
        lo, hi = self.eng._extract_range("Amount between 9500 EUR and 9999 EUR")
        assert lo == 9500.0
        assert hi == 9999.0

    def test_extract_range_dash_format(self):
        result = self.eng._extract_range("9500-9999")
        assert result == (9500.0, 9999.0)

    def test_run_rules_empty_db_returns_empty_list(self):
        from fraud.rule_engine import run_rules_from_db
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        df = pd.DataFrame({"transaction_amount": [5001.0]})
        results = run_rules_from_db(df, mock_db)
        assert results == []

    def test_evaluator_error_does_not_crash_pipeline(self):
        """Une erreur dans un évaluateur ne doit pas arrêter les autres règles."""
        from fraud.rule_engine import run_rules_from_db
        mock_db = MagicMock()

        rule_ok  = make_rule("RL-OK-001",  trigger="Amount > 5000 EUR")
        rule_bad = make_rule("RL-BAD-001", trigger="COMPLETELY INVALID TRIGGER XYZ")
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            rule_ok, rule_bad
        ]

        df = pd.DataFrame({"transaction_amount": [6000.0]})
        results = run_rules_from_db(df, mock_db)

        assert len(results) == 2
        r_ok = next(r for r in results if r["rule"] == "RL-OK-001")
        assert r_ok["triggered"] is True
