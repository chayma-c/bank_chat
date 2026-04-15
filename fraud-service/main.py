"""
BankChat Fraud Service — main FastAPI application.
"""

import re
import os
import io
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage


from fraud.database     import SessionLocal, Base, engine
from fraud.models       import FraudRuleModel        # noqa: F401
from fraud.crud         import seed_default_rules
from fraud.rules_router import router as rules_router
from fraud.graph        import run_fraud_agent
from fraud.db import init_db, get_settings, update_settings, get_report_blob, list_reports
from fraud.scheduler import scheduler_loop, run_global_analysis_task

app = FastAPI(title="BankChat Fraud Service", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    # 1. Initialize DB tables
    init_db()
    # 2. Start background scheduler loop
    asyncio.create_task(scheduler_loop())
    print("🚀 [fraud-service] Startup complete: DB initialized & Scheduler started.")

import asyncio # Needed for task creation
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified/created.")
    except Exception as e:
        logger.error(f"Could not create tables: {e}")

    try:
        db = SessionLocal()
        try:
            seed_default_rules(db)
            logger.info("Default fraud rules seeded.")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Could not seed default rules: {e}")

    yield
    logger.info("Fraud service shutting down.")


app = FastAPI(title="BankChat Fraud Service", version="2.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:4200,http://localhost"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# nginx strips /fraud/ prefix → FastAPI receives /rules/...
app.include_router(rules_router)


def _reports_dir() -> Path:
    d = Path(os.getenv("REPORTS_DIR", "/app/data/reports"))
    d.mkdir(parents=True, exist_ok=True)
    return d


IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)


def extract_iban_from_text(text: str) -> str:
    if not text:
        return ""
    match = IBAN_PATTERN.search(text)
    if match:
        return (match.group(1) or match.group(2) or "").replace(" ", "").upper()
    return ""


class FraudRequest(BaseModel):
    iban:       str = ""
    message:    str = ""
    action:     str = "fraud_check"
    user_id:    str = "anonymous"
    session_id: str = ""
    excel_path: str = ""

class SettingsRequest(BaseModel):
    frequency: str
    time: str
    dayOfWeek: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: FraudRequest):
    iban = req.iban or extract_iban_from_text(req.message)
    if req.message:
        user_content = req.message
    elif iban:
        user_content = f"Analyse les fraudes pour l'IBAN {iban}"
    else:
        return {"error": "IBAN requis.", "llm_summary": "❌ IBAN non fourni."}

    messages = [HumanMessage(content=user_content)]
    result = run_fraud_agent(
        messages=messages,
        user_id=req.user_id,
        session_id=req.session_id,
        excel_path=req.excel_path or "",
    )
    return {
        "iban":               result.get("iban", iban),
        "action":             result.get("action", req.action),
        "transactions_count": result.get("transactions_count", 0),
        "account_summary":    result.get("account_summary"),
        "score_behavioral":   result.get("score_behavioral", 0),
        "score_aml":          result.get("score_aml", 0),
        "score_final":        result.get("score_final", 0),
        "risk_level":         result.get("risk_level", ""),
        "tracfin_required":   result.get("tracfin_required", False),
        "fraud_results":      result.get("fraud_results", []),
        "report_path":        result.get("report_path", ""),
        "download_url":       result.get("download_url", ""),
        "llm_summary":        result.get("llm_summary", ""),
        "error":              result.get("error"),
        "sheet_url":          result.get("sheet_url", ""),
        "drive_url":          result.get("drive_url", ""),
        "output_errors":      result.get("output_errors", []),
    }


@app.get("/reports/{filename}")
async def download_report(filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    filepath = _reports_dir() / filename
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"Rapport non trouvé : {filename}")
    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/reports")
async def list_reports_endpoint():
    """Liste tous les rapports disponibles (FileSystem + DB)."""
async def list_reports():
    reports_dir = _reports_dir()
    files = sorted(reports_dir.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
    base  = os.getenv("FRAUD_SERVICE_PUBLIC_URL", "http://localhost:8001").rstrip("/")
    
    fs_reports = [
        {
            "id":           None,
            "source":       "file",
            "filename":     f.name,
            "download_url": f"{base}/reports/{f.name}",
            "size_kb":      round(f.stat().st_size / 1024, 1),
            "created_at":   datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for f in files
    ]

    db_reports = [
        {
            "id":           r["id"],
            "source":       "db",
            "filename":     r["filename"],
            "download_url": f"{base}/db-reports/{r['id']}",
            "size_kb":      "N/A",
            "created_at":   r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r["created_at"], datetime) else str(r["created_at"]),
        }
        for r in list_reports()
    ]

    return {
        "reports": fs_reports + db_reports,
        "total": len(fs_reports) + len(db_reports),
    }

# ── Settings Endpoints ──────────────────────────────────────────────────────

@app.get("/settings")
async def get_fraud_settings():
    settings = get_settings()
    if not settings:
        return {"frequency": "manual", "time": "02:00", "dayOfWeek": 1, "lastRun": None, "lastStatus": "ready"}
    
    # Simple status inference
    last_status = "success" if settings["last_run"] else "ready"
    
    return {
        "frequency": settings["frequency"],
        "time":      settings["scheduled_time"],
        "dayOfWeek": settings["day_of_week"],
        "lastRun":   settings["last_run"].isoformat() if settings["last_run"] else None,
        "lastAutoRun": settings["last_auto_run"].isoformat() if settings["last_auto_run"] else None,
        "lastStatus": last_status
    }

@app.post("/settings")
async def update_fraud_settings(req: SettingsRequest):
    update_settings(req.frequency, req.time, req.dayOfWeek)
    return {"status": "ok", "message": "Settings updated successfully"}

@app.post("/trigger")
async def trigger_full_analysis(background_tasks: BackgroundTasks):
    """Trigger a global audit manually in the background."""
    background_tasks.add_task(run_global_analysis_task)
    return {"status": "ok", "message": "Global analysis triggered in background."}

@app.get("/db-reports/{report_id}")
async def download_db_report(report_id: int):
    """Download a report directly from the database BLOB."""
    report = get_report_blob(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found in database.")
    
    return StreamingResponse(
        io.BytesIO(report["report_data"]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={report['filename']}"}
    )
    base = os.getenv("FRAUD_SERVICE_PUBLIC_URL", "http://localhost:8001").rstrip("/")
    return {
        "reports": [
            {
                "filename":     f.name,
                "download_url": f"{base}/reports/{f.name}",
                "size_kb":      round(f.stat().st_size / 1024, 1),
                "created_at":   datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
            for f in files
        ],
        "total": len(files),
    }


@app.get("/health")
def health():
    reports_dir = _reports_dir()
    return {
        "status":        "ok",
        "service":       "fraud-service",
        "version":       "2.0.0",
        "reports_dir":   str(reports_dir),
        "reports_count": len(list(reports_dir.glob("*.xlsx"))) if reports_dir.exists() else 0,
    }