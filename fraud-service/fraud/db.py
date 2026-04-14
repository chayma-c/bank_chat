import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

# ── DB Config ───────────────────────────────────────────────────────────────

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("FRAUD_DB_HOST", "db"),
        database=os.getenv("FRAUD_DB_NAME", "banking_data"),
        user=os.getenv("FRAUD_DB_USER", "sql_user"),
        password=os.getenv("FRAUD_DB_PASSWORD", "sql_password"),
        port=os.getenv("FRAUD_DB_PORT", "5432")
    )

def init_db():
    """Create tables if they don't exist and initialize default settings."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Table for scheduling settings and last run status
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fraud_metadata (
                    id SERIAL PRIMARY KEY,
                    frequency VARCHAR(20) DEFAULT 'manual',
                    scheduled_time VARCHAR(10) DEFAULT '02:00',
                    day_of_week INTEGER DEFAULT 1,
                    last_run TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                );
            """)
            
            # Ensure at least one settings row exists
            cur.execute("SELECT COUNT(*) FROM fraud_metadata;")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                    INSERT INTO fraud_metadata (frequency, scheduled_time, day_of_week)
                    VALUES ('manual', '02:00', 1);
                """)

            # 2. Table for storing the Excel reports (BLOB)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fraud_reports (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    report_data BYTEA NOT NULL,
                    alert_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            print("✅ [fraud-db] Database initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"❌ [fraud-db] Error initializing database: {e}")
    finally:
        conn.close()

# ── Settings Management ──────────────────────────────────────────────────────

def get_settings():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM fraud_metadata LIMIT 1;")
            return cur.fetchone()
    finally:
        conn.close()

def update_settings(freq, sched_time, day_of_week):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE fraud_metadata 
                SET frequency = %s, scheduled_time = %s, day_of_week = %s
                WHERE id = (SELECT id FROM fraud_metadata LIMIT 1);
            """, (freq, sched_time, day_of_week))
            conn.commit()
    finally:
        conn.close()

def update_last_run():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE fraud_metadata 
                SET last_run = CURRENT_TIMESTAMP
                WHERE id = (SELECT id FROM fraud_metadata LIMIT 1);
            """, )
            conn.commit()
    finally:
        conn.close()

# ── Report Management ────────────────────────────────────────────────────────

def save_report_blob(filename, binary_data, alert_count):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO fraud_reports (filename, report_data, alert_count)
                VALUES (%s, %s, %s)
                RETURNING id;
            """, (filename, psycopg2.Binary(binary_data), alert_count))
            report_id = cur.fetchone()[0]
            conn.commit()
            return report_id
    finally:
        conn.close()

def get_report_blob(report_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT filename, report_data FROM fraud_reports WHERE id = %s;", (report_id,))
            return cur.fetchone()
    finally:
        conn.close()

def list_reports():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, filename, alert_count, created_at FROM fraud_reports ORDER BY created_at DESC;")
            return cur.fetchall()
    finally:
        conn.close()
