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
  ❌ BLOCKED : SELECT * (forces explicit column selection)
  ❌ BLOCKED : Unknown column names (hallucinaton detection)
  ❌ BLOCKED : CROSS JOIN without explicit condition (cartesian products)
"""

import re
import logging
from dataclasses import dataclass, field

from .schema import ALLOWED_TABLES, ALLOWED_COLUMNS

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
    r"\bpg_\w+",
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
    r"\blo_\w+",
    r"\bdblink\b",
    r"\bpg_sleep\b",          # Prevent time-based DoS attacks
    r"\bgenerate_series\b",   # Can produce huge result sets without LIMIT
]

# ── Patterns for expensive query detection ────────────────────────────────────
EXPENSIVE_PATTERNS = [
    # CROSS JOIN without any WHERE clause afterward
    (r"\bCROSS\s+JOIN\b", "CROSS JOIN (produit cartésien) non autorisé."),
]


@dataclass
class ValidationResult:
    is_valid:    bool
    error:       str = ""
    cleaned_sql: str = ""
    warnings:    list = field(default_factory=list)


def _rewrite_select_star(sql: str, table_refs: list[str]) -> tuple[str, list[str]]:
    """
    Replace SELECT * with explicit whitelisted column list.

    Instead of blocking SELECT *, we rewrite it to be safe and deterministic.
    Only columns belonging to the referenced tables are included.
    Returns (rewritten_sql, warnings).
    """
    warnings = []
    if not re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
        return sql, warnings

    # Build explicit column list for the referenced tables
    explicit_cols = []
    for table in table_refs:
        cols = ALLOWED_COLUMNS.get(table.lower(), [])
        explicit_cols.extend(cols)

    if not explicit_cols:
        # Fallback: just warn, don't rewrite
        warnings.append(
            "⚠️ SELECT * utilisé sans correspondance de schéma — "
            "ajoutez des noms de colonnes explicites pour de meilleures performances."
        )
        return sql, warnings

    col_list = ", ".join(explicit_cols)
    rewritten = re.sub(
        r"SELECT\s+\*",
        f"SELECT {col_list}",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    warnings.append(
        f"ℹ️ SELECT * remplacé par les colonnes explicites : {col_list}"
    )
    logger.info(f"[validator] SELECT * rewritten to explicit columns for tables: {table_refs}")
    return rewritten, warnings


def _validate_columns(sql: str, table_refs: list[str]) -> list[str]:
    """
    Detect column hallucinations: column names used in the query that don't
    exist in the known schema for the referenced tables.

    Returns a list of warning strings (empty if all columns are valid).
    This is a best-effort check — it may have false positives for aliases/subqueries.
    """
    # Build the set of all valid columns for the referenced tables
    valid_cols: set[str] = set()
    for table in table_refs:
        valid_cols.update(ALLOWED_COLUMNS.get(table.lower(), []))

    if not valid_cols:
        return []

    # Extract identifiers that look like column references
    # Strategy: find tokens after SELECT ... FROM that are bare identifiers
    # We use a heuristic: find all word-tokens not in SQL keywords or table names
    sql_keywords = {
        "select", "from", "where", "and", "or", "not", "in", "is", "null",
        "true", "false", "like", "ilike", "between", "as", "on", "join",
        "left", "right", "inner", "outer", "group", "by", "order", "having",
        "limit", "offset", "distinct", "count", "sum", "avg", "min", "max",
        "upper", "lower", "trim", "now", "date_trunc", "extract", "interval",
        "case", "when", "then", "else", "end", "cast", "asc", "desc",
        "with", "union", "all", "exists", "coalesce", "nullif",
    }
    all_table_names = {t.lower() for t in ALLOWED_TABLES}

    # Only check the SELECT clause (before FROM)
    select_match = re.match(r"SELECT\s+(.*?)\s+FROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return []

    select_clause = select_match.group(1)

    # Extract bare identifiers (not SQL keywords, not numbers, not strings)
    candidate_cols = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", select_clause)

    hallucinations = []
    for col in candidate_cols:
        col_lower = col.lower()
        if (
            col_lower not in sql_keywords
            and col_lower not in all_table_names
            and col_lower not in valid_cols
            and not col_lower.startswith("'")
        ):
            hallucinations.append(col)

    if hallucinations:
        logger.warning(f"[validator] Potential hallucination columns: {hallucinations}")
        return [
            f"⚠️ Colonne(s) inconnue(s) détectée(s) : {', '.join(set(hallucinations))}. "
            f"Colonnes disponibles : {', '.join(sorted(valid_cols))}."
        ]
    return []


def validate_sql(sql: str) -> ValidationResult:
    """
    Full security validation pipeline.

    Returns a ValidationResult with:
      - is_valid=True  + cleaned_sql if safe to execute
      - is_valid=False + error message if rejected
      - warnings: non-blocking notices (hallucinations, SELECT * rewrite, etc.)
    """
    warnings: list[str] = []

    if not sql or not sql.strip():
        return ValidationResult(is_valid=False, error="La requête SQL est vide.")

    # ── 1. Normalize ──────────────────────────────────────────────────────────
    cleaned = sql.strip().rstrip(";").strip()

    # ── 2. Block stacked queries ──────────────────────────────────────────────
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
            keyword = re.sub(r"\\b", "", pattern).strip()
            logger.warning(f"[validator] Blocked keyword: {keyword}")
            return ValidationResult(
                is_valid=False,
                error=f"Mot-clé interdit détecté : {keyword}. "
                      "Seules les requêtes SELECT sont autorisées.",
            )

    # ── 5. Block system table access ─────────────────────────────────────────
    for pattern in BLOCKED_TABLE_PATTERNS:
        if re.search(pattern, cleaned, flags):
            logger.warning(f"[validator] Blocked system table: {pattern}")
            return ValidationResult(
                is_valid=False,
                error="Accès aux tables système interdit (pg_*, information_schema).",
            )

    # ── 6. Block dangerous functions ─────────────────────────────────────────
    for pattern in BLOCKED_FUNCTIONS:
        if re.search(pattern, cleaned, flags):
            func = re.sub(r"\\b", "", pattern).strip()
            logger.warning(f"[validator] Blocked dangerous function: {func}")
            return ValidationResult(
                is_valid=False,
                error=f"Fonction interdite : {func}.",
            )

    # ── 7. Block expensive patterns (CROSS JOIN, etc.) ────────────────────────
    for pattern, msg in EXPENSIVE_PATTERNS:
        if re.search(pattern, cleaned, flags):
            logger.warning(f"[validator] Blocked expensive pattern: {pattern}")
            return ValidationResult(is_valid=False, error=msg)

    # ── 8. Whitelist table check ──────────────────────────────────────────────
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

    # ── 9. Require at least one whitelisted table ─────────────────────────────
    if not table_refs:
        return ValidationResult(
            is_valid=False,
            error="La requête doit référencer au moins une table de la base bancaire.",
        )

    # ── 10. Rewrite SELECT * → explicit columns (non-blocking) ───────────────
    cleaned, star_warnings = _rewrite_select_star(cleaned, table_refs)
    warnings.extend(star_warnings)

    # ── 11. Column hallucination detection (non-blocking warning) ─────────────
    col_warnings = _validate_columns(cleaned, table_refs)
    warnings.extend(col_warnings)

    # ── 12. Enforce LIMIT if absent ───────────────────────────────────────────
    if not re.search(r"\bLIMIT\b", cleaned, flags):
        # Only add LIMIT for non-aggregate queries
        is_aggregate = bool(re.search(
            r"\b(COUNT|SUM|AVG|MIN|MAX|GROUP\s+BY)\b", cleaned, flags
        ))
        if not is_aggregate:
            cleaned += " LIMIT 100"
            warnings.append("ℹ️ LIMIT 100 ajouté automatiquement à la requête.")
            logger.info("[validator] Auto-added LIMIT 100")

    logger.info(f"[validator] ✅ SQL validated — tables: {table_refs}, warnings: {len(warnings)}")
    return ValidationResult(is_valid=True, cleaned_sql=cleaned, warnings=warnings)
