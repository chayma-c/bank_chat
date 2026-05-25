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
    # Catégorie de l'erreur — utilisée par main.py et nodes.py pour
    # afficher un message convivial adapté au contexte :
    #   "dml_blocked"    → DELETE / UPDATE / INSERT / DROP / etc.
    #   "stacked"        → requêtes multiples via ;
    #   "no_select"      → requête ne commence pas par SELECT
    #   "system_table"   → accès pg_* / information_schema
    #   "dangerous_func" → pg_read_file, dblink, …
    #   "expensive"      → CROSS JOIN, …
    #   "unknown_table"  → table absente de la whitelist
    #   "no_table"       → aucune table référencée
    #   "hallucination"  → colonne inconnue dans le schéma
    #   ""               → succès (is_valid=True)
    error_type:  str = ""
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


def _validate_columns(sql: str, table_refs: list[str]) -> ValidationResult | None:
    """
    Détecte les hallucinations de colonnes : noms de colonnes utilisés dans la
    requête qui n'existent pas dans le schéma connu des tables référencées.

    Retourne :
      - None  si toutes les colonnes sont valides (ou non vérifiables)
      - ValidationResult(is_valid=False, error_type="hallucination", …)
        si des colonnes inconnues sont trouvées.

    ⚠️  Ce contrôle est désormais BLOQUANT : une requête avec des colonnes
    inconnues est rejetée AVANT d'être envoyée à PostgreSQL.
    Heuristique : on ne vérifie que la clause SELECT (avant FROM) pour
    limiter les faux-positifs liés aux alias ou aux sous-requêtes.
    """
    # Construire l'ensemble des colonnes valides pour les tables référencées
    valid_cols: set[str] = set()
    for table in table_refs:
        valid_cols.update(ALLOWED_COLUMNS.get(table.lower(), []))

    if not valid_cols:
        # Schéma inconnu pour ces tables → on ne peut pas vérifier, on laisse passer
        return None

    # Mots-clés SQL et fonctions à ignorer lors de l'analyse de la clause SELECT
    sql_keywords = {
        "select", "from", "where", "and", "or", "not", "in", "is", "null",
        "true", "false", "like", "ilike", "between", "as", "on", "join",
        "left", "right", "inner", "outer", "group", "by", "order", "having",
        "limit", "offset", "distinct", "count", "sum", "avg", "min", "max",
        "upper", "lower", "trim", "now", "date_trunc", "extract", "interval",
        "case", "when", "then", "else", "end", "cast", "asc", "desc",
        "with", "union", "all", "exists", "coalesce", "nullif",
        # Fonctions supplémentaires fréquentes
        "round", "floor", "ceil", "length", "substr", "replace",
        "to_char", "to_date", "to_timestamp", "age", "date_part",
        "current_date", "current_timestamp", "greatest", "least",
        "array_agg", "string_agg", "json_agg", "jsonb_agg",
    }
    all_table_names = {t.lower() for t in ALLOWED_TABLES}

    # Analyser uniquement la clause SELECT (avant FROM)
    select_match = re.match(r"SELECT\s+(.*?)\s+FROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return None

    select_clause = select_match.group(1)

    # Extraire les identifiants bruts (exclusion des mots-clés, nombres, chaînes)
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
        unknown = ", ".join(sorted(set(hallucinations)))
        available = ", ".join(sorted(valid_cols))
        tables_str = ", ".join(sorted(set(table_refs)))
        logger.warning(f"[validator] Colonnes inconnues BLOQUÉES : {hallucinations}")
        return ValidationResult(
            is_valid=False,
            error_type="hallucination",
            error=(
                f"Colonne(s) inconnue(s) dans le schéma : {unknown}. "
                f"Colonnes disponibles pour [{tables_str}] : {available}."
            ),
        )
    return None


def validate_sql(sql: str) -> ValidationResult:
    """
    Pipeline complet de validation de sécurité SQL.

    Retourne un ValidationResult avec :
      - is_valid=True  + cleaned_sql   → requête sûre, prête à l'exécution
      - is_valid=False + error         → requête rejetée (+ error_type pour message UI adapté)
      - warnings                       → avis non-bloquants (réécriture SELECT *, LIMIT auto)
    """
    warnings: list[str] = []

    if not sql or not sql.strip():
        return ValidationResult(
            is_valid=False, error_type="empty",
            error="La requête SQL est vide.",
        )

    # ── 1. Normalisation ──────────────────────────────────────────────────────
    cleaned = sql.strip().rstrip(";").strip()

    # ── 2. Bloquer les requêtes empilées (stacked queries) ────────────────────
    if ";" in cleaned:
        logger.warning("[validator] Bloqué : requêtes empilées détectées")
        return ValidationResult(
            is_valid=False, error_type="stacked",
            error="Requêtes multiples séparées par ';' non autorisées.",
        )

    flags = re.IGNORECASE

    # ── 3. Doit commencer par SELECT ──────────────────────────────────────────
    if not re.match(r"^\s*SELECT\b", cleaned, flags):
        logger.warning("[validator] Bloqué : requête ne commence pas par SELECT")
        return ValidationResult(
            is_valid=False, error_type="no_select",
            error="Seules les requêtes SELECT sont autorisées.",
        )

    # ── 4. Bloquer les mots-clés DML/DDL dangereux ───────────────────────────
    for pattern in BLOCKED_KEYWORDS:
        if re.search(pattern, cleaned, flags):
            keyword = re.sub(r"\\b", "", pattern).strip()
            logger.warning(f"[validator] Mot-clé bloqué : {keyword}")
            return ValidationResult(
                is_valid=False, error_type="dml_blocked",
                error=f"Opération interdite détectée : {keyword}. "
                      "Ce système est en lecture seule — seules les requêtes SELECT sont autorisées.",
            )

    # ── 5. Bloquer l'accès aux tables système ────────────────────────────────
    for pattern in BLOCKED_TABLE_PATTERNS:
        if re.search(pattern, cleaned, flags):
            logger.warning(f"[validator] Table système bloquée : {pattern}")
            return ValidationResult(
                is_valid=False, error_type="system_table",
                error="Accès aux tables système interdit (pg_*, information_schema, pg_catalog).",
            )

    # ── 6. Bloquer les fonctions dangereuses ─────────────────────────────────
    for pattern in BLOCKED_FUNCTIONS:
        if re.search(pattern, cleaned, flags):
            func = re.sub(r"\\b", "", pattern).strip()
            logger.warning(f"[validator] Fonction dangereuse bloquée : {func}")
            return ValidationResult(
                is_valid=False, error_type="dangerous_func",
                error=f"Fonction système interdite : {func}.",
            )

    # ── 7. Bloquer les patterns coûteux (CROSS JOIN, …) ──────────────────────
    for pattern, msg in EXPENSIVE_PATTERNS:
        if re.search(pattern, cleaned, flags):
            logger.warning(f"[validator] Pattern coûteux bloqué : {pattern}")
            return ValidationResult(is_valid=False, error_type="expensive", error=msg)

    # ── 8. Vérification de la whitelist des tables ────────────────────────────
    table_refs = re.findall(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        cleaned,
        flags,
    )
    for table in table_refs:
        if table.lower() not in ALLOWED_TABLES:
            logger.warning(f"[validator] Table '{table}' absente de la whitelist")
            return ValidationResult(
                is_valid=False, error_type="unknown_table",
                error=(
                    f"Table '{table}' hors périmètre. "
                    f"Tables autorisées : {', '.join(sorted(ALLOWED_TABLES))}."
                ),
            )

    # ── 9. Au moins une table whitelistée requise ─────────────────────────────
    if not table_refs:
        return ValidationResult(
            is_valid=False, error_type="no_table",
            error="La requête doit référencer au moins une table de la base bancaire.",
        )

    # ── 10. Réécriture SELECT * → colonnes explicites (non-bloquant) ──────────
    cleaned, star_warnings = _rewrite_select_star(cleaned, table_refs)
    warnings.extend(star_warnings)

    # ── 11. Détection des colonnes inconnues (BLOQUANT) ───────────────────────
    #  On vérifie après la réécriture SELECT * pour travailler sur le SQL propre.
    col_result = _validate_columns(cleaned, table_refs)
    if col_result is not None:
        # _validate_columns retourne un ValidationResult(is_valid=False) si hallucination
        return col_result

    # ── 12. Forcer LIMIT si absent ────────────────────────────────────────────
    if not re.search(r"\bLIMIT\b", cleaned, flags):
        is_aggregate = bool(re.search(
            r"\b(COUNT|SUM|AVG|MIN|MAX|GROUP\s+BY)\b", cleaned, flags
        ))
        if not is_aggregate:
            cleaned += " LIMIT 100"
            warnings.append("ℹ️ LIMIT 100 ajouté automatiquement à la requête.")
            logger.info("[validator] LIMIT 100 ajouté automatiquement")

    logger.info(f"[validator] ✅ SQL validé — tables: {table_refs}, avertissements: {len(warnings)}")
    return ValidationResult(is_valid=True, cleaned_sql=cleaned, warnings=warnings)
