"""
SQL Security Validator

Enforces a strict READ-ONLY whitelist policy on all generated SQL queries.
Any query that does not pass validation is rejected before execution.

Rules:
  ✅ ALLOWED : SELECT ... FROM [whitelisted_tables]
  ❌ BLOCKED : DELETE, UPDATE, INSERT, DROP, ALTER, TRUNCATE, CREATE
  ❌ BLOCKED : Access to system tables (pg_*, information_schema)
  ❌ BLOCKED : Dangerous functions (pg_read_file, copy, lo_*)
  ❌ BLOCKED : Stacked queries (semicolons inside statements)
  ❌ BLOCKED : Any table not in the explicit whitelist
"""

import re
import logging
from dataclasses import dataclass

from .schema import ALLOWED_TABLES

logger = logging.getLogger(__name__)

# ── Blocked SQL keywords (case-insensitive word boundary match) ───────────────
BLOCKED_KEYWORDS = [
    r"\bDELETE\b",
    r"\bUPDATE\b",
    r"\bINSERT\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bTRUNCATE\b",
    r"\bCREATE\b",
    r"\bREPLACE\b",
    r"\bMERGE\b",
    r"\bCALL\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
]

# ── Blocked system table patterns ─────────────────────────────────────────────
BLOCKED_TABLE_PATTERNS = [
    r"\bpg_\w+",                    # pg_catalog tables
    r"\binformation_schema\b",
    r"\bpg_catalog\b",
    r"\bpg_shadow\b",
    r"\bpg_user\b",
    r"\bpg_roles\b",
    r"\bpg_auth\w+",
]

# ── Blocked dangerous functions ───────────────────────────────────────────────
BLOCKED_FUNCTIONS = [
    r"\bpg_read_file\b",
    r"\bpg_ls_dir\b",
    r"\bpg_write_file\b",
    r"\bcopy\b",
    r"\blo_\w+",                    # Large object functions
    r"\bdblink\b",
]


@dataclass
class ValidationResult:
    is_valid: bool
    error: str = ""
    cleaned_sql: str = ""


def validate_sql(sql: str) -> ValidationResult:
    """
    Full security validation pipeline.

    Returns a ValidationResult with is_valid=True and the cleaned SQL
    if safe, or is_valid=False with an error message if rejected.
    """
    if not sql or not sql.strip():
        return ValidationResult(is_valid=False, error="La requête SQL est vide.")

    # ── 1. Normalize: strip trailing semicolons & whitespace ─────────────────
    cleaned = sql.strip().rstrip(";").strip()

    # ── 2. Block stacked queries (multiple statements) ────────────────────────
    # A semicolon inside the query (not at the end) signals stacking
    if ";" in cleaned:
        logger.warning("[validator] Blocked: stacked queries detected")
        return ValidationResult(
            is_valid=False,
            error="Requêtes multiples (;) non autorisées.",
        )

    flags = re.IGNORECASE

    # ── 3. Must start with SELECT ─────────────────────────────────────────────
    if not re.match(r"^\s*SELECT\b", cleaned, flags):
        logger.warning("[validator] Blocked: query does not start with SELECT")
        return ValidationResult(
            is_valid=False,
            error="Seules les requêtes SELECT sont autorisées.",
        )

    # ── 4. Block dangerous DML/DDL keywords ──────────────────────────────────
    for pattern in BLOCKED_KEYWORDS:
        if re.search(pattern, cleaned, flags):
            keyword = pattern.replace(r"\b", "").strip()
            logger.warning(f"[validator] Blocked keyword: {keyword}")
            return ValidationResult(
                is_valid=False,
                error=f"Mot-clé interdit détecté : {keyword}. "
                      "Seules les requêtes SELECT sont autorisées.",
            )

    # ── 5. Block system table access ─────────────────────────────────────────
    for pattern in BLOCKED_TABLE_PATTERNS:
        if re.search(pattern, cleaned, flags):
            logger.warning(f"[validator] Blocked system table pattern: {pattern}")
            return ValidationResult(
                is_valid=False,
                error="Accès aux tables système interdit (pg_*, information_schema).",
            )

    # ── 6. Block dangerous functions ─────────────────────────────────────────
    for pattern in BLOCKED_FUNCTIONS:
        if re.search(pattern, cleaned, flags):
            func = pattern.replace(r"\b", "").strip()
            logger.warning(f"[validator] Blocked dangerous function: {func}")
            return ValidationResult(
                is_valid=False,
                error=f"Fonction interdite : {func}.",
            )

    # ── 7. Whitelist table check ──────────────────────────────────────────────
    # Extract all identifiers that appear after FROM or JOIN
    table_refs = re.findall(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        cleaned,
        flags,
    )
    for table in table_refs:
        if table.lower() not in ALLOWED_TABLES:
            logger.warning(f"[validator] Blocked: table '{table}' not in whitelist")
            return ValidationResult(
                is_valid=False,
                error=f"Table '{table}' non autorisée. "
                      f"Tables disponibles : {', '.join(sorted(ALLOWED_TABLES))}.",
            )

    # ── 8. Require at least one whitelisted table ─────────────────────────────
    if not table_refs:
        return ValidationResult(
            is_valid=False,
            error="La requête doit référencer au moins une table de la base bancaire.",
        )

    logger.info(f"[validator] ✅ SQL validated — tables: {table_refs}")
    return ValidationResult(is_valid=True, cleaned_sql=cleaned)
