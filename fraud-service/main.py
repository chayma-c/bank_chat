"""
BankChat Fraud Service — main FastAPI application.
"""

import re
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from fraud.database     import SessionLocal, Base, engine
from fraud.models       import FraudRuleModel ,FraudDecisionLog         # noqa: F401
from fraud.crud         import seed_default_rules
from fraud.rule_router import router as rule_router
from fraud.graph        import run_fraud_agent
from typing import Optional
import asyncio
from fraud.db import init_db, get_settings, update_settings
from fraud.scheduler import scheduler_loop, run_global_analysis_task
from fraud.loader import seed_transactions_from_csv
from fraud.auth import require_bank_agent
from sqlalchemy import desc
from fraud.mail_log_service import MailLogService

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

    try:
        init_db()
        
        # Seed transactions from CSV into DB if not already done
        db_seed = SessionLocal()
        try:
            seed_transactions_from_csv(db_seed)
        finally:
            db_seed.close()

        asyncio.create_task(scheduler_loop())
        logger.info("Fraud scheduler loop started.")
    except Exception as e:
        logger.error(f"Could not start scheduler or seed data: {e}")

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
app.include_router(rule_router)

# ── Settings & Manual Trigger ───────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    frequency: str
    time: str
    dayOfWeek: int

@app.get("/settings")
async def get_fraud_settings():
    s = get_settings()
    if not s:
        return {"frequency": "manual", "time": "02:00", "dayOfWeek": 1}
    # Mapping UI fields (time, dayOfWeek) to DB fields (scheduled_time, day_of_week)
    return {
        "frequency": s.get("frequency", "manual"),
        "time":      s.get("scheduled_time", "02:00"),
        "dayOfWeek": s.get("day_of_week", 1),
        "lastRun":   s.get("last_run"),
        "lastAutoRun": s.get("last_auto_run")
    }

@app.post("/settings")
async def save_fraud_settings(
    data: SettingsUpdate,
    _user: dict = Depends(require_bank_agent),):
    try:
        update_settings(data.frequency, data.time, data.dayOfWeek)
        return {"status": "success", "message": "Settings updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/trigger")
async def trigger_fraud_analysis(_user: dict = Depends(require_bank_agent)):
    # background task
    asyncio.create_task(run_global_analysis_task())
    return {"status": "triggered", "message": "Global analysis started in background"}



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

class MailUpdatePayload(BaseModel):
    mail_sent:      bool
    mail_recipient: Optional[str] = None
    mail_template:  Optional[str] = None
    mail_status:    Optional[str] = None
    mail_id:        Optional[str] = None


@app.post("/analyze")
async def analyze(
    req: FraudRequest,
    _user: dict = Depends(require_bank_agent)):
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
        "decision_log_id":    result.get("decision_log_id", ""),
    }


from fraud.auth import require_bank_agent, verify_report_signature
from fastapi.security import HTTPBearer

_bearer_optional = HTTPBearer(auto_error=False)

@app.get("/reports/{filename}")
async def download_report(
    filename: str,
    signature: str | None = None,
    creds = Depends(_bearer_optional)
):
    """
    Download a report. 
    Supports two auth modes:
    1. Bearer Token (standard API access)
    2. Signed URL (for browser clicks, using 'expires' and 'signature' params)
    """
    is_authed = False
    
    # Mode 1: Bearer Token
    if creds:
        try:
            await require_bank_agent(creds)
            is_authed = True
        except HTTPException:
            pass
            
    # Mode 2: HMAC Signature fallback (signature now tied to filename only)
    if not is_authed:
        if not signature:
            raise HTTPException(
                status_code=401,
                detail="Authentication required: Provide a Bearer token or a valid signed URL."
            )

        if not verify_report_signature(filename, signature):
            raise HTTPException(
                status_code=403,
                detail="Access denied: Invalid signature."
            )

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
async def list_reports(_user: dict = Depends(require_bank_agent)):
    reports_dir = _reports_dir()
    files = sorted(reports_dir.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
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

# ── Decision Logs endpoints ──────────────────────────────────────────────────

@app.get("/decision-logs")
async def list_decision_logs(
    limit:      int = 50,
    offset:     int = 0,
    iban:       str = None,
    risk_level: str = None,
    mail_sent:  bool = None,
    user_id:    str = None,
    _user: dict = Depends(require_bank_agent)
   ):    
    """
    Liste paginée des analyses avec filtres.
    Angular l'appelle via GET /fraud/decision-logs
    """
    db = SessionLocal()
    try:
        q = db.query(FraudDecisionLog)
        if iban:       q = q.filter(FraudDecisionLog.iban.contains(iban.upper()))
        if risk_level: q = q.filter(FraudDecisionLog.risk_level == risk_level)
        if mail_sent is not None: q = q.filter(FraudDecisionLog.mail_sent == mail_sent)
        if user_id:    q = q.filter(FraudDecisionLog.user_id == user_id)

        total = q.count()
        logs  = q.order_by(desc(FraudDecisionLog.created_at)).offset(offset).limit(limit).all()

        return {
            "total":  total,
            "limit":  limit,
            "offset": offset,
            "logs":   [l.to_dict() for l in logs],
        }
    finally:
        db.close()


@app.get("/decision-logs/stats")
async def decision_log_stats(_user: dict = Depends(require_bank_agent)):
    """Statistiques pour les metric cards du dashboard."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        total     = db.query(func.count(FraudDecisionLog.id)).scalar()
        critical  = db.query(func.count(FraudDecisionLog.id)).filter(
                        FraudDecisionLog.risk_level == "BLOCK").scalar()
        tracfin   = db.query(func.count(FraudDecisionLog.id)).filter(
                        FraudDecisionLog.tracfin_required == True).scalar()
        mailed    = db.query(func.count(FraudDecisionLog.id)).filter(
                        FraudDecisionLog.mail_sent == True).scalar()
        avg_score = db.query(func.avg(FraudDecisionLog.score_final)).scalar()
        return {
            "total_analyses": total     or 0,
            "block_count":    critical  or 0,
            "tracfin_count":  tracfin   or 0,
            "mailed_count":   mailed    or 0,
            "avg_score":      round(float(avg_score), 1) if avg_score else 0.0,
        }
    finally:
        db.close()


@app.get("/decision-logs/{log_id}")
async def get_decision_log(log_id: str, _user: dict = Depends(require_bank_agent)):
    """Détail complet d'un log — pour le drawer/modal dans l'UI."""
    db = SessionLocal()
    try:
        log = db.query(FraudDecisionLog).filter(FraudDecisionLog.id == log_id).first()
        if not log:
            raise HTTPException(404, detail=f"Log {log_id} not found")
        return log.to_dict()
    finally:
        db.close()

@app.post("/decision-logs/{log_id}")
async def update_log_mail(log_id: str, data: MailUpdatePayload, _user: dict = Depends(require_bank_agent)):
    """
    Appelé par mail_agent (orchestrateur Django) après envoi du mail.
    Utilise MailLogService.update_with_mail() pour garantir l'atomicité.
    """
    db = SessionLocal()
    try:
        svc = MailLogService(db)
        updated = svc.update_with_mail(
            log_id         = log_id,
            mail_sent      = data.mail_sent,
            mail_recipient = data.mail_recipient,
            mail_template  = data.mail_template,
            mail_status    = data.mail_status,
            mail_id        = data.mail_id,
        )
        if not updated:
            raise HTTPException(404, detail=f"Log {log_id} not found")
        return {"status": "updated", "log_id": log_id, "mail_sent": data.mail_sent}
    finally:
        db.close()
 
@app.patch("/decision-logs/{log_id}/mail")
async def update_log_mail_endpoint(
    log_id: str,
    data: MailUpdatePayload,
    _user: dict = Depends(require_bank_agent)
):
    return await update_log_mail(log_id, data)

@app.get("/health")
def health():
    return {
        "status":        "ok",
        "service":       "fraud-service",
        "version":       "2.0.0",
    }
