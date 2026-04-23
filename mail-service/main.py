"""
Mail Service — microservice SMTP indépendant.
Envoie des emails et trace chaque envoi dans PostgreSQL.
"""

import os
import uuid
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BankChat Mail Service", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Jinja2 ────────────────────────────────────────────────────────────────────
TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

# ── Config SMTP ───────────────────────────────────────────────────────────────
SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM",     SMTP_USER)

# ── Config PostgreSQL ─────────────────────────────────────────────────────────
DB_URL = os.getenv(
    "MAIL_DATABASE_URL",
    "postgresql://mail_user:mail_password@db:5432/mail_db"
)


# ── Connexion BD ──────────────────────────────────────────────────────────────

def get_db_conn():
    """Retourne une connexion psycopg2. Lève une exception si indisponible."""
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _log_email(
    recipient:      str,
    subject:        str,
    template_type:  str,
    status:         str,
    cc:             str | None = None,
    iban:           str | None = None,
    score_final:    int | None = None,
    risk_level:     str | None = None,
    tracfin:        bool = False,
    has_attachment: bool = False,
    error_detail:   str | None = None,
    session_id:     str | None = None,
    user_id:        str | None = None,
) -> str:
    """
    Insère un enregistrement dans sent_emails.
    Retourne l'UUID généré. Ne lève jamais d'exception (non bloquant).
    """
    record_id = str(uuid.uuid4())
    try:
        conn = get_db_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO sent_emails (
                id, sent_at, recipient, cc, subject, template_type,
                iban, score_final, risk_level, tracfin,
                has_attachment, status, error_detail, session_id, user_id
            ) VALUES (
                %s, NOW(), %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
        """, (
            record_id, recipient, cc, subject, template_type,
            iban, score_final, risk_level, tracfin,
            has_attachment, status, error_detail, session_id, user_id,
        ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[mail-db] Logged email {record_id} — {status}")
    except Exception as e:
        logger.warning(f"[mail-db] Failed to log email (non-blocking): {e}")
    return record_id


# ── Schémas Pydantic ──────────────────────────────────────────────────────────

class MailRequest(BaseModel):
    to:              str
    subject:         str
    template:        str
    context:         dict = {}
    attachment_path: Optional[str] = None
    cc:              Optional[str] = None
    # Métadonnées optionnelles pour la traçabilité
    iban:            Optional[str] = None
    score_final:     Optional[int] = None
    risk_level:      Optional[str] = None
    tracfin:         Optional[bool] = False
    session_id:      Optional[str] = None
    user_id:         Optional[str] = None


# ── Envoi SMTP ────────────────────────────────────────────────────────────────

def _send_email(
    to: str, subject: str, html_body: str,
    attachment_path: Optional[str] = None,
    cc: Optional[str] = None,
) -> bool:
    """Envoie l'email. Retourne True si succès, lève une exception sinon."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    if cc:
        msg["Cc"] = cc

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    has_attachment = False
    if attachment_path:
        path = Path(attachment_path)
        if path.is_file():
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={path.name}")
            msg.attach(part)
            has_attachment = True
        else:
            logger.warning(f"[mail] Attachment not found: {attachment_path}")

    recipients = [to] + ([cc] if cc else [])
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.sendmail(SMTP_FROM, recipients, msg.as_string())

    return has_attachment


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/send")
def send_mail(req: MailRequest):
    # Rendu du template
    try:
        template  = jinja_env.get_template(f"{req.template}.html")
        html_body = template.render(**req.context)
    except TemplateNotFound:
        raise HTTPException(400, detail=f"Template '{req.template}.html' introuvable.")
    except Exception as e:
        raise HTTPException(400, detail=f"Template render error: {e}")

    # Envoi SMTP + log BD
    try:
        has_attachment = _send_email(
            to=req.to, subject=req.subject, html_body=html_body,
            attachment_path=req.attachment_path, cc=req.cc,
        )
        # ── Succès → log "sent" ───────────────────────────────────────────────
        record_id = _log_email(
            recipient=req.to,       subject=req.subject,
            template_type=req.template, status="sent",
            cc=req.cc,              iban=req.iban,
            score_final=req.score_final, risk_level=req.risk_level,
            tracfin=req.tracfin or False,
            has_attachment=has_attachment,
            session_id=req.session_id,  user_id=req.user_id,
        )
        logger.info(f"[mail] Sent → {req.to} | id={record_id}")
        return {"status": "sent", "id": record_id, "to": req.to, "subject": req.subject}

    except smtplib.SMTPAuthenticationError:
        err = "SMTP authentication failed — vérifiez SMTP_USER et SMTP_PASSWORD (App Password Gmail)"
        _log_email(
            recipient=req.to, subject=req.subject,
            template_type=req.template, status="failed",
            iban=req.iban, score_final=req.score_final,
            error_detail=err, session_id=req.session_id, user_id=req.user_id,
        )
        raise HTTPException(502, detail=err)

    except Exception as e:
        err = str(e)
        _log_email(
            recipient=req.to, subject=req.subject,
            template_type=req.template, status="failed",
            iban=req.iban, score_final=req.score_final,
            error_detail=err, session_id=req.session_id, user_id=req.user_id,
        )
        logger.exception("[mail] SMTP error")
        raise HTTPException(500, detail=err)


@app.get("/history")
def get_history(
    limit:         int = Query(50,  ge=1, le=500),
    offset:        int = Query(0,   ge=0),
    template_type: str = Query(None),
    status:        str = Query(None),
    iban:          str = Query(None),
):
    """
    Retourne l'historique des emails envoyés.
    Filtres optionnels : template_type, status, iban.
    """
    try:
        conn = get_db_conn()
        cur  = conn.cursor()

        where_clauses = []
        params        = []
        if template_type:
            where_clauses.append("template_type = %s")
            params.append(template_type)
        if status:
            where_clauses.append("status = %s")
            params.append(status)
        if iban:
            where_clauses.append("iban = %s")
            params.append(iban)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params += [limit, offset]

        cur.execute(f"""
            SELECT id, sent_at, recipient, subject, template_type,
                   iban, score_final, risk_level, tracfin,
                   has_attachment, status, error_detail, session_id, user_id
            FROM sent_emails
            {where_sql}
            ORDER BY sent_at DESC
            LIMIT %s OFFSET %s
        """, params)

        rows = cur.fetchall()

        # Compter le total pour la pagination
        cur.execute(f"SELECT COUNT(*) as total FROM sent_emails {where_sql}", params[:-2])
        total = cur.fetchone()["total"]

        cur.close()
        conn.close()

        return {
            "total":  total,
            "limit":  limit,
            "offset": offset,
            "emails": [dict(r) for r in rows],
        }
    except Exception as e:
        logger.exception("[mail-db] History query error")
        raise HTTPException(500, detail=str(e))


@app.get("/stats")
def get_stats():
    """
    Statistiques globales pour le dashboard .
    Retourne les comptages par template, par statut, les alertes récentes.
    """
    try:
        conn = get_db_conn()
        cur  = conn.cursor()

        cur.execute("""
            SELECT
                COUNT(*)                                          AS total_sent,
                COUNT(*) FILTER (WHERE status = 'sent')          AS success_count,
                COUNT(*) FILTER (WHERE status = 'failed')        AS failed_count,
                COUNT(*) FILTER (WHERE template_type = 'critical_alert') AS critical_count,
                COUNT(*) FILTER (WHERE template_type = 'fraud_alert')    AS fraud_count,
                COUNT(*) FILTER (WHERE template_type = 'client_response') AS client_count,
                COUNT(*) FILTER (WHERE template_type = 'nightly_report') AS nightly_count,
                COUNT(*) FILTER (WHERE tracfin = TRUE)           AS tracfin_count,
                MAX(sent_at)                                      AS last_email_at
            FROM sent_emails
        """)
        stats = dict(cur.fetchone())

        # 5 dernières alertes critiques
        cur.execute("""
            SELECT sent_at, recipient, subject, iban, score_final, risk_level, status
            FROM sent_emails
            WHERE template_type IN ('critical_alert', 'fraud_alert')
            ORDER BY sent_at DESC
            LIMIT 5
        """)
        stats["recent_alerts"] = [dict(r) for r in cur.fetchall()]

        cur.close()
        conn.close()
        return stats

    except Exception as e:
        logger.exception("[mail-db] Stats query error")
        raise HTTPException(500, detail=str(e))


@app.get("/health")
def health():
    # Tester aussi la connexion BD
    db_ok = False
    try:
        conn  = get_db_conn()
        cur   = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        pass

    return {
        "status":    "ok",
        "service":   "mail-service",
        "version":   "2.0.0",
        "smtp_host": SMTP_HOST,
        "smtp_user": SMTP_USER,
        "db_ok":     db_ok,
    }