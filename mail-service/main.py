"""
Mail Service — microservice SMTP indépendant.
Reçoit des requêtes de l'orchestrateur, envoie des emails via SMTP.
Pas de logique métier : uniquement envoi + templates + pièces jointes.
"""

import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BankChat Mail Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Jinja2 templates ──────────────────────────────────────────────────────────
TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

# ── Config SMTP ───────────────────────────────────────────────────────────────
# IMPORTANT : SMTP_PASSWORD doit être un App Password Gmail (16 chars, sans espaces)
# Ne jamais mettre de commentaires sur la même ligne que la valeur dans .env
SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM",     SMTP_USER)


class MailRequest(BaseModel):
    to:              str
    subject:         str
    template:        str            # "fraud_alert" | "critical_alert" | "client_response" | "nightly_report"
    context:         dict = {}      # variables injectées dans le template Jinja2
    attachment_path: Optional[str] = None
    cc:              Optional[str] = None


def _send_email(
    to: str,
    subject: str,
    html_body: str,
    attachment_path: Optional[str] = None,
    cc: Optional[str] = None,
) -> None:
    """Envoie un email via SMTP avec TLS. Lève une exception en cas d'échec."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    if cc:
        msg["Cc"] = cc

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # ── Pièce jointe optionnelle ──────────────────────────────────────────────
    if attachment_path:
        path = Path(attachment_path)
        if path.is_file():
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            # NOTE : pas d'espace après "filename=" (bug dans la version originale)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={path.name}",
            )
            msg.attach(part)
        else:
            logger.warning(f"[mail] Attachment not found, skipping: {attachment_path}")

    recipients = [to] + ([cc] if cc else [])

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.sendmail(SMTP_FROM, recipients, msg.as_string())

    logger.info(f"[mail] Email sent → {to} | subject: {subject}")


@app.post("/send")
def send_mail(req: MailRequest):
    # ── Rendu du template Jinja2 ──────────────────────────────────────────────
    try:
        template  = jinja_env.get_template(f"{req.template}.html")
        html_body = template.render(**req.context)
    except TemplateNotFound:
        raise HTTPException(
            status_code=400,
            detail=f"Template '{req.template}.html' introuvable dans /templates."
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Template render error: {e}")

    # ── Envoi SMTP ────────────────────────────────────────────────────────────
    try:
        _send_email(
            to=req.to,
            subject=req.subject,
            html_body=html_body,
            attachment_path=req.attachment_path,
            cc=req.cc,
        )
        return {"status": "sent", "to": req.to, "subject": req.subject}
    except smtplib.SMTPAuthenticationError:
        logger.error("[mail] SMTP authentication failed — check SMTP_USER and SMTP_PASSWORD (App Password)")
        raise HTTPException(
            status_code=502,
            detail="SMTP authentication failed. Vérifiez SMTP_USER et SMTP_PASSWORD (App Password Gmail)."
        )
    except smtplib.SMTPException as e:
        logger.error(f"[mail] SMTP error: {e}")
        raise HTTPException(status_code=502, detail=f"SMTP error: {e}")
    except Exception as e:
        logger.exception("[mail] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {
        "status":    "ok",
        "service":   "mail-service",
        "smtp_host": SMTP_HOST,
        "smtp_user": SMTP_USER,
        # Ne jamais exposer le mot de passe, même masqué partiellement
    }