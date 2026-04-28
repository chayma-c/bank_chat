-- ══════════════════════════════════════════════════════════════════════════
-- init-db.sql — PostgreSQL Database Initialization Script
-- ══════════════════════════════════════════════════════════════════════════
-- Purpose: Create separate databases with dedicated users for each service
-- Location: ./postgres/init-db.sql
-- Execution: Automatically run on first PostgreSQL container startup
-- ══════════════════════════════════════════════════════════════════════════

\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo '🚀 Starting database initialization...'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

-- ══════════════════════════════════════════════════════════════════════════
-- DATABASE 1: bank_orchestrateur
-- Service: Django Orchestrator (main backend)
-- Purpose: User conversations, message history, agent orchestration
-- ══════════════════════════════════════════════════════════════════════════

\echo ''
\echo '📊 Creating database: bank_orchestrateur'
\echo '   Service: Django Orchestrator'
\echo '   User: orchestrateur_user'

-- Switch to postgres database for creation
\connect postgres

-- Create database
CREATE DATABASE bank_orchestrateur
    WITH 
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE = 'en_US.utf8'
    TEMPLATE = template0;

-- Create dedicated user
CREATE USER orchestrateur_user WITH PASSWORD 'orchestrateur_password';

-- Grant database ownership
ALTER DATABASE bank_orchestrateur OWNER TO orchestrateur_user;
GRANT ALL PRIVILEGES ON DATABASE bank_orchestrateur TO orchestrateur_user;

-- Connect to the new database to set schema permissions
\connect bank_orchestrateur

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO orchestrateur_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO orchestrateur_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO orchestrateur_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO orchestrateur_user;

-- Set default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO orchestrateur_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO orchestrateur_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO orchestrateur_user;

\echo '✅ bank_orchestrateur created successfully'

-- ══════════════════════════════════════════════════════════════════════════
-- DATABASE 2: keycloak_db
-- Service: Keycloak Authentication Server
-- Purpose: User authentication, SSO, OAuth2/OIDC, realm management
-- ══════════════════════════════════════════════════════════════════════════

\echo ''
\echo '🔐 Creating database: keycloak_db'
\echo '   Service: Keycloak Authentication'
\echo '   User: keycloak_user'

-- Switch back to postgres
\connect postgres

-- Create database
CREATE DATABASE keycloak_db
    WITH 
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE = 'en_US.utf8'
    TEMPLATE = template0;

-- Create dedicated user
CREATE USER keycloak_user WITH PASSWORD 'keycloak_password';

-- Grant database ownership
ALTER DATABASE keycloak_db OWNER TO keycloak_user;
GRANT ALL PRIVILEGES ON DATABASE keycloak_db TO keycloak_user;

-- Connect to the new database
\connect keycloak_db

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO keycloak_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO keycloak_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO keycloak_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO keycloak_user;

-- Set default privileges
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO keycloak_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO keycloak_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO keycloak_user;

\echo '✅ keycloak_db created successfully'

-- ══════════════════════════════════════════════════════════════════════════
-- DATABASE 3: banking_data
-- Service: Fraud Detection Service + Future Text-to-SQL Agent
-- Purpose: Banking data analysis, fraud detection, SQL query execution
-- ══════════════════════════════════════════════════════════════════════════

\echo ''
\echo '🏦 Creating database: banking_data'
\echo '   Service: Fraud Detection + Text-to-SQL'
\echo '   User: sql_user'

-- Switch back to postgres
\connect postgres

-- Create database
CREATE DATABASE banking_data
    WITH 
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE = 'en_US.utf8'
    TEMPLATE = template0;

-- Create dedicated user
CREATE USER sql_user WITH PASSWORD 'sql_password';

-- Grant database ownership
ALTER DATABASE banking_data OWNER TO sql_user;
GRANT ALL PRIVILEGES ON DATABASE banking_data TO sql_user;

-- Connect to the new database
\connect banking_data

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO sql_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO sql_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO sql_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO sql_user;

-- Set default privileges
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO sql_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO sql_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO sql_user;

-- Create sample schema for banking data (optional)
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    transaction_id VARCHAR(255) UNIQUE NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    amount DECIMAL(15, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'EUR',
    transaction_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    fraud_score DECIMAL(5, 4),
    is_fraudulent BOOLEAN DEFAULT FALSE,
    merchant_name VARCHAR(255),
    merchant_category VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_transactions_user_id ON transactions(user_id);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
CREATE INDEX idx_transactions_fraud_score ON transactions(fraud_score);
-- ── Fraud detection rules (admin CRUD) ────────────────────────────────
CREATE TABLE IF NOT EXISTS fraud_rules (
    id             VARCHAR(64)   PRIMARY KEY,
    name           VARCHAR(255)  NOT NULL,
    domain         VARCHAR(64)   NOT NULL CHECK (domain IN ('VELOCITY','LIMIT','GEOGRAPHIC','AML','BEHAVIORAL')),
    trigger        VARCHAR(512)  NOT NULL,
    trigger_detail VARCHAR(512)  DEFAULT '',
    points         INTEGER       NOT NULL DEFAULT 10 CHECK (points BETWEEN 0 AND 100),
    severity       VARCHAR(32)   NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    active         BOOLEAN       NOT NULL DEFAULT TRUE,
    description    TEXT          DEFAULT '',
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
-- Ajouter dans la section banking_data, après la table fraud_rules

CREATE TABLE IF NOT EXISTS fraud_decision_logs (
    id                     VARCHAR(64)   PRIMARY KEY,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    user_id                VARCHAR(128),
    session_id             VARCHAR(128),
    iban                   VARCHAR(64)   NOT NULL,
    transactions_count     INTEGER       DEFAULT 0,
    date_range             VARCHAR(64),
    score_behavioral       INTEGER       DEFAULT 0,
    score_aml              INTEGER       DEFAULT 0,
    score_final            INTEGER       DEFAULT 0,
    risk_level             VARCHAR(32),
    tracfin_required       BOOLEAN       NOT NULL DEFAULT FALSE,
    rules_triggered        INTEGER       DEFAULT 0,
    rules_evaluated        INTEGER       DEFAULT 0,
    triggered_rules_detail JSONB,
    report_path            VARCHAR(512),
    download_url           VARCHAR(512),
    mail_sent              BOOLEAN       NOT NULL DEFAULT FALSE,
    mail_recipient         VARCHAR(255),
    mail_template          VARCHAR(64),
    mail_status            VARCHAR(16),
    mail_id                VARCHAR(64),
    llm_summary            TEXT,
    error                  TEXT
);

CREATE INDEX idx_decision_logs_created_at ON fraud_decision_logs(created_at DESC);
CREATE INDEX idx_decision_logs_iban       ON fraud_decision_logs(iban);
CREATE INDEX idx_decision_logs_risk_level ON fraud_decision_logs(risk_level);
CREATE INDEX idx_decision_logs_user_id    ON fraud_decision_logs(user_id);

\echo '✅ fraud_decision_logs table created'

-- Auto-update updated_at on every UPDATE
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER fraud_rules_updated_at
    BEFORE UPDATE ON fraud_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX idx_fraud_rules_domain ON fraud_rules(domain);
CREATE INDEX idx_fraud_rules_active ON fraud_rules(active);

\echo '✅ banking_data created successfully'


-- ══════════════════════════════════════════════════════════════════════════
-- VERIFICATION
-- Display all created databases and their owners
-- ══════════════════════════════════════════════════════════════════════════

\echo ''
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo '📋 Database verification:'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

\connect postgres

SELECT 
    datname AS "Database",
    pg_catalog.pg_get_userbyid(datdba) AS "Owner",
    pg_encoding_to_char(encoding) AS "Encoding",
    datcollate AS "Collation"
FROM pg_database
WHERE datname IN ('bank_orchestrateur', 'keycloak_db', 'banking_data')
ORDER BY datname;


-- ══════════════════════════════════════════════════════════════════════════
-- DATABASE 4: mail_db
-- Service: Mail Service
-- Purpose: Traçabilité de tous les emails envoyés
-- ══════════════════════════════════════════════════════════════════════════

\connect postgres

CREATE DATABASE mail_db
    WITH
    ENCODING = 'UTF8'
    LC_COLLATE = 'C'
    LC_CTYPE = 'C'
    TEMPLATE = template0;

CREATE USER mail_user WITH PASSWORD 'mail_password';
ALTER DATABASE mail_db OWNER TO mail_user;
GRANT ALL PRIVILEGES ON DATABASE mail_db TO mail_user;

\connect mail_db

GRANT ALL ON SCHEMA public TO mail_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO mail_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO mail_user;

CREATE TABLE IF NOT EXISTS sent_emails (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    sent_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    recipient      VARCHAR(255)  NOT NULL,
    cc             VARCHAR(255),
    subject        VARCHAR(512)  NOT NULL,
    template_type  VARCHAR(64)   NOT NULL,   -- fraud_alert | critical_alert | client_response | nightly_report
    iban           VARCHAR(64),              -- IBAN concerné (null si non applicable)
    score_final    INTEGER,                  -- score fraude (null si non applicable)
    risk_level     VARCHAR(32),              -- APPROVED | REVIEW | HOLD | BLOCK
    tracfin        BOOLEAN       DEFAULT FALSE,
    has_attachment BOOLEAN       DEFAULT FALSE,
    status         VARCHAR(16)   NOT NULL DEFAULT 'sent',  -- sent | failed
    error_detail   TEXT,                     -- message d'erreur si status=failed
    session_id     VARCHAR(128),             -- session de conversation source
    user_id        VARCHAR(128)              -- utilisateur qui a déclenché
);

CREATE INDEX idx_sent_emails_sent_at       ON sent_emails(sent_at DESC);
CREATE INDEX idx_sent_emails_template_type ON sent_emails(template_type);
CREATE INDEX idx_sent_emails_iban          ON sent_emails(iban);
CREATE INDEX idx_sent_emails_status        ON sent_emails(status);

\echo '✅ mail_db created successfully'

\echo ''
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo '✅ Database initialization completed successfully!'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo ''