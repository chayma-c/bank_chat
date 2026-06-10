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

    -- ══════════════════════════════════════════════════════════════════════════
    -- TEXT-TO-SQL USER
    -- ══════════════════════════════════════════════════════════════════════════
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

    -- ══════════════════════════════════════════════════════════════════════════
    -- FRAUD USER
    -- ══════════════════════════════════════════════════════════════════════════
    CREATE USER fraud_user WITH PASSWORD 'fraud_password';
    GRANT CONNECT ON DATABASE banking_data TO fraud_user;

    \connect banking_data

    GRANT USAGE, CREATE ON SCHEMA public TO fraud_user;
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO fraud_user;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO fraud_user;
    GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO fraud_user;
    
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO fraud_user;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON SEQUENCES TO fraud_user;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON FUNCTIONS TO fraud_user;

    -- ══════════════════════════════════════════════════════════════════════════
    -- TABLES & VIEWS
    -- ══════════════════════════════════════════════════════════════════════════

    -- ══════════════════════════════════════════════════════════════════════════
    -- TABLE: transactions
    -- Unified schema — legacy seed columns + CSV-aligned fraud-engine columns.
    -- Legacy columns are kept for backward compatibility with the text-to-sql
    -- agent and existing seed data. CSV-aligned columns are what the fraud
    -- detection engine (loader.py) actually reads.
    -- ══════════════════════════════════════════════════════════════════════════
    CREATE TABLE IF NOT EXISTS transactions (
        -- ── Primary key ─────────────────────────────────────────────────────
        id                      SERIAL PRIMARY KEY,

        -- ── Legacy columns (kept for backward compat & seed data) ────────────
        transaction_id          VARCHAR(255) UNIQUE,
        user_id                 VARCHAR(255),
        amount                  DECIMAL(15, 2),
        currency                VARCHAR(3)    DEFAULT 'EUR',
        status                  VARCHAR(20)   DEFAULT 'pending',
        fraud_score             DECIMAL(5, 4),
        is_fraudulent           BOOLEAN       DEFAULT FALSE,
        merchant_name           VARCHAR(255),
        merchant_category       VARCHAR(100),
        created_at              TIMESTAMPTZ   DEFAULT NOW(),
        updated_at              TIMESTAMPTZ   DEFAULT NOW(),

        -- ── CSV-aligned columns (fraud detection engine) ──────────────────────
        -- Matches: Transaction_Amount, Timestamp, Geo_Location, IP_Address,
        --          Merchant_MCC, Account_CurrentBalance, Client_IBAN,
        --          Counterparty_IBAN, Transaction_Type
        transaction_amount      DECIMAL(15, 2),
        timestamp               TIMESTAMPTZ,
        geo_location            TEXT,
        ip_address              VARCHAR(45),
        merchant_mcc            INTEGER,
        account_current_balance DECIMAL(15, 2),
        client_iban             VARCHAR(64),
        counterparty_iban       VARCHAR(64),
        transaction_type        VARCHAR(50),

        -- ── Ingestion tracking ───────────────────────────────────────────────
        -- Set on INSERT — used by the incremental loader to find new rows.
        ingested_at             TIMESTAMPTZ   DEFAULT NOW()
    );

    -- ── Legacy indexes (preserved) ────────────────────────────────────────────
    CREATE INDEX idx_transactions_user_id           ON transactions(user_id);
    CREATE INDEX idx_transactions_created_at        ON transactions(created_at);
    CREATE INDEX idx_transactions_fraud_score       ON transactions(fraud_score);
    CREATE INDEX idx_transactions_is_fraudulent     ON transactions(is_fraudulent);
    CREATE INDEX idx_transactions_status            ON transactions(status);

    -- ── New indexes (CSV-aligned columns) ────────────────────────────────────
    CREATE INDEX idx_transactions_client_iban       ON transactions(client_iban);
    CREATE INDEX idx_transactions_counterparty_iban ON transactions(counterparty_iban);
    CREATE INDEX idx_transactions_timestamp         ON transactions(timestamp DESC);
    CREATE INDEX idx_transactions_ingested_at       ON transactions(ingested_at DESC);

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
        updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
        -- audit trail (P0 security fix)
        created_by     VARCHAR(128),
        updated_by     VARCHAR(128),
        deleted_at     TIMESTAMPTZ,
        deleted_by     VARCHAR(128)
    );

    -- Migration pour bases existantes : ajout des colonnes d'audit si absentes
    ALTER TABLE fraud_rules ADD COLUMN IF NOT EXISTS created_by  VARCHAR(128);
    ALTER TABLE fraud_rules ADD COLUMN IF NOT EXISTS updated_by  VARCHAR(128);
    ALTER TABLE fraud_rules ADD COLUMN IF NOT EXISTS deleted_at  TIMESTAMPTZ;
    ALTER TABLE fraud_rules ADD COLUMN IF NOT EXISTS deleted_by  VARCHAR(128);

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

    -- ══════════════════════════════════════════════════════════════════════════
    -- 🌱 SEED: fraud_rules — 13 règles EUR (normes 5AMLD / PSD2 / FATF 2023)
    -- ══════════════════════════════════════════════════════════════════════════
    -- Référence réglementaire :
    --   R1  HIGH_AMOUNT           > 5 000 €   PSD2 SCA Art.18 (was 3 000 TND)
    --   R2  SUSPICIOUS_IBAN       regex + pays OFAC (RU/IR/KP/SY/VE)
    --   R3  STRUCTURING           > 20 dépôts < 10 000 € / 7 jours  5AMLD Art.11 (was 3 txns/24h)
    --   R4  NIGHT_TRANSACTION     00:00–05:00  (inchangé)
    --   R5  FOREIGN_IP            géofencing > 1 000 km  (was IP fixe 185.230.x.x)
    --   R6  HIGH_RISK_MCC         MCC 5541/5999/5311 + > 1 500 €  (inchangé)
    --   R7  BALANCE_DRAIN         > 80 % du solde  (inchangé)
    --   R8  REPEATED_ALERTS       ≥ 2 alertes / 7 jours  FATF Rec.20 (was 3+)
    --   R9  VELOCITY_HIGH         carte > 5/1h · virement > 10/10 min  5AMLD (NOUVEAU)
    --   R10 NEAR_THRESHOLD        9 500–9 999 €  AML  (was montant rond > 500 €, NOUVEAU)
    --   R11 CROSS_BORDER          pays différents / 48h  (NOUVEAU)
    --   R12 NEW_BENEFICIARY_HIGH  nouveau bénéf. + transfert immédiat > 3 000 €  (NOUVEAU)
    --   R13 DORMANT_ACCOUNT       inactif > 90 jours  (NOUVEAU)
    -- ══════════════════════════════════════════════════════════════════════════

    \echo '🌱 Seeding fraud_rules (13 rules, EUR thresholds)...'

    INSERT INTO fraud_rules (id, name, domain, trigger, trigger_detail, points, severity, active, description, created_at, updated_at) VALUES

    -- R1 — HIGH_AMOUNT : > 5 000 EUR (PSD2 high-value threshold)
    ('RL-HA-001',
     'High amount transaction',
     'LIMIT',
     'Amount > 5000 EUR',
     'PSD2 high-value threshold — 3DS + geoloc required above 5 000 €',
     30, 'HIGH', TRUE,
     'Flags any single transaction exceeding 5 000 €. Aligned with PSD2 SCA Art.18 and card-network high-value rules (Visa/Mastercard: >5 000 € → mandatory 3DS + geolocation check). Previous threshold of 3 000 TND generated excessive false positives on routine business payments.',
     NOW(), NOW()),

    -- R2 — SUSPICIOUS_IBAN : regex + OFAC country prefixes
    ('RL-SI-002',
     'Suspicious IBAN check',
     'GEOGRAPHIC',
     'Client/counterparty IBAN in blacklist or OFAC country',
     'OFAC sanctioned countries: RU, IR, KP, SY, VE — auto-BLOCK',
     20, 'HIGH', TRUE,
     'Checks client and counterparty IBANs against the known suspicious IBAN blacklist AND against OFAC-sanctioned country prefixes (RU=Russia, IR=Iran, KP=North Korea, SY=Syria, VE=Venezuela). Any match with an OFAC country triggers automatic BLOCK. Self-transfer detection also covered.',
     NOW(), NOW()),

    -- R3 — STRUCTURING : > 20 deposits < 10 000 EUR / 7 days (5AMLD Art.11)
    ('RL-ST-003',
     'Structuring / smurfing (AML)',
     'AML',
     'More than 20 deposits under 10000 EUR within 7 days',
     '5AMLD Art.11: smurfing window = 7 days, reporting threshold = 10 000 €. Suspect amounts: 9 990, 9 950, 4 990 €.',
     35, 'CRITICAL', TRUE,
     'Classic AML structuring (smurfing): multiple deposits just below the 10 000 € mandatory reporting threshold (5AMLD Art.11), spread over a 7-day sliding window to avoid detection. TRACFIN declaration mandatory if triggered. Replaces the previous single-day 850–950 TND band which matched no legal standard.',
     NOW(), NOW()),

    -- R4 — NIGHT_TRANSACTION : 00:00–05:00 (confirmed, unchanged)
    ('RL-NT-004',
     'Night transfer alert',
     'BEHAVIORAL',
     'P2P / INTL transfer between 00:00–05:00',
     'Unusual hour for high-value transfers — banking scoring matrix +20 pts',
     10, 'MEDIUM', TRUE,
     'Flags P2P and international wire transfers executed during the 00:00–05:00 window. Confirmed as a standard behavioural signal in bank scoring matrices (night-hour = +20 pts base). No threshold change required.',
     NOW(), NOW()),

    -- R5 — FOREIGN_IP / GEOFENCING : country-level (was IP prefix 185.230.x.x)
    ('RL-FI-005',
     'Foreign IP / geofencing alert',
     'GEOGRAPHIC',
     'IP country differs from client home country OR distance > 1000 km',
     'Secondary check: amount > 3000 EUR or customer risk score >= 70. OFAC countries → BLOCK.',
     20, 'HIGH', TRUE,
     'Replaces the single hard-coded IP prefix 185.230.x.x with country-level geofencing: any transaction originating from an IP whose country differs from the client registered home country, or where estimated distance from the last known location exceeds 1 000 km, is flagged. Matches Visa/Mastercard geo-blocking standard. Secondary threshold raised to 3 000 EUR (was 2 000 TND).',
     NOW(), NOW()),

    -- R6 — HIGH_RISK_MCC : MCC 5541/5999/5311 + > 1 500 EUR (confirmed, TND→EUR label)
    ('RL-MCC-006',
     'High-risk merchant (MCC)',
     'BEHAVIORAL',
     'MCC 5541/5999/5311 and amount > 1500 EUR',
     'No recent pattern for this MCC on account history',
     10, 'MEDIUM', TRUE,
     'Flags high-value purchases at merchant category codes statistically linked to fraud: 5541 (service stations/gas), 5999 (misc. retail), 5311 (department stores). Threshold of 1 500 EUR confirmed appropriate for the international context.',
     NOW(), NOW()),

    -- R7 — BALANCE_DRAIN : > 80% of balance (confirmed, unchanged)
    ('RL-BD-007',
     'Balance drain pattern',
     'BEHAVIORAL',
     'Amount > 80% of account current balance',
     'Moving most of balance in one transaction',
     10, 'HIGH', TRUE,
     'Detects transactions that drain more than 80% of the account balance in a single operation. Standard behavioural signal across banking fraud systems. No threshold change required.',
     NOW(), NOW()),

    -- R8 — REPEATED_ALERTS : >= 2 alerts / 7 days (was 3+) — FATF Rec. 20
    ('RL-RA-008',
     'Repeated fraud alerts',
     'BEHAVIORAL',
     '2 or more ALERTED transactions in last 7 days',
     'Same client IBAN — persistent risk profile confirmed from second alert',
     25, 'HIGH', FALSE,
     'Detects ongoing risk profiles from repeated alert status on the same IBAN. Threshold lowered from 3 to 2 alerts: a second flag within 7 days is sufficient to confirm a persistent pattern per FATF Recommendation 20 (ongoing monitoring). Score raised to 25. Disabled by default until alert_count_7d column is populated.',
     NOW(), NOW()),

    -- R9 — VELOCITY_HIGH : card > 5/1h OR wire > 10/10min (NEW — 5AMLD velocity)
    ('RL-VH-009',
     'High velocity transactions',
     'VELOCITY',
     'More than 5 card transactions per 1 hour OR more than 10 wire transfers per 10 minutes',
     'Card >5/1h → Challenge SMS (5AMLD) | Wire >10/10min → auto-BLOCK',
     20, 'HIGH', TRUE,
     'Two velocity sub-rules in one: (1) Card: >5 transactions in 1 hour → Challenge SMS (5AMLD velocity standard). (2) Wire/SEPA: >10 transfers in 10 minutes → automatic BLOCK. Previous single threshold of >10/1h was double the card standard and missed fast-paced wire fraud.',
     NOW(), NOW()),

    -- R10 — NEAR_THRESHOLD : 9 500–9 999 EUR AML band (NEW — replaces round > 500 EUR)
    ('RL-NTA-010',
     'Suspicious near-threshold amount (AML)',
     'AML',
     'Amount between 9500 EUR and 9999 EUR',
     'Classic structuring: just below 10 000 € TRACFIN reporting threshold. Also flags 4 500–4 999 € and 14 500–14 999 €.',
     15, 'HIGH', TRUE,
     'Detects amounts deliberately kept just below the mandatory cash reporting threshold (10 000 €) as defined by 5AMLD and FATF. Danger bands: 9 500–9 999 € (primary), 4 500–4 999 € and 14 500–14 999 € (secondary, for split-transaction structuring). Replaces the previous >500 € round amount rule which generated massive false positives (every salary, rent, or round-number business payment).',
     NOW(), NOW()),

    -- R11 — CROSS_BORDER : different countries / 48h (NEW — layering detection)
    ('RL-CB-011',
     'Cross-border transaction',
     'GEOGRAPHIC',
     'Transactions in different countries within 48 hours',
     'Layering detection: A→B→C chain under 48h. OFAC countries → BLOCK.',
     12, 'MEDIUM', TRUE,
     'Flags accounts with transactions in different countries within a 48-hour window. Aligned with AML layering detection (A→B→C cascade < 48h) and OFAC geofencing. No threshold change required.',
     NOW(), NOW()),

    -- R12 — NEW_BENEFICIARY_HIGH : new benef + immediate transfer > 3 000 EUR (NEW)
    ('RL-NB-012',
     'New beneficiary high-value transfer',
     'BEHAVIORAL',
     'New beneficiary added AND immediate transfer > 3000 EUR in same session',
     'Beneficiary velocity: add + transfer in same session is the critical signal, not amount alone',
     20, 'HIGH', TRUE,
     'Detects the critical fraud pattern: a new beneficiary is added and a high-value transfer to that beneficiary is executed in the same session. Threshold raised from 2 000 € to 3 000 € to reduce false positives on routine payroll or rent payments. The immediate transfer condition (same session) is mandatory — the velocity component is the true fraud signal.',
     NOW(), NOW()),

    -- R13 — DORMANT_ACCOUNT : inactive > 90 days (NEW — money-mule reactivation)
    ('RL-DA-013',
     'Dormant account reactivation',
     'BEHAVIORAL',
     'Account inactive for more than 90 days with sudden activity',
     'Standard money-mule reactivation indicator',
     18, 'HIGH', TRUE,
     'Flags accounts that have been inactive for more than 90 days and suddenly show significant transaction activity. Standard money-mule reactivation indicator per FATF typologies. No threshold change required.',
     NOW(), NOW());

    \echo '✅ fraud_rules seeded: 13 rules (EUR thresholds, 5AMLD/PSD2/FATF 2023)'

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
    -- TABLE: account_risk_profile
    -- Rolling-memory table — one row per client IBAN.
    -- Updated after every analysis run. Stores the last known risk state and
    -- rolling aggregates so the engine only needs to analyze NEW transactions
    -- within the configured window (rolling_window_days) each run.
    -- ══════════════════════════════════════════════════════════════════════════
    CREATE TABLE IF NOT EXISTS account_risk_profile (
        id                      SERIAL PRIMARY KEY,

        -- ── Identity ─────────────────────────────────────────────────────────
        client_iban             VARCHAR(64)   UNIQUE NOT NULL,

        -- ── Last analysis snapshot ───────────────────────────────────────────
        last_analyzed_at        TIMESTAMPTZ,                    -- when the last run occurred
        last_score_final        INTEGER       DEFAULT 0,        -- fraud score from last run
        last_risk_level         VARCHAR(32)   DEFAULT 'APPROVED'
                                    CHECK (last_risk_level IN ('APPROVED','REVIEW','HOLD','BLOCK')),
        last_tracfin            BOOLEAN       DEFAULT FALSE,    -- TRACFIN flag from last run

        -- ── Rolling window aggregates ─────────────────────────────────────────
        -- Recomputed each analysis run over the last `rolling_window_days` days.
        rolling_tx_count        INTEGER       DEFAULT 0,        -- # of transactions in window
        rolling_total_amount    DECIMAL(15,2) DEFAULT 0,        -- sum of amounts in window
        rolling_avg_amount      DECIMAL(15,2) DEFAULT 0,        -- average amount in window
        rolling_max_amount      DECIMAL(15,2) DEFAULT 0,        -- largest single tx in window
        rolling_window_days     INTEGER       DEFAULT 7,        -- window size used in last run

        -- ── Historical alert tracking (implements Rule 8) ─────────────────────
        alert_count_7d          INTEGER       DEFAULT 0,        -- analyses that triggered alerts in last 7d

        -- ── Timestamps ───────────────────────────────────────────────────────
        created_at              TIMESTAMPTZ   DEFAULT NOW(),
        updated_at              TIMESTAMPTZ   DEFAULT NOW()
    );

    -- Auto-update updated_at
    CREATE TRIGGER account_risk_profile_updated_at
        BEFORE UPDATE ON account_risk_profile
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();

    CREATE INDEX idx_arp_client_iban        ON account_risk_profile(client_iban);
    CREATE INDEX idx_arp_last_analyzed_at   ON account_risk_profile(last_analyzed_at DESC);
    CREATE INDEX idx_arp_last_risk_level    ON account_risk_profile(last_risk_level);
    CREATE INDEX idx_arp_last_score_final   ON account_risk_profile(last_score_final DESC);

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