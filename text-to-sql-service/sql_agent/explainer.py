"""
SQL Explainer — Formats query results and generates business-level explanations.

Two responsibilities:
  1. Format raw rows into a readable markdown table
  2. Call the LLM to generate a short, actionable business explanation in French
"""

import os
import json
import logging

from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

# ── LLM (lazy, same provider as generator) ────────────────────────────────────
_llm = None

def _get_llm():
    global _llm
    if _llm is not None:
        return _llm
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "ollama":
        _llm = ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            temperature=0.3,
        )
    else:
        _llm = ChatGroq(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            api_key=os.getenv("GROQ_API_KEY"),
            temperature=0.3,
        )
    return _llm


# ── Markdown table formatter ───────────────────────────────────────────────────

def format_as_markdown_table(columns: list[str], rows: list[dict]) -> str:
    """Render rows as a markdown table."""
    if not rows:
        return "_Aucun résultat trouvé._"

    # Header
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"

    lines = [header, separator]
    for row in rows:
        cells = []
        for col in columns:
            val = row.get(col, "")
            # Truncate long text for readability
            s = str(val) if val is not None else ""
            cells.append(s[:80] + "…" if len(s) > 80 else s)
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


# ── LLM explanation generator ─────────────────────────────────────────────────

_EXPLAINER_SYSTEM = """\
You are a banking analyst assistant. Your job is to explain SQL query results
in clear, concise French for bank staff (agents and administrators).

Rules:
- Write 2-4 sentences maximum.
- Be direct and business-focused.
- Highlight key numbers, anomalies, or trends visible in the data.
- If the result is empty, say so clearly and suggest why.
- Do NOT mention SQL or technical details.
- Do NOT use bullet points — write in flowing prose.
- Respond ONLY in French.
"""


def generate_explanation(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[dict],
    truncated: bool,
) -> str:
    """
    Generate a business-level explanation of the query results.

    Returns a French prose explanation string.
    """
    row_count = len(rows)
    truncation_note = (
        f"\n⚠️ Note: Seules les {row_count} premières lignes sont affichées "
        f"(résultats limités à {row_count} lignes)."
        if truncated else ""
    )

    # Prepare a compact summary for the LLM (max 20 rows to avoid token bloat)
    sample = rows[:20]
    sample_json = json.dumps(sample, ensure_ascii=False, default=str)

    context = (
        f"Question de l'utilisateur : {question}\n\n"
        f"Nombre de résultats : {row_count}{' (tronqué)' if truncated else ''}\n"
        f"Colonnes : {', '.join(columns)}\n\n"
        f"Échantillon des données (max 20 lignes) :\n{sample_json}"
    )

    try:
        llm = _get_llm()
        response = llm.invoke([
            SystemMessage(content=_EXPLAINER_SYSTEM),
            HumanMessage(content=context),
        ])
        explanation = response.content.strip()
        if truncation_note:
            explanation += truncation_note
        return explanation
    except Exception as exc:
        logger.warning(f"[explainer] LLM explanation failed: {exc}")
        # Fallback: deterministic summary
        if row_count == 0:
            return "Aucun résultat trouvé pour cette requête." + truncation_note
        return (
            f"La requête a retourné **{row_count} résultat(s)**."
            + truncation_note
        )


# ── Main entry point ───────────────────────────────────────────────────────────

def build_response(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[dict],
    truncated: bool,
) -> dict:
    """
    Build the final response payload with:
      - markdown table of results
      - LLM business explanation
      - metadata
    """
    table_md    = format_as_markdown_table(columns, rows)
    explanation = generate_explanation(question, sql, columns, rows, truncated)

    # Compose a rich explanation that includes the table
    if rows:
        full_explanation = (
            f"{explanation}\n\n"
            f"**Résultats ({len(rows)} ligne{'s' if len(rows) > 1 else ''}) :**\n\n"
            f"{table_md}"
        )
    else:
        full_explanation = explanation

    return {
        "explanation":  full_explanation,
        "table":        table_md,
        "summary":      explanation,
        "row_count":    len(rows),
        "truncated":    truncated,
        "columns":      columns,
    }
