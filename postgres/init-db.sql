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

    -- Create dedicated user (READ-ONLY — used by text-to-sql-service)
    CREATE USER sql_user WITH PASSWORD 'sql_password';

    -- ⚠️ Do NOT give ownership — sql_user must be read-only
    -- Give connect rights only
    GRANT CONNECT ON DATABASE banking_data TO sql_user;

    -- Connect to the new database
    \connect banking_data

    -- Read-only: USAGE on schema + SELECT on all tables only
    GRANT USAGE ON SCHEMA public TO sql_user;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_user;
    GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO sql_user;

    -- Ensure future tables are also SELECT-only for sql_user
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sql_user;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO sql_user;

    -- Create sample schema for banking data
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

    CREATE INDEX idx_transactions_user_id       ON transactions(user_id);
    CREATE INDEX idx_transactions_created_at    ON transactions(created_at);
    CREATE INDEX idx_transactions_fraud_score   ON transactions(fraud_score);
    CREATE INDEX idx_transactions_is_fraudulent ON transactions(is_fraudulent);
    CREATE INDEX idx_transactions_status        ON transactions(status);

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

    -- ── Fraud decision logs (written by fraud-service, read by text-to-sql) ──
    CREATE TABLE IF NOT EXISTS fraud_decision_logs (
        id                     VARCHAR(64)   PRIMARY KEY,
        created_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
        user_id                VARCHAR(128),
        session_id             VARCHAR(128),
        iban                   VARCHAR(64),
        transactions_count     INTEGER       DEFAULT 0,
        date_range             VARCHAR(64),
        score_behavioral       INTEGER       DEFAULT 0,
        score_aml              INTEGER       DEFAULT 0,
        score_final            INTEGER       DEFAULT 0,
        risk_level             VARCHAR(32)   DEFAULT 'APPROVED'
                                CHECK (risk_level IN ('APPROVED','REVIEW','HOLD','BLOCK')),
        tracfin_required       BOOLEAN       DEFAULT FALSE,
        rules_triggered        INTEGER       DEFAULT 0,
        rules_evaluated        INTEGER       DEFAULT 0,
        triggered_rules_detail JSONB         DEFAULT '[]',
        report_path            VARCHAR(512),
        download_url           VARCHAR(512),
        mail_sent              BOOLEAN       DEFAULT FALSE,
        mail_recipient         VARCHAR(255),
        mail_template          VARCHAR(64),
        mail_status            VARCHAR(16),
        mail_id                VARCHAR(128),
        llm_summary            TEXT,
        error                  TEXT
    );

    CREATE INDEX idx_fdl_created_at      ON fraud_decision_logs(created_at DESC);
    CREATE INDEX idx_fdl_iban            ON fraud_decision_logs(iban);
    CREATE INDEX idx_fdl_risk_level      ON fraud_decision_logs(risk_level);
    CREATE INDEX idx_fdl_tracfin         ON fraud_decision_logs(tracfin_required);
    CREATE INDEX idx_fdl_mail_sent       ON fraud_decision_logs(mail_sent);

    -- ══════════════════════════════════════════════════════════════════════════
    -- 🌱 SEED DATA — 60 realistic banking transactions for sandbox testing
    -- ══════════════════════════════════════════════════════════════════════════

    \echo '🌱 Seeding banking_data with sample transactions...'

    INSERT INTO transactions
        (transaction_id, user_id, amount, currency, transaction_type,
        status, fraud_score, is_fraudulent, merchant_name, merchant_category, created_at)
    VALUES
    -- ── Normal transactions (low fraud score) ─────────────────────────────
    ('TXN-001','USER_A', 52.30, 'EUR','payment',    'completed', 0.0420, false, 'CARREFOUR',       'grocery',      NOW() - INTERVAL '30 days'),
    ('TXN-002','USER_A', 120.00,'EUR','payment',    'completed', 0.0310, false, 'FNAC',            'electronics',  NOW() - INTERVAL '29 days'),
    ('TXN-003','USER_A', 15.80, 'EUR','payment',    'completed', 0.0150, false, 'UBER',            'transport',    NOW() - INTERVAL '28 days'),
    ('TXN-004','USER_B', 300.00,'EUR','transfer',   'completed', 0.0500, false, 'BANK_TRANSFER',   'transfer',     NOW() - INTERVAL '28 days'),
    ('TXN-005','USER_B', 1200.00,'EUR','transfer',  'completed', 0.0620, false, 'SOCIETE_GENERALE','transfer',     NOW() - INTERVAL '27 days'),
    ('TXN-006','USER_C', 45.00, 'EUR','payment',    'completed', 0.0210, false, 'SEPHORA',         'beauty',       NOW() - INTERVAL '27 days'),
    ('TXN-007','USER_C', 89.99, 'EUR','payment',    'completed', 0.0340, false, 'AMAZON',          'ecommerce',    NOW() - INTERVAL '26 days'),
    ('TXN-008','USER_D', 500.00,'EUR','withdrawal', 'completed', 0.0450, false, 'ATM_PARIS_01',    'cash',         NOW() - INTERVAL '26 days'),
    ('TXN-009','USER_D', 75.00, 'EUR','payment',    'completed', 0.0280, false, 'MONOPRIX',        'grocery',      NOW() - INTERVAL '25 days'),
    ('TXN-010','USER_E', 230.00,'EUR','payment',    'completed', 0.0390, false, 'ZARA',            'clothing',     NOW() - INTERVAL '25 days'),
    -- ── Medium-risk transactions ───────────────────────────────────────────
    ('TXN-011','USER_A', 2500.00,'EUR','transfer',  'completed', 0.3800, false, 'WISE_TRANSFER',   'fintech',      NOW() - INTERVAL '24 days'),
    ('TXN-012','USER_B', 800.00, 'EUR','withdrawal','completed', 0.4200, false, 'ATM_GARE_NORD',   'cash',         NOW() - INTERVAL '24 days'),
    ('TXN-013','USER_C', 999.00, 'EUR','payment',   'completed', 0.4500, false, 'STEAM',           'gaming',       NOW() - INTERVAL '23 days'),
    ('TXN-014','USER_F', 3200.00,'EUR','transfer',  'pending',   0.4900, false, 'REVOLUT',         'fintech',      NOW() - INTERVAL '23 days'),
    ('TXN-015','USER_F', 1800.00,'EUR','payment',   'completed', 0.5100, false, 'BINANCE',         'crypto',       NOW() - INTERVAL '22 days'),
    ('TXN-016','USER_G', 450.00, 'EUR','payment',   'completed', 0.3600, false, 'AIRBNB',          'travel',       NOW() - INTERVAL '22 days'),
    ('TXN-017','USER_G', 5000.00,'EUR','transfer',  'completed', 0.5500, false, 'WESTERN_UNION',   'transfer',     NOW() - INTERVAL '21 days'),
    ('TXN-018','USER_H', 670.00, 'EUR','payment',   'completed', 0.4800, false, 'CASINO_ENGHIEN',  'gambling',     NOW() - INTERVAL '21 days'),
    ('TXN-019','USER_H', 1100.00,'EUR','transfer',  'completed', 0.5900, false, 'MONERO_EXCHANGE', 'crypto',       NOW() - INTERVAL '20 days'),
    ('TXN-020','USER_I', 380.00, 'EUR','payment',   'failed',    0.4100, false, 'POKER_STARS',     'gambling',     NOW() - INTERVAL '20 days'),
    -- ── High-risk / suspicious transactions ───────────────────────────────
    ('TXN-021','USER_J', 9500.00,'EUR','transfer',  'completed', 0.8100, true,  'SHELL_COMPANY_LTD','offshore',    NOW() - INTERVAL '19 days'),
    ('TXN-022','USER_J', 9400.00,'EUR','transfer',  'completed', 0.8300, true,  'SHELL_COMPANY_LTD','offshore',    NOW() - INTERVAL '19 days'),
    ('TXN-023','USER_J', 9300.00,'EUR','transfer',  'completed', 0.8200, true,  'SHELL_COMPANY_LTD','offshore',    NOW() - INTERVAL '18 days'),
    ('TXN-024','USER_K', 15000.00,'EUR','transfer', 'blocked',   0.9100, true,  'CRYPTO_MIXER',    'crypto',       NOW() - INTERVAL '18 days'),
    ('TXN-025','USER_K', 7800.00,'EUR','withdrawal','blocked',   0.8800, true,  'ATM_ANONYMOUS',   'cash',         NOW() - INTERVAL '17 days'),
    ('TXN-026','USER_L', 22000.00,'EUR','transfer', 'blocked',   0.9500, true,  'DARKWEB_SERVICE', 'other',        NOW() - INTERVAL '17 days'),
    ('TXN-027','USER_L', 4500.00,'EUR','payment',   'blocked',   0.8700, true,  'BITCOIN_ATM',     'crypto',       NOW() - INTERVAL '16 days'),
    ('TXN-028','USER_M', 11000.00,'EUR','transfer', 'completed', 0.7900, true,  'ANON_CORP',       'offshore',     NOW() - INTERVAL '16 days'),
    ('TXN-029','USER_M', 3300.00,'EUR','payment',   'completed', 0.7500, true,  'CASINO_ONLINE',   'gambling',     NOW() - INTERVAL '15 days'),
    ('TXN-030','USER_N', 6700.00,'EUR','transfer',  'completed', 0.8600, true,  'FOREX_ILLICIT',   'finance',      NOW() - INTERVAL '15 days'),
    -- ── Recent transactions (last 2 weeks) ────────────────────────────────
    ('TXN-031','USER_A', 65.00,  'EUR','payment',   'completed', 0.0190, false, 'LIDL',            'grocery',      NOW() - INTERVAL '14 days'),
    ('TXN-032','USER_B', 220.00, 'EUR','payment',   'completed', 0.0350, false, 'DARTY',           'electronics',  NOW() - INTERVAL '13 days'),
    ('TXN-033','USER_C', 18.00,  'EUR','payment',   'completed', 0.0120, false, 'STARBUCKS',       'restaurant',   NOW() - INTERVAL '12 days'),
    ('TXN-034','USER_D', 1500.00,'EUR','transfer',  'completed', 0.0550, false, 'BNP_PARIBAS',     'transfer',     NOW() - INTERVAL '11 days'),
    ('TXN-035','USER_E', 890.00, 'EUR','payment',   'completed', 0.0720, false, 'IKEA',            'furniture',    NOW() - INTERVAL '10 days'),
    ('TXN-036','USER_F', 4900.00,'EUR','transfer',  'pending',   0.6200, true,  'OFFSHORE_INVEST',  'offshore',    NOW() - INTERVAL '10 days'),
    ('TXN-037','USER_G', 350.00, 'EUR','payment',   'completed', 0.3100, false, 'DECATHLON',       'sport',        NOW() - INTERVAL '9 days'),
    ('TXN-038','USER_H', 9900.00,'EUR','transfer',  'completed', 0.7200, true,  'ANON_WALLET',     'crypto',       NOW() - INTERVAL '9 days'),
    ('TXN-039','USER_I', 42.50,  'EUR','payment',   'completed', 0.0250, false, 'McDONALDS',       'restaurant',   NOW() - INTERVAL '8 days'),
    ('TXN-040','USER_J', 8800.00,'EUR','transfer',  'blocked',   0.9200, true,  'SHELL_COMPANY_LTD','offshore',    NOW() - INTERVAL '8 days'),
    -- ── This week ──────────────────────────────────────────────────────────
    ('TXN-041','USER_A', 33.00,  'EUR','payment',   'completed', 0.0110, false, 'PICARD',          'grocery',      NOW() - INTERVAL '6 days'),
    ('TXN-042','USER_B', 750.00, 'EUR','withdrawal','completed', 0.0480, false, 'ATM_OPERA',       'cash',         NOW() - INTERVAL '5 days'),
    ('TXN-043','USER_K', 9800.00,'EUR','transfer',  'blocked',   0.9600, true,  'CRYPTO_MIXER',    'crypto',       NOW() - INTERVAL '5 days'),
    ('TXN-044','USER_C', 199.00, 'EUR','payment',   'completed', 0.0400, false, 'APPLE_STORE',     'electronics',  NOW() - INTERVAL '4 days'),
    ('TXN-045','USER_L', 18500.00,'EUR','transfer', 'blocked',   0.9800, true,  'DARKWEB_SERVICE', 'other',        NOW() - INTERVAL '4 days'),
    ('TXN-046','USER_D', 600.00, 'EUR','transfer',  'completed', 0.0590, false, 'CREDIT_AGRICOLE', 'transfer',     NOW() - INTERVAL '3 days'),
    ('TXN-047','USER_M', 5500.00,'EUR','transfer',  'completed', 0.8100, true,  'ANON_CORP',       'offshore',     NOW() - INTERVAL '3 days'),
    ('TXN-048','USER_E', 28.00,  'EUR','payment',   'completed', 0.0180, false, 'BOULANGER',       'electronics',  NOW() - INTERVAL '2 days'),
    ('TXN-049','USER_N', 3200.00,'EUR','transfer',  'pending',   0.7700, true,  'FOREX_ILLICIT',   'finance',      NOW() - INTERVAL '2 days'),
    ('TXN-050','USER_F', 95.00,  'EUR','payment',   'failed',    0.0650, false, 'BOOKING_COM',     'travel',       NOW() - INTERVAL '2 days'),
    -- ── Today's transactions ───────────────────────────────────────────────
    ('TXN-051','USER_A', 47.20,  'EUR','payment',   'completed', 0.0230, false, 'AUCHAN',          'grocery',      NOW() - INTERVAL '4 hours'),
    ('TXN-052','USER_B', 180.00, 'EUR','payment',   'completed', 0.0410, false, 'ZALANDO',         'clothing',     NOW() - INTERVAL '3 hours'),
    ('TXN-053','USER_K', 9700.00,'EUR','transfer',  'blocked',   0.9400, true,  'CRYPTO_MIXER',    'crypto',       NOW() - INTERVAL '2 hours'),
    ('TXN-054','USER_G', 560.00, 'EUR','payment',   'completed', 0.0380, false, 'LEROY_MERLIN',    'hardware',     NOW() - INTERVAL '90 minutes'),
    ('TXN-055','USER_L', 12000.00,'EUR','transfer', 'blocked',   0.9700, true,  'DARKWEB_SERVICE', 'other',        NOW() - INTERVAL '60 minutes'),
    ('TXN-056','USER_C', 39.90,  'EUR','payment',   'completed', 0.0270, false, 'SPOTIFY',         'subscription', NOW() - INTERVAL '45 minutes'),
    ('TXN-057','USER_H', 8200.00,'EUR','transfer',  'pending',   0.8400, true,  'ANON_WALLET',     'crypto',       NOW() - INTERVAL '30 minutes'),
    ('TXN-058','USER_D', 110.00, 'EUR','payment',   'completed', 0.0330, false, 'OPTICAL_CENTER',  'health',       NOW() - INTERVAL '20 minutes'),
    ('TXN-059','USER_N', 6100.00,'EUR','transfer',  'pending',   0.8900, true,  'FOREX_ILLICIT',   'finance',      NOW() - INTERVAL '10 minutes'),
    ('TXN-060','USER_J', 9100.00,'EUR','transfer',  'blocked',   0.9300, true,  'SHELL_COMPANY_LTD','offshore',    NOW() - INTERVAL '5 minutes');

    -- ── Seed fraud_decision_logs (sample analysis history) ────────────────
    INSERT INTO fraud_decision_logs
        (id, created_at, user_id, session_id, iban, transactions_count, date_range,
        score_behavioral, score_aml, score_final, risk_level, tracfin_required,
        rules_triggered, rules_evaluated, mail_sent, mail_recipient, llm_summary)
    VALUES
    ('LOG-001', NOW()-INTERVAL '10 days', 'agent1', 'sess-aaa', 'TN59001001USER_J',
    12, '2025-04-01/2025-05-01', 72, 68, 81, 'BLOCK', true, 5, 12, true,
    'compliance@bank.com',
    'IBAN TN59001001USER_J présente des virements structurés sous 10k EUR répétés vers une entité offshore non identifiée. TRACFIN requis.'),
    ('LOG-002', NOW()-INTERVAL '7 days',  'agent2', 'sess-bbb', 'TN59001001USER_K',
    8,  '2025-04-15/2025-05-01', 88, 90, 92, 'BLOCK', true, 7, 12, true,
    'compliance@bank.com',
    'Compte USER_K : utilisation intensive d un crypto-mixer. Transferts nocturnes vers wallet anonyme. Risque TRACFIN critique.'),
    ('LOG-003', NOW()-INTERVAL '4 days',  'agent1', 'sess-ccc', 'TN59001001USER_F',
    5,  '2025-04-20/2025-05-01', 45, 38, 52, 'HOLD', false, 3, 12, false,
    NULL,
    'Compte USER_F : activité Binance et Revolut notable. Score intermédiaire. Surveillance recommandée sans blocage immédiat.'),
    ('LOG-004', NOW()-INTERVAL '1 day',   'agent3', 'sess-ddd', 'TN59001001USER_A',
    15, '2025-04-01/2025-05-01', 12, 8,  10, 'APPROVED', false, 0, 12, false,
    NULL,
    'Compte USER_A : comportement normal. Achats courants en France. Aucune anomalie détectée.'),
    ('LOG-005', NOW()-INTERVAL '2 hours', 'agent2', 'sess-eee', 'TN59001001USER_L',
    6,  '2025-05-06/2025-05-13', 91, 95, 97, 'BLOCK', true, 8, 12, true,
    'compliance@bank.com',
    'URGENCE : Compte USER_L réalise des transferts massifs (18k-22k EUR) vers service darkweb. Blocage immédiat et déclaration TRACFIN.');

    \echo '✅ banking_data created and seeded successfully (60 transactions + 5 decision logs)'

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