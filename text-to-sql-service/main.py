"""
BankChat Text-to-SQL Service — Main FastAPI Application

Pipeline:
  POST /query
    1. comprend l'intention (NL → SQL via LLM)
    2. génère la requête SQL
    3. valide la sécurité (SELECT-only, tables whitelistées)
    4. exécute la requête (PostgreSQL banking_data)
    5. retourne le résultat sous forme lisible + explication métier

Sécurité:
  - JWT Keycloak requis (rôles bank_agent ou admin uniquement)
  - Les clients n'ont pas accès
  - Seules les requêtes SELECT sont exécutées
  - Tables système bloquées
"""

import os
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sql_agent.auth      import require_bank_agent
from sql_agent.generator import generate_sql
from sql_agent.validator import validate_sql
from sql_agent.executor  import execute_query
from sql_agent.explainer import build_response
from sql_agent.schema    import get_schema_dict

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Text-to-SQL Service starting up...")
    db_url = os.getenv("DATABASE_URL", "NOT SET")
    logger.info(f"   DATABASE_URL: {db_url[:40]}..." if len(db_url) > 40 else f"   DATABASE_URL: {db_url}")
    logger.info(f"   LLM_PROVIDER: {os.getenv('LLM_PROVIDER', 'groq')}")
    logger.info(f"   GROQ_MODEL:   {os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')}")
    yield
    logger.info("Text-to-SQL Service shutting down.")


# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="BankChat Text-to-SQL Service",
    description=(
        "Converts natural language banking questions into SQL queries, "
        "executes them on the banking_data database, and returns "
        "business-readable results. Restricted to bank staff only."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:4200,http://localhost",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic Models ───────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Natural language question in French or English",
        examples=["Quelles sont les transactions frauduleuses ce mois-ci ?"],
    )
    user_id: str = Field(
        default="anonymous",
        description="Identifier of the requesting user",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of rows to return",
    )


class QueryResponse(BaseModel):
    question:    str
    sql:         str
    explanation: str          # Full markdown: prose + table
    summary:     str          # Prose only (for chat bubble)
    table:       str          # Markdown table only
    columns:     list[str]
    rows:        list[dict]
    row_count:   int
    truncated:   bool
    user_id:     str
    duration_ms: float
    status:      str          # "success" | "error"
    error:       str | None


class ValidationErrorResponse(BaseModel):
    status:   str
    question: str
    sql:      str
    error:    str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post(
    "/query",
    response_model=QueryResponse,
    summary="Natural Language to SQL Query",
    description=(
        "Main endpoint: converts a natural language question into SQL, "
        "validates it for security, executes it, and returns formatted results. "
        "Requires bank_agent or admin JWT token."
    ),
)
async def query_endpoint(
    req: QueryRequest,
    user: dict = Depends(require_bank_agent),
) -> QueryResponse:
    start = time.perf_counter()
    user_sub = user.get("sub", req.user_id)
    logger.info(
        f"[/query] user={user_sub!r} question={req.question!r}"
    )

    # ── Step 1 : Generate SQL from natural language ───────────────────────────
    gen = generate_sql(req.question)
    if gen["error"]:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.error(f"[/query] Generation failed: {gen['error']}")
        return QueryResponse(
            question=req.question, sql="", explanation=gen["error"],
            summary=gen["error"], table="", columns=[], rows=[],
            row_count=0, truncated=False, user_id=user_sub,
            duration_ms=round(duration_ms, 1), status="error",
            error=gen["error"],
        )

    sql = gen["sql"]
    logger.info(f"[/query] Generated SQL: {sql[:150]}")

    # ── Step 2 : Security validation ─────────────────────────────────────────
    validation = validate_sql(sql)
    if not validation.is_valid:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            f"[/query] Validation rejected — {validation.error}"
        )
        error_msg = (
            f"⛔ Requête SQL rejetée par le validateur de sécurité.\n\n"
            f"**Raison :** {validation.error}\n\n"
            f"**SQL généré :**\n```sql\n{sql}\n```"
        )
        return QueryResponse(
            question=req.question, sql=sql, explanation=error_msg,
            summary=validation.error, table="", columns=[], rows=[],
            row_count=0, truncated=False, user_id=user_sub,
            duration_ms=round(duration_ms, 1), status="error",
            error=validation.error,
        )

    clean_sql = validation.cleaned_sql

    # ── Step 3 : Execute query ────────────────────────────────────────────────
    exec_result = execute_query(clean_sql)
    if exec_result["error"]:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.error(f"[/query] Execution error: {exec_result['error']}")
        error_msg = (
            f"❌ Erreur lors de l'exécution de la requête.\n\n"
            f"**Détail :** {exec_result['error']}\n\n"
            f"**SQL exécuté :**\n```sql\n{clean_sql}\n```"
        )
        return QueryResponse(
            question=req.question, sql=clean_sql, explanation=error_msg,
            summary=exec_result["error"], table="", columns=[], rows=[],
            row_count=0, truncated=False, user_id=user_sub,
            duration_ms=round(duration_ms, 1), status="error",
            error=exec_result["error"],
        )

    # ── Step 4 : Format & explain results ────────────────────────────────────
    formatted = build_response(
        question  = req.question,
        sql       = clean_sql,
        columns   = exec_result["columns"],
        rows      = exec_result["rows"],
        truncated = exec_result["truncated"],
    )

    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        f"[/query] ✅ Done — {exec_result['row_count']} rows "
        f"in {duration_ms:.0f}ms (user={user_sub!r})"
    )

    return QueryResponse(
        question    = req.question,
        sql         = clean_sql,
        explanation = formatted["explanation"],
        summary     = formatted["summary"],
        table       = formatted["table"],
        columns     = formatted["columns"],
        rows        = exec_result["rows"],
        row_count   = exec_result["row_count"],
        truncated   = exec_result["truncated"],
        user_id     = user_sub,
        duration_ms = round(duration_ms, 1),
        status      = "success",
        error       = None,
    )


@app.get(
    "/schema",
    summary="Available Database Schema",
    description="Returns the list of whitelisted tables and their columns. Requires bank_agent or admin role.",
)
async def schema_endpoint(
    _user: dict = Depends(require_bank_agent),
) -> dict:
    """Return the accessible banking schema for UI display or LLM context."""
    return get_schema_dict()


@app.get(
    "/health",
    summary="Service Health Check",
    description="Public health check — does NOT require authentication.",
)
def health() -> dict:
    return {
        "status":   "ok",
        "service":  "text-to-sql-service",
        "version":  "1.0.0",
        "llm":      os.getenv("LLM_PROVIDER", "groq"),
        "database": "banking_data",
    }


@app.get(
    "/examples",
    summary="Example Questions",
    description="Returns example NL questions for each use case. Requires bank_agent or admin role.",
)
async def examples_endpoint(
    _user: dict = Depends(require_bank_agent),
) -> dict:
    """Return pre-built example questions to guide users."""
    return {
        "examples": [
            {
                "category": "🚨 Fraude",
                "questions": [
                    "Quelles sont les transactions frauduleuses ce mois-ci ?",
                    "Quelles transactions ont un score de fraude supérieur à 0.8 ?",
                    "Quel est le montant total des transactions frauduleuses par type ?",
                    "Combien de transactions suspectes ont eu lieu cette semaine ?",
                ],
            },
            {
                "category": "🔍 Anomalies",
                "questions": [
                    "Quelles transactions dépassent 10 000 EUR ?",
                    "Quels marchands apparaissent le plus souvent dans les transactions bloquées ?",
                    "Y a-t-il des transactions vers des catégories à risque (casino, crypto) ?",
                ],
            },
            {
                "category": "👤 Support Client",
                "questions": [
                    "Combien de transactions ont le statut 'failed' aujourd'hui ?",
                    "Quel est l'historique des transactions de l'utilisateur USER123 ?",
                ],
            },
            {
                "category": "📊 Reporting",
                "questions": [
                    "Quel est le score de fraude moyen par type de transaction ?",
                    "Combien d'analyses ont été faites cette semaine ?",
                    "Combien de décisions BLOCK ont été prises ce mois-ci ?",
                ],
            },
            {
                "category": "📋 Audit",
                "questions": [
                    "Quelles règles de fraude de type AML sont actives ?",
                    "Combien de signalements TRACFIN ont été générés ?",
                    "Quelles analyses ont déclenché l'envoi d'un email d'alerte ?",
                ],
            },
            {
                "category": "📈 Analyse métier",
                "questions": [
                    "Quelle est la tendance des scores de fraude sur les 30 derniers jours ?",
                    "Quelle est la répartition des niveaux de risque (APPROVED/REVIEW/HOLD/BLOCK) ?",
                    "Quels sont les 5 IBANs avec les scores de risque les plus élevés ?",
                ],
            },
        ]
    }
