import re
import os
from fastapi import FastAPI
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from fraud.graph import run_fraud_agent

app = FastAPI(title="BankChat Fraud Service", version="1.0.0")

# ── IBAN extraction ───────────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)

def extract_iban_from_text(text: str) -> str:
    """Extrait le premier IBAN trouvé dans un texte libre."""
    if not text:
        return ""
    match = IBAN_PATTERN.search(text)
    if match:
        return (match.group(1) or match.group(2) or "").replace(" ", "").upper()
    return ""


# ── Schéma de requête ─────────────────────────────────────────────────────────

class FraudRequest(BaseModel):
    iban:       str = ""      # IBAN direct (prioritaire)
    message:    str = ""      # message brut (fallback pour extraire l'IBAN)
    action:     str = "fraud_check"
    user_id:    str = "anonymous"
    session_id: str = ""
    excel_path: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: FraudRequest):
    # Résoudre l'IBAN : direct ou extrait du message
    iban = req.iban or extract_iban_from_text(req.message)

    # Construire le message LangChain pour le fraud graph
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

    # Retourner un dict JSON-serializable
    # (FraudAgentState contient des objets non-serializable comme BaseMessage)
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
        "llm_summary":        result.get("llm_summary", ""),
        "error":              result.get("error"),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "fraud-service", "version": "1.0.0"}