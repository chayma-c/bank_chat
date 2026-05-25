"""
SQL Generator — CORRIGÉ FINAL

Corrections apportées :
1. ✅ Instructions strictes pour NE JAMAIS interroger les tables système
2. ✅ Exemples concrets pour guider le LLM
3. ✅ Évite les JOINs inutiles
4. ✅ Évite les alias complexes (t.*, fd.*)
"""

import os
import re
import logging

from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

from .schema import get_schema_for_prompt, ALLOWED_TABLES, ALLOWED_COLUMNS

logger = logging.getLogger(__name__)

# ── LLM initialization ────────────────────────────────────────────────────────

def _build_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.getenv("OLLAMA_MODEL", "llama3.2")
        logger.info(f"[generator] Using Ollama: {model} @ {base_url}")
        return ChatOllama(base_url=base_url, model=model, temperature=0.0)
    else:
        api_key = os.getenv("GROQ_API_KEY")
        model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if not api_key:
            raise ValueError("GROQ_API_KEY is required.")
        logger.info(f"[generator] Using Groq: {model}")
        return ChatGroq(model=model, api_key=api_key, temperature=0.0)


_llm = None

def get_llm():
    global _llm
    if _llm is None:
        _llm = _build_llm()
    return _llm


# ── System prompt amélioré ────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    schema = get_schema_for_prompt()
    
    # Construire une liste claire des colonnes par table
    columns_list = []
    for table in sorted(ALLOWED_TABLES):
        cols = ALLOWED_COLUMNS.get(table, [])
        columns_list.append(f"  {table}: {', '.join(cols)}")
    
    columns_reference = "\n".join(columns_list)
    
    return f"""\
You are an expert PostgreSQL query generator for a banking fraud detection system.
Your job is to convert natural language questions into valid SQL SELECT queries.

{schema}

══════════════════════════════════════════════════════════
AVAILABLE COLUMNS (USE ONLY THESE):
══════════════════════════════════════════════════════════
{columns_reference}

═══════════════════════════════════════════════════════════════════════
CRITICAL SECURITY RULES — VIOLATION = QUERY REJECTED:
═══════════════════════════════════════════════════════════════════════
1. Output ONLY the raw SQL query — no markdown, no explanation.
2. Always use SELECT. NEVER use DELETE, UPDATE, INSERT, DROP, ALTER.
3. ⚠️ NEVER query system tables: pg_user, pg_catalog, information_schema, pg_* are STRICTLY FORBIDDEN.
4. Only query these 3 tables: transactions, fraud_rules, fraud_decision_logs.
5. Use explicit column names — NEVER use SELECT *.
6. ⚠️ AVOID JOINS unless absolutely necessary! Most questions need only 1 table.
7. Always add LIMIT 100 unless aggregating (COUNT, SUM, AVG, MAX, MIN).

═══════════════════════════════════════════════════════════════════════
DATE & STRING HANDLING:
═══════════════════════════════════════════════════════════════════════
- Dates: created_at >= '2024-01-01' AND created_at < '2024-02-01'
- Strings: UPPER(column) = UPPER('value') OR column ILIKE '%value%'

═══════════════════════════════════════════════════════════════════════
EXAMPLES (GOOD):
═══════════════════════════════════════════════════════════════════════
Q: "Combien de transactions bloquées ?"
A: SELECT COUNT(*) as total FROM transactions WHERE status = 'blocked';

Q: "Liste les transactions bloquées en janvier 2024"
A: SELECT transaction_id, user_id, amount, status, created_at 
   FROM transactions 
   WHERE status = 'blocked' 
   AND created_at >= '2024-01-01' 
   AND created_at < '2024-02-01' 
   ORDER BY created_at DESC 
   LIMIT 100;

Q: "Quelles règles AML sont actives ?"
A: SELECT id, name, domain, severity, points 
   FROM fraud_rules 
   WHERE domain = 'AML' AND active = TRUE 
   LIMIT 100;

Q: "Montant total des transactions frauduleuses"
A: SELECT SUM(amount) as total_amount 
   FROM transactions 
   WHERE is_fraudulent = TRUE;

Q: "Top 5 des marchands par volume"
A: SELECT merchant_name, COUNT(*) as transaction_count 
   FROM transactions 
   GROUP BY merchant_name 
   ORDER BY transaction_count DESC 
   LIMIT 5;

═══════════════════════════════════════════════════════════════════════
FORBIDDEN PATTERNS (WILL BE REJECTED):
═══════════════════════════════════════════════════════════════════════
❌ SELECT * FROM pg_user                          # System table
❌ SELECT * FROM information_schema.tables        # System catalog
❌ SELECT t.* FROM transactions t                 # Alias with *
❌ SELECT * FROM transactions; DROP TABLE ...     # Stacked queries
❌ SELECT DISTINCT t.* FROM transactions t 
   JOIN fraud_decision_logs fd ...                # Unnecessary JOIN

═══════════════════════════════════════════════════════════════════════
IF THE QUESTION ASKS FOR SYSTEM TABLES OR USERS:
═══════════════════════════════════════════════════════════════════════
If the user asks to query system tables (pg_*, information_schema, users, etc.):
OUTPUT EXACTLY THIS:
SELECT 'SECURITY ERROR: System table access is forbidden. This system only allows queries on banking data tables: transactions, fraud_rules, fraud_decision_logs.' AS error_message;

Output format: A single valid SQL SELECT statement ending with a semicolon.
"""


# ── SQL extraction helper ─────────────────────────────────────────────────────

def _extract_sql(raw: str) -> str:
    """Extract clean SQL from LLM output."""
    # Remove markdown fences
    raw = re.sub(r"```(?:sql)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)
    
    # Extract first SELECT ... ; block
    match = re.search(r"(SELECT\b.*?)(?:;|$)", raw, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip() + ";"
    
    return raw.strip()


# ── Main generation function ──────────────────────────────────────────────────

def generate_sql(question: str) -> dict:
    """
    Convert natural language to SQL.
    
    Returns:
        {
            "sql": str,
            "raw_llm": str,
            "error": str | None
        }
    """
    logger.info(f"[generator] Question: {question!r}")
    
    try:
        llm = get_llm()
        messages = [
            SystemMessage(content=_build_system_prompt()),
            HumanMessage(content=f"Question: {question}"),
        ]
        response = llm.invoke(messages)
        raw = response.content.strip()
        sql = _extract_sql(raw)
        
        logger.info(f"[generator] Generated: {sql[:200]}")
        return {"sql": sql, "raw_llm": raw, "error": None}
    
    except Exception as exc:
        logger.exception(f"[generator] Error: {exc}")
        return {
            "sql": "",
            "raw_llm": "",
            "error": f"Erreur génération SQL : {str(exc)}",
        }
