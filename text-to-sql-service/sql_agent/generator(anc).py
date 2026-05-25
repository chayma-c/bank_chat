"""
SQL Generator — Natural Language → SQL via LLM

Uses Groq (or Ollama as fallback) with the banking schema injected into
the system prompt. The LLM is instructed to return ONLY a raw SQL SELECT
statement with no markdown fences or explanation.
"""

import os
import re
import logging

from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

from .schema import get_schema_for_prompt

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
            raise ValueError("GROQ_API_KEY is required for Groq provider.")
        logger.info(f"[generator] Using Groq: {model}")
        return ChatGroq(model=model, api_key=api_key, temperature=0.0)


_llm = None

def get_llm():
    global _llm
    if _llm is None:
        _llm = _build_llm()
    return _llm


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    schema = get_schema_for_prompt()
    return f"""\
You are an expert PostgreSQL query generator for a banking fraud detection system.
Your ONLY job is to convert a user's natural language question into a valid SQL SELECT query.

{schema}

STRICT RULES:
1. Output ONLY the raw SQL query — no markdown, no explanation, no comments.
2. Always use SELECT. NEVER use DELETE, UPDATE, INSERT, DROP, ALTER, TRUNCATE, CREATE.
3. Only query tables listed above: transactions, fraud_rules, fraud_decision_logs.
4. Always add LIMIT 100 unless the user asks for aggregation (COUNT, SUM, AVG, etc.).
5. Use clear column aliases when aggregating (e.g., COUNT(*) AS total).
6. Use date functions for temporal queries: NOW(), date_trunc(), INTERVAL.
7. All string comparisons must use UPPER() or ILIKE for case-insensitivity.
8. For IBAN comparisons always use UPPER(iban).
9. If the question is ambiguous or cannot be answered with the available schema, output:
   SELECT 'Question non applicable au schéma disponible' AS message;

Output format: A single valid SQL SELECT statement ending with a semicolon.
"""


# ── SQL extraction helper ─────────────────────────────────────────────────────

def _extract_sql(raw: str) -> str:
    """
    Extract a clean SQL statement from LLM output.
    Handles markdown code blocks, inline backticks, extra prose.
    """
    # Remove ```sql ... ``` or ``` ... ``` fences
    raw = re.sub(r"```(?:sql)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)

    # Take only the first SELECT … ; block
    match = re.search(r"(SELECT\b.*?)(?:;|$)", raw, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip() + ";"

    # Fallback: return cleaned text
    return raw.strip()


# ── Main generation function ──────────────────────────────────────────────────

def generate_sql(question: str) -> dict:
    """
    Convert a natural language question into a SQL query.

    Returns:
        {
            "sql": str,          # generated SQL statement
            "raw_llm": str,      # raw LLM output (for debugging)
            "error": str | None  # error message if generation failed
        }
    """
    logger.info(f"[generator] Generating SQL for: {question!r}")

    try:
        llm = get_llm()
        messages = [
            SystemMessage(content=_build_system_prompt()),
            HumanMessage(content=f"Question: {question}"),
        ]
        response = llm.invoke(messages)
        raw = response.content.strip()
        sql = _extract_sql(raw)

        logger.info(f"[generator] Generated SQL: {sql[:200]}")
        return {"sql": sql, "raw_llm": raw, "error": None}

    except Exception as exc:
        logger.exception(f"[generator] LLM error: {exc}")
        return {
            "sql": "",
            "raw_llm": "",
            "error": f"Erreur lors de la génération SQL : {str(exc)}",
        }
