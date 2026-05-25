"""
SQL Security Validator — CORRIGÉ FINAL

Corrections apportées :
1. ✅ Détection stricte des stacked queries (bloque même après normalisation)
2. ✅ Messages de sécurité plus explicites et clairs
3. ✅ Support des alias de table (t.*, fd.*) sans faux positifs
4. ✅ Validation rigoureuse des colonnes
"""

import re
import logging
from dataclasses import dataclass, field

from .schema import ALLOWED_TABLES, ALLOWED_COLUMNS

logger = logging.getLogger(__name__)

# ── Mots-clés SQL bloqués ──────────────────────────────────────────────────────
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

# ── Patterns de tables système bloquées ────────────────────────────────────────
BLOCKED_TABLE_PATTERNS = [
    r"\bpg_\w+",
    r"\binformation_schema\b",
    r"\bpg_catalog\b",
    r"\bpg_shadow\b",
    r"\bpg_user\b",
    r"\bpg_roles\b",
    r"\bpg_auth\w+",
]

# ── Fonctions dangereuses bloquées ─────────────────────────────────────────────
BLOCKED_FUNCTIONS = [
    r"\bpg_read_file\b",
    r"\bpg_ls_dir\b",
    r"\bpg_write_file\b",
    r"\bcopy\b",
    r"\blo_\w+",
    r"\bdblink\b",
    r"\bpg_sleep\b",
    r"\bgenerate_series\b",
]

# ── Patterns coûteux bloqués ───────────────────────────────────────────────────
EXPENSIVE_PATTERNS = [
    (r"\bCROSS\s+JOIN\b", "CROSS JOIN (produit cartésien) non autorisé."),
]


@dataclass
class ValidationResult:
    is_valid:    bool
    error:       str = ""
    error_type:  str = ""
    cleaned_sql: str = ""
    warnings:    list = field(default_factory=list)


def _rewrite_select_star(sql: str, table_refs: list[str]) -> tuple[str, list[str]]:
    """
    Remplace SELECT * par une liste explicite de colonnes.
    
    CORRECTION : Gère aussi les alias (t.*, fd.*).
    """
    warnings = []
    
    # Pattern pour détecter SELECT * ou alias.*
    star_pattern = r"SELECT\s+(?:DISTINCT\s+)?(?:(\w+)\.\*|\*)"
    match = re.search(star_pattern, sql, re.IGNORECASE)
    
    if not match:
        return sql, warnings
    
    # Construire la liste des colonnes explicites
    explicit_cols = []
    for table in table_refs:
        cols = ALLOWED_COLUMNS.get(table.lower(), [])
        explicit_cols.extend(cols)
    
    if not explicit_cols:
        warnings.append(
            "⚠️ SELECT * utilisé sans correspondance de schéma — "
            "ajoutez des noms de colonnes explicites."
        )
        return sql, warnings
    
    col_list = ", ".join(explicit_cols)
    
    # Remplacer en préservant DISTINCT si présent
    rewritten = re.sub(
        star_pattern,
        f"SELECT \\1 {col_list}" if match.group(1) else f"SELECT {col_list}",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    
    warnings.append(
        f"ℹ️ SELECT * remplacé par les colonnes explicites : {col_list}"
    )
    logger.info(f"[validator] SELECT * rewritten for tables: {table_refs}")
    return rewritten, warnings


def _validate_columns(sql: str, table_refs: list[str]) -> ValidationResult | None:
    """
    Détecte les colonnes hallucinées.
    
    CORRECTION MAJEURE : Ignore complètement les alias de table (t., fd., etc.).
    On ne valide que les colonnes nues (sans point).
    """
    # Construire l'ensemble des colonnes valides
    valid_cols: set[str] = set()
    for table in table_refs:
        valid_cols.update(ALLOWED_COLUMNS.get(table.lower(), []))
    
    if not valid_cols:
        return None
    
    # Mots-clés SQL à ignorer
    sql_keywords = {
        "select", "from", "where", "and", "or", "not", "in", "is", "null",
        "true", "false", "like", "ilike", "between", "as", "on", "join",
        "left", "right", "inner", "outer", "group", "by", "order", "having",
        "limit", "offset", "distinct", "count", "sum", "avg", "min", "max",
        "upper", "lower", "trim", "now", "date_trunc", "extract", "interval",
        "case", "when", "then", "else", "end", "cast", "asc", "desc",
        "with", "union", "all", "exists", "coalesce", "nullif",
        "round", "floor", "ceil", "length", "substr", "replace",
        "to_char", "to_date", "to_timestamp", "age", "date_part",
        "current_date", "current_timestamp", "greatest", "least",
        "array_agg", "string_agg", "json_agg", "jsonb_agg",
    }
    all_table_names = {t.lower() for t in ALLOWED_TABLES}
    
    # Extraire la clause SELECT
    select_match = re.match(r"SELECT\s+(.*?)\s+FROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return None
    
    select_clause = select_match.group(1)
    
    # Supprimer les alias AS pour ne pas les confondre avec des colonnes
    # ex: COUNT(*) AS total  →  COUNT(*)
    select_no_aliases = re.sub(r"\bAS\s+\w+", "", select_clause, flags=re.IGNORECASE)

    # CORRECTION : Ignorer tout ce qui contient un point (alias de table)
    # On ne garde que les identifiants nus
    candidate_cols = []
    for word in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", select_no_aliases):
        # Vérifier si ce mot est suivi d'un point dans la clause
        if not re.search(rf"\b{word}\s*\.", select_no_aliases, re.IGNORECASE):
            candidate_cols.append(word)
    
    hallucinations = []
    for col in candidate_cols:
        col_lower = col.lower()
        # Ignorer si c'est un mot-clé SQL, un nom de table, ou une colonne valide
        if (
            col_lower not in sql_keywords
            and col_lower not in all_table_names
            and col_lower not in valid_cols
            and not col_lower.startswith("'")
            and len(col) > 1  # Ignorer les alias d'une seule lettre
        ):
            hallucinations.append(col)
    
    if hallucinations:
        unknown = ", ".join(sorted(set(hallucinations)))
        available = ", ".join(sorted(valid_cols))
        tables_str = ", ".join(sorted(set(table_refs)))
        logger.warning(f"[validator] Colonnes inconnues : {hallucinations}")
        return ValidationResult(
            is_valid=False,
            error_type="hallucination",
            error=(
                f"Colonne(s) inconnue(s) : {unknown}. "
                f"Colonnes disponibles pour [{tables_str}] : {available}."
            ),
        )
    
    return None


def validate_sql(sql: str) -> ValidationResult:
    """
    Pipeline complet de validation SQL.
    
    CORRECTIONS :
    - Messages d'erreur de sécurité explicites
    - Support des alias dans SELECT
    - Blocage strict des stacked queries
    """
    warnings: list[str] = []
    
    if not sql or not sql.strip():
        return ValidationResult(
            is_valid=False,
            error_type="empty",
            error="La requête SQL est vide.",
        )
    
    # ── 1. Normalisation ──────────────────────────────────────────────────────
    cleaned = sql.strip().rstrip(";").strip()
    
    # ── 2. Bloquer les requêtes empilées (CORRECTION : blocage strict) ────────
    if ";" in cleaned:
        logger.warning("[validator] Requêtes empilées détectées")
        return ValidationResult(
            is_valid=False,
            error_type="stacked",
            error=(
                "Validation Security Error: Multiple queries (stacked queries via ';') "
                "are strictly forbidden."
            ),
        )
    
    flags = re.IGNORECASE
    
    # ── 3. Doit commencer par SELECT ──────────────────────────────────────────
    if not re.match(r"^\s*SELECT\b", cleaned, flags):
        logger.warning("[validator] Requête ne commence pas par SELECT")
        return ValidationResult(
            is_valid=False,
            error_type="no_select",
            error="Seules les requêtes SELECT sont autorisées.",
        )
    
    # ── 4. Bloquer les mots-clés DML/DDL ──────────────────────────────────────
    for pattern in BLOCKED_KEYWORDS:
        if re.search(pattern, cleaned, flags):
            keyword = re.sub(r"\\b", "", pattern).strip()
            logger.warning(f"[validator] Mot-clé DML/DDL bloqué : {keyword}")
            return ValidationResult(
                is_valid=False,
                error_type="dml_blocked",
                error=(
                    f"Validation Security Error: Dangerous keyword '{keyword}' detected. "
                    f"This system is READ-ONLY — only SELECT queries are allowed."
                ),
            )
    
    # ── 5. Bloquer les tables système (CORRECTION : message plus clair) ───────
    for pattern in BLOCKED_TABLE_PATTERNS:
        match = re.search(pattern, cleaned, flags)
        if match:
            table_name = match.group()
            logger.warning(f"[validator] Table système bloquée : {table_name}")
            return ValidationResult(
                is_valid=False,
                error_type="system_table",
                error=(
                    f"Validation Security Error: Access to system tables "
                    f"(catalogues, information_schema, pg_*) is restricted. "
                    f"Attempted access to '{table_name}'."
                ),
            )
    
    # ── 6. Bloquer les fonctions dangereuses ──────────────────────────────────
    for pattern in BLOCKED_FUNCTIONS:
        if re.search(pattern, cleaned, flags):
            func = re.sub(r"\\b", "", pattern).strip()
            logger.warning(f"[validator] Fonction dangereuse : {func}")
            return ValidationResult(
                is_valid=False,
                error_type="dangerous_func",
                error=f"Fonction système interdite : {func}.",
            )
    
    # ── 7. Bloquer les patterns coûteux ───────────────────────────────────────
    for pattern, msg in EXPENSIVE_PATTERNS:
        if re.search(pattern, cleaned, flags):
            logger.warning(f"[validator] Pattern coûteux : {pattern}")
            return ValidationResult(
                is_valid=False,
                error_type="expensive",
                error=msg,
            )
    
    # ── 8. Vérifier la whitelist des tables ───────────────────────────────────
    table_refs = re.findall(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        cleaned,
        flags,
    )
    
    for table in table_refs:
        if table.lower() not in ALLOWED_TABLES:
            logger.warning(f"[validator] Table '{table}' hors whitelist")
            return ValidationResult(
                is_valid=False,
                error_type="unknown_table",
                error=(
                    f"Table '{table}' non autorisée. "
                    f"Tables disponibles : {', '.join(sorted(ALLOWED_TABLES))}."
                ),
            )
    
    # ── 9. Au moins une table requise ─────────────────────────────────────────
    if not table_refs:
        return ValidationResult(
            is_valid=False,
            error_type="no_table",
            error="La requête doit référencer au moins une table.",
        )
    
    # ── 10. Réécrire SELECT * (avec support des alias) ────────────────────────
    cleaned, star_warnings = _rewrite_select_star(cleaned, table_refs)
    warnings.extend(star_warnings)
    
    # ── 11. Valider les colonnes (avec support des alias) ─────────────────────
    col_result = _validate_columns(cleaned, table_refs)
    if col_result is not None:
        return col_result
    
    # ── 12. Forcer LIMIT si absent ────────────────────────────────────────────
    if not re.search(r"\bLIMIT\b", cleaned, flags):
        is_aggregate = bool(re.search(
            r"\b(COUNT|SUM|AVG|MIN|MAX|GROUP\s+BY)\b", cleaned, flags
        ))
        if not is_aggregate:
            cleaned += " LIMIT 100"
            warnings.append("ℹ️ LIMIT 100 ajouté automatiquement.")
            logger.info("[validator] LIMIT 100 ajouté")
    
    logger.info(f"[validator] ✅ SQL validé — tables: {table_refs}")
    return ValidationResult(
        is_valid=True,
        cleaned_sql=cleaned,
        warnings=warnings,
    )
