-- ══════════════════════════════════════════════════════════════════════════
-- seed-data.sql — Mock Data for Banking Database
-- ══════════════════════════════════════════════════════════════════════════

\c bank

-- =========================
-- ACCOUNT TYPES
-- =========================
INSERT INTO account_types (name, description)
VALUES
('savings', 'Personal savings account'),
('checking', 'Daily spending account'),
('business', 'Business operations account'),
('system', 'System internal account');

-- =========================
-- USERS
-- =========================
-- Create a system user for external entities (Company, Merchant, etc.)
INSERT INTO users (email, full_name, status)
VALUES ('system@bank.com', 'System External Entity', 'active');

INSERT INTO users (email, full_name)
VALUES
('alice@example.com', 'Alice Johnson'),
('bob@example.com', 'Bob Smith'),
('charlie@example.com', 'Charlie Brown'),
('diana@example.com', 'Diana Prince');

-- =========================
-- ACCOUNTS
-- =========================

-- System/External Accounts
INSERT INTO accounts (user_id, account_type_id, account_number, balance, status)
SELECT u.id, (SELECT id FROM account_types WHERE name='system'), 'SYS-EXTERNAL-000', 1000000000, 'active'
FROM users u WHERE email='system@bank.com';

-- Alice
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='savings'), 'ALC-SAV-1001', 5000 FROM users u WHERE email='alice@example.com';
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='checking'), 'ALC-CHK-1002', 1200 FROM users u WHERE email='alice@example.com';

-- Bob
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='savings'), 'BOB-SAV-2001', 8000 FROM users u WHERE email='bob@example.com';
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='checking'), 'BOB-CHK-2002', 650 FROM users u WHERE email='bob@example.com';

-- Charlie
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='business'), 'CHR-BUS-3001', 15000 FROM users u WHERE email='charlie@example.com';
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='checking'), 'CHR-CHK-3002', 900 FROM users u WHERE email='charlie@example.com';

-- Diana
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='savings'), 'DIA-SAV-4001', 12000 FROM users u WHERE email='diana@example.com';
INSERT INTO accounts (user_id, account_type_id, account_number, balance)
SELECT u.id, (SELECT id FROM account_types WHERE name='checking'), 'DIA-CHK-4002', 3000 FROM users u WHERE email='diana@example.com';

-- =========================
-- TRANSACTIONS (Realistic Banking Activity)
-- =========================

-- 💰 1. Salary deposit (System → Alice checking)
DO $$
DECLARE
    txn_id UUID := uuid_generate_v4();
    sys_acc UUID := (SELECT id FROM accounts WHERE account_number = 'SYS-EXTERNAL-000');
    alc_acc UUID := (SELECT id FROM accounts WHERE account_number = 'ALC-CHK-1002');
BEGIN
    INSERT INTO transactions (id, reference, status) VALUES (txn_id, 'SALARY-ALICE-001', 'completed');
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, sys_acc, 'debit', 3000);
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, alc_acc, 'credit', 3000);
END $$;

-- 🔁 2. Alice sends Bob money
DO $$
DECLARE
    txn_id UUID := uuid_generate_v4();
    alc_acc UUID := (SELECT id FROM accounts WHERE account_number = 'ALC-CHK-1002');
    bob_acc UUID := (SELECT id FROM accounts WHERE account_number = 'BOB-CHK-2002');
BEGIN
    INSERT INTO transactions (id, reference, status) VALUES (txn_id, 'P2P-ALICE-BOB-001', 'completed');
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, alc_acc, 'debit', 200);
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, bob_acc, 'credit', 200);
END $$;

-- 🛒 3. Bob purchases (debit card spend)
DO $$
DECLARE
    txn_id UUID := uuid_generate_v4();
    bob_acc UUID := (SELECT id FROM accounts WHERE account_number = 'BOB-CHK-2002');
    merch_acc UUID := (SELECT id FROM accounts WHERE account_number = 'SYS-EXTERNAL-000');
BEGIN
    INSERT INTO transactions (id, reference, status) VALUES (txn_id, 'POS-BOB-001', 'completed');
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, bob_acc, 'debit', 85.50);
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, merch_acc, 'credit', 85.50);
END $$;

-- 🏢 4. Business income (Charlie business account)
DO $$
DECLARE
    txn_id UUID := uuid_generate_v4();
    client_acc UUID := (SELECT id FROM accounts WHERE account_number = 'SYS-EXTERNAL-000');
    chr_acc UUID := (SELECT id FROM accounts WHERE account_number = 'CHR-BUS-3001');
BEGIN
    INSERT INTO transactions (id, reference, status) VALUES (txn_id, 'INVOICE-CHARLIE-001', 'completed');
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, client_acc, 'debit', 5000);
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, chr_acc, 'credit', 5000);
END $$;

-- 💳 5. Internal transfer (Diana savings → checking)
DO $$
DECLARE
    txn_id UUID := uuid_generate_v4();
    dia_sav UUID := (SELECT id FROM accounts WHERE account_number = 'DIA-SAV-4001');
    dia_chk UUID := (SELECT id FROM accounts WHERE account_number = 'DIA-CHK-4002');
BEGIN
    INSERT INTO transactions (id, reference, status) VALUES (txn_id, 'INT-TRANSFER-DIANA-001', 'completed');
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, dia_sav, 'debit', 1000);
    INSERT INTO transaction_entries (transaction_id, account_id, entry_type, amount) VALUES (txn_id, dia_chk, 'credit', 1000);
END $$;

\echo '✅ Mock data seeding completed successfully'
