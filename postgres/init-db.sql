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

\echo ''
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo '✅ Database initialization completed successfully!'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo ''