"""
SQL Executor — Runs validated SELECT queries against PostgreSQL banking_data.

Security measures:
  - Read-only connection (sql_user has SELECT privileges only)
  - Statement timeout: 30 seconds max
  - Result limit: 100 rows max
  - All exceptions are caught and returned as structured errors
"""

import os
import logging
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL  = os.getenv("DATABASE_URL", "")
MAX_ROWS      = int(os.getenv("SQL_MAX_ROWS", "100"))
QUERY_TIMEOUT = int(os.getenv("SQL_TIMEOUT_MS", "30000"))  # 30 s in milliseconds


def _get_connection():
    """Open a fresh psycopg2 connection to banking_data."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured for text-to-sql-service."
        )
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def execute_query(sql: str) -> dict:
    """
    Execute a validated SQL SELECT statement.

    Returns:
        {
            "rows":         list[dict],   # result rows as list of dicts
            "columns":      list[str],    # column names
            "row_count":    int,          # number of rows returned
            "truncated":    bool,         # True if result was capped at MAX_ROWS
            "error":        str | None    # error message on failure
        }
    """
    logger.info(f"[executor] Running query: {sql[:200]}")
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Set per-statement timeout to prevent runaway queries
            cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT};")

            # Execute the actual query
            cur.execute(sql)

            # Fetch with a hard cap
            raw_rows: list[dict[str, Any]] = cur.fetchmany(MAX_ROWS + 1)

        truncated = len(raw_rows) > MAX_ROWS
        rows = raw_rows[:MAX_ROWS]

        # Serialize: convert non-JSON-serializable types to strings
        serialized = []
        for row in rows:
            serialized.append({
                k: _serialize_value(v)
                for k, v in dict(row).items()
            })

        columns = list(serialized[0].keys()) if serialized else []

        logger.info(
            f"[executor] ✅ Query returned {len(serialized)} rows "
            f"(truncated={truncated})"
        )
        return {
            "rows":      serialized,
            "columns":   columns,
            "row_count": len(serialized),
            "truncated": truncated,
            "error":     None,
        }

    except psycopg2.errors.QueryCanceled:
        logger.warning("[executor] Query timed out")
        return _error_result(
            f"La requête a dépassé le délai maximal de {QUERY_TIMEOUT // 1000} secondes."
        )
    except psycopg2.errors.InsufficientPrivilege as exc:
        logger.warning(f"[executor] Permission denied: {exc}")
        return _error_result("Permission refusée : opération non autorisée.")
    except psycopg2.Error as exc:
        logger.error(f"[executor] PostgreSQL error: {exc}")
        return _error_result(f"Erreur PostgreSQL : {exc.pgerror or str(exc)}")
    except Exception as exc:
        logger.exception(f"[executor] Unexpected error: {exc}")
        return _error_result(f"Erreur interne : {str(exc)}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _serialize_value(v: Any) -> Any:
    """Convert psycopg2 types that are not JSON-serializable to strings."""
    import datetime
    import decimal
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dict, list, bool, int, float, str)):
        return v
    return str(v)


def _error_result(message: str) -> dict:
    return {
        "rows":      [],
        "columns":   [],
        "row_count": 0,
        "truncated": False,
        "error":     message,
    }
