import re
import os
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from fraud.graph import run_fraud_agent

app = FastAPI(title="BankChat Fraud Service", version="1.0.0")


def _reports_dir() -> Path:
    d = Path(os.getenv("REPORTS_DIR", "/app/data/reports"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── IBAN extraction ───────────────────────────────────────────────────────────

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


# ── Schéma de requête ─────────────────────────────────────────────────────────

class FraudRequest(BaseModel):
    iban:       str = ""
    message:    str = ""
    action:     str = "fraud_check"
    user_id:    str = "anonymous"
    session_id: str = ""
    excel_path: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: FraudRequest):
    iban = req.iban or extract_iban_from_text(req.message)

    if req.message:
        user_content = req.message
    elif iban:
        user_content = f"Analyse les fraudes pour l'IBAN {iban}"
    else:
        return {
            "error": "IBAN requis. Fournissez un IBAN valide dans 'iban' ou dans 'message'.",
            "llm_summary": "❌ IBAN non fourni.",
        }

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
    """Télécharge un rapport Excel généré."""
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
async def list_reports():
    """Liste tous les rapports disponibles."""
    reports_dir = _reports_dir()
    files = sorted(reports_dir.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
    base  = os.getenv("FRAUD_SERVICE_PUBLIC_URL", "http://localhost:8001").rstrip("/")
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
        "version":       "1.0.0",
        "reports_dir":   str(reports_dir),
        "reports_count": len(list(reports_dir.glob("*.xlsx"))) if reports_dir.exists() else 0,
    }