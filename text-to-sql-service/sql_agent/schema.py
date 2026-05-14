"""
Database schema definition for the Text-to-SQL Agent.

Declares the whitelisted tables, their columns, and business descriptions
that are injected into the LLM prompt to guide SQL generation.
Only tables in ALLOWED_TABLES can be queried.
"""

# ── Whitelisted tables ────────────────────────────────────────────────────────
# Any table NOT in this set will be blocked by the validator.

ALLOWED_TABLES = frozenset({
    "transactions",
    "fraud_rules",
    "fraud_decision_logs",
})

# ── Column whitelist per table (used for SELECT * rewriting + hallucination check)
ALLOWED_COLUMNS: dict[str, list[str]] = {
    "transactions": [
        "id", "transaction_id", "user_id", "amount", "currency",
        "transaction_type", "status", "fraud_score", "is_fraudulent",
        "merchant_name", "merchant_category", "created_at", "updated_at",
    ],
    "fraud_rules": [
        "id", "name", "domain", "trigger", "trigger_detail",
        "points", "severity", "active", "description",
        "created_at", "updated_at",
    ],
    "fraud_decision_logs": [
        "id", "created_at", "user_id", "session_id", "iban",
        "transactions_count", "date_range",
        "score_behavioral", "score_aml", "score_final",
        "risk_level", "tracfin_required",
        "rules_triggered", "rules_evaluated", "triggered_rules_detail",
        "report_path", "download_url",
        "mail_sent", "mail_recipient", "mail_template", "mail_status", "mail_id",
        "llm_summary", "error",
    ],
}

# ── Full schema description for the LLM prompt ────────────────────────────────
# Each table entry has: description, columns (name, type, description)

SCHEMA_DESCRIPTION = """
You have READ-ONLY access to the following PostgreSQL tables in the 'banking_data' database:

══════════════════════════════════════════════════════════
TABLE: transactions
Description: Bank transactions with fraud scoring
══════════════════════════════════════════════════════════
Columns:
  - id               SERIAL          Primary key
  - transaction_id   VARCHAR(255)    Unique transaction identifier
  - user_id          VARCHAR(255)    User/client identifier
  - amount           DECIMAL(15,2)   Transaction amount in EUR
  - currency         VARCHAR(3)      Currency code (default: EUR)
  - transaction_type VARCHAR(50)     Type: transfer, payment, withdrawal, deposit...
  - status           VARCHAR(20)     Status: pending, completed, failed, blocked
  - fraud_score      DECIMAL(5,4)    ML fraud score between 0 and 1 (higher = more suspicious)
  - is_fraudulent    BOOLEAN         Whether flagged as fraudulent
  - merchant_name    VARCHAR(255)    Merchant or counterparty name
  - merchant_category VARCHAR(100)   Merchant category (e.g. gambling, luxury, crypto)
  - created_at       TIMESTAMP       Transaction creation date/time
  - updated_at       TIMESTAMP       Last update date/time

══════════════════════════════════════════════════════════
TABLE: fraud_rules
Description: Fraud detection rules managed by compliance team
══════════════════════════════════════════════════════════
Columns:
  - id               VARCHAR(64)     Rule identifier
  - name             VARCHAR(255)    Rule name
  - domain           VARCHAR(64)     Domain: VELOCITY, LIMIT, GEOGRAPHIC, AML, BEHAVIORAL
  - trigger          VARCHAR(512)    Trigger condition description
  - trigger_detail   VARCHAR(512)    Additional trigger details
  - points           INTEGER         Risk score points (0-100) awarded when triggered
  - severity         VARCHAR(32)     Severity: LOW, MEDIUM, HIGH, CRITICAL
  - active           BOOLEAN         Whether rule is currently enabled
  - description      TEXT            Full rule description
  - created_at       TIMESTAMPTZ     Rule creation date
  - updated_at       TIMESTAMPTZ     Last modification date

══════════════════════════════════════════════════════════
TABLE: fraud_decision_logs
Description: Historical log of fraud analysis decisions per IBAN
══════════════════════════════════════════════════════════
Columns:
  - id                     VARCHAR(64)   Decision log identifier
  - created_at             TIMESTAMPTZ   Analysis date/time
  - user_id                VARCHAR(128)  Analyst user id
  - session_id             VARCHAR(128)  Chat session identifier
  - iban                   VARCHAR(64)   Analyzed IBAN
  - transactions_count     INTEGER       Number of transactions analyzed
  - date_range             VARCHAR(64)   Date range of analysis
  - score_behavioral       INTEGER       Behavioral risk score (0-100)
  - score_aml              INTEGER       AML risk score (0-100)
  - score_final            INTEGER       Final composite risk score (0-100)
  - risk_level             VARCHAR(32)   Decision: APPROVED, REVIEW, HOLD, BLOCK
  - tracfin_required       BOOLEAN       Whether TRACFIN reporting is required
  - rules_triggered        INTEGER       Number of fraud rules triggered
  - rules_evaluated        INTEGER       Total rules evaluated
  - triggered_rules_detail JSONB         Details of triggered rules
  - report_path            VARCHAR(512)  Path to generated Excel report
  - download_url           VARCHAR(512)  Public download URL for the report
  - mail_sent              BOOLEAN       Whether alert email was sent
  - mail_recipient         VARCHAR(255)  Alert email recipient
  - mail_template          VARCHAR(64)   Email template used
  - mail_status            VARCHAR(16)   Email send status
  - llm_summary            TEXT          AI-generated analysis summary
  - error                  TEXT          Error message if analysis failed
"""


def get_schema_for_prompt() -> str:
    """Return the schema description ready to inject into the LLM system prompt."""
    return SCHEMA_DESCRIPTION


def get_schema_dict() -> dict:
    """Return schema as a structured dict for the /schema endpoint."""
    return {
        "database": "banking_data",
        "allowed_tables": sorted(ALLOWED_TABLES),
        "tables": {
            "transactions": {
                "description": "Bank transactions with fraud scoring",
                "columns": [
                    {"name": "id", "type": "SERIAL", "description": "Primary key"},
                    {"name": "transaction_id", "type": "VARCHAR", "description": "Unique transaction identifier"},
                    {"name": "user_id", "type": "VARCHAR", "description": "User/client identifier"},
                    {"name": "amount", "type": "DECIMAL", "description": "Transaction amount in EUR"},
                    {"name": "currency", "type": "VARCHAR", "description": "Currency code (default EUR)"},
                    {"name": "transaction_type", "type": "VARCHAR", "description": "Type: transfer, payment, withdrawal, deposit"},
                    {"name": "status", "type": "VARCHAR", "description": "Status: pending, completed, failed, blocked"},
                    {"name": "fraud_score", "type": "DECIMAL", "description": "ML fraud score 0-1"},
                    {"name": "is_fraudulent", "type": "BOOLEAN", "description": "Whether flagged as fraudulent"},
                    {"name": "merchant_name", "type": "VARCHAR", "description": "Merchant or counterparty name"},
                    {"name": "merchant_category", "type": "VARCHAR", "description": "Merchant category"},
                    {"name": "created_at", "type": "TIMESTAMP", "description": "Transaction date/time"},
                ],
            },
            "fraud_rules": {
                "description": "Fraud detection rules managed by compliance",
                "columns": [
                    {"name": "id", "type": "VARCHAR", "description": "Rule identifier"},
                    {"name": "name", "type": "VARCHAR", "description": "Rule name"},
                    {"name": "domain", "type": "VARCHAR", "description": "VELOCITY | LIMIT | GEOGRAPHIC | AML | BEHAVIORAL"},
                    {"name": "points", "type": "INTEGER", "description": "Risk score points 0-100"},
                    {"name": "severity", "type": "VARCHAR", "description": "LOW | MEDIUM | HIGH | CRITICAL"},
                    {"name": "active", "type": "BOOLEAN", "description": "Whether rule is active"},
                ],
            },
            "fraud_decision_logs": {
                "description": "Historical fraud analysis decisions per IBAN",
                "columns": [
                    {"name": "id", "type": "VARCHAR", "description": "Decision log identifier"},
                    {"name": "created_at", "type": "TIMESTAMPTZ", "description": "Analysis date"},
                    {"name": "iban", "type": "VARCHAR", "description": "Analyzed IBAN"},
                    {"name": "score_final", "type": "INTEGER", "description": "Final risk score 0-100"},
                    {"name": "risk_level", "type": "VARCHAR", "description": "APPROVED | REVIEW | HOLD | BLOCK"},
                    {"name": "tracfin_required", "type": "BOOLEAN", "description": "TRACFIN reporting required"},
                    {"name": "mail_sent", "type": "BOOLEAN", "description": "Alert email sent"},
                ],
            },
        },
    }
