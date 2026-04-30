"""
MailLogService — fraud-service/fraud/mail_log_service.py

Centralise TOUTE la logique de persistance des decision logs liés au mail.
Deux méthodes publiques :
  - create_pending()      → appelé par generate_summary() AVANT l'envoi
  - update_with_mail()    → appelé par PATCH /decision-logs/{id}/mail APRÈS l'envoi

Avantage : une seule place pour lire/écrire le log.
Le drawer Angular lit toujours la version à jour via GET /decision-logs/{id}.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import FraudDecisionLog

logger = logging.getLogger(__name__)


class MailLogService:

    def __init__(self, db: Session):
        self.db = db

    # ── Créer le log initial (mail pas encore envoyé) ────────────────────────

    def create_pending(
        self,
        *,
        iban:                   str,
        user_id:                Optional[str],
        session_id:             Optional[str],
        transactions_count:     int,
        date_range:             Optional[str],
        score_behavioral:       int,
        score_aml:              int,
        score_final:            int,
        risk_level:             str,
        tracfin_required:       bool,
        rules_triggered:        int,
        rules_evaluated:        int,
        triggered_rules_detail: list,
        report_path:            Optional[str],
        download_url:           Optional[str],
        llm_summary:            str,
        error:                  Optional[str],
    ) -> str:
        """
        INSERT un nouveau FraudDecisionLog avec mail_sent=False.
        Retourne l'UUID du log créé.

        Appelé dans generate_summary() du fraud-service AVANT que le mail
        soit envoyé par l'orchestrateur.
        """
        log_id = str(uuid.uuid4())
        log = FraudDecisionLog(
            id                     = log_id,
            created_at             = datetime.now(timezone.utc),
            user_id                = user_id,
            session_id             = session_id,
            iban                   = iban,
            transactions_count     = transactions_count,
            date_range             = date_range,
            score_behavioral       = score_behavioral,
            score_aml              = score_aml,
            score_final            = score_final,
            risk_level             = risk_level,
            tracfin_required       = tracfin_required,
            rules_triggered        = rules_triggered,
            rules_evaluated        = rules_evaluated,
            triggered_rules_detail = triggered_rules_detail,
            report_path            = report_path,
            download_url           = download_url,
            # ── Mail non encore envoyé ────────────────────────────────────────
            mail_sent              = False,
            mail_recipient         = None,
            mail_template          = None,
            mail_status            = None,
            mail_id                = None,
            # ── Résumé ───────────────────────────────────────────────────────
            llm_summary            = llm_summary,
            error                  = error,
        )
        try:
            self.db.add(log)
            self.db.commit()
            logger.info(f"[MailLogService] ✅ Log créé — id={log_id} IBAN={iban} score={score_final}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"[MailLogService] ❌ Échec création log: {e}")
            raise
        return log_id

    # ── Mettre à jour avec les infos mail après envoi ────────────────────────

    def update_with_mail(
        self,
        log_id:         str,
        mail_sent:      bool,
        mail_recipient: Optional[str],
        mail_template:  Optional[str],
        mail_status:    Optional[str],
        mail_id:        Optional[str],
    ) -> bool:
        """
        UPDATE les colonnes mail_* d'un log existant.
        Retourne True si le log a été trouvé et mis à jour, False sinon.

        Appelé via PATCH /decision-logs/{log_id}/mail par l'orchestrateur
        après que mail_agent() a envoyé (ou tenté d'envoyer) le mail.
        """
        log = self.db.query(FraudDecisionLog).filter(
            FraudDecisionLog.id == log_id
        ).first()

        if not log:
            logger.warning(f"[MailLogService] Log {log_id} introuvable pour update_with_mail")
            return False

        log.mail_sent      = mail_sent
        log.mail_recipient = mail_recipient
        log.mail_template  = mail_template
        log.mail_status    = mail_status
        log.mail_id        = mail_id

        try:
            self.db.commit()
            status_label = "✅ sent" if mail_sent else "❌ failed"
            logger.info(
                f"[MailLogService] {status_label} — "
                f"log={log_id} recipient={mail_recipient} template={mail_template}"
            )
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"[MailLogService] Échec update_with_mail pour log {log_id}: {e}")
            raise

    # ── Récupérer un log par ID (pour vérification) ──────────────────────────

    def get(self, log_id: str) -> Optional[FraudDecisionLog]:
        return self.db.query(FraudDecisionLog).filter(
            FraudDecisionLog.id == log_id
        ).first()