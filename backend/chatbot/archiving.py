"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              BANK CHAT — SERVICE D'ARCHIVAGE POSTGRESQL                     ║
║                                                                              ║
║  Stratégie : Résumé consolidé → suppression des vieux messages              ║
║  Déclenchement : automatique (seuil msgs) + manuel + tâche nuit             ║
╚══════════════════════════════════════════════════════════════════════════════╝

POURQUOI CETTE APPROCHE :
─────────────────────────
On ne supprime PAS les messages sans les résumer d'abord.
On ne déplace PAS vers une autre table (ça ne réduit pas la taille).
On REMPLACE N messages par 1 résumé textuel dans la colonne Conversation.summary.

SEUILS CHOISIS :
─────────────────
ARCHIVE_THRESHOLD = 50 messages
  → En dessous de 50 msgs, garder tout (conversations courtes, contexte utile)
  → Au-delà, les messages anciens sont redondants avec le résumé

KEEP_RECENT = 12 messages
  → On garde les 12 derniers intacts (6 échanges = fenêtre glissante active)
  → Aligné avec RECENT_TURNS * 2 dans memory_manager.py

BATCH_SIZE = 100 conversations
  → Traiter 100 conversations par run pour éviter les timeouts
  → Chaque run = max ~5 minutes de travail
"""

import logging
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger(__name__)

# ── Seuils ────────────────────────────────────────────────────────────────────
ARCHIVE_THRESHOLD = 50 
KEEP_RECENT       = 12
#ARCHIVE_THRESHOLD = 50   # archiver si > N messages dans la conversation
#KEEP_RECENT       = 12   # garder les N derniers messages intacts
BATCH_SIZE        = 100  # conversations traitées par run


def _build_summary_text(existing_summary: str, messages_to_archive: list) -> str:
    """
    Génère le nouveau résumé consolidé en combinant :
    - Le résumé existant (si la conv a déjà été archivée)
    - Les nouveaux messages à archiver

    On utilise le LLM pour produire un résumé de qualité.
    Fallback textuel si le LLM échoue.
    """
    from .graph.nodes import get_llm

    conversation_text = ""
    for msg in messages_to_archive:
        prefix = "Client" if msg.role == "user" else "Assistant"
        # Tronquer chaque message à 500 chars pour le prompt de résumé
        content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
        conversation_text += f"{prefix}: {content}\n"

    if existing_summary:
        prompt = (
            "Tu es un assistant bancaire. Voici un résumé existant d'une conversation "
            "et de nouveaux échanges à intégrer.\n\n"
            f"RÉSUMÉ EXISTANT :\n{existing_summary}\n\n"
            f"NOUVEAUX ÉCHANGES À INTÉGRER :\n{conversation_text}\n\n"
            "Produis un résumé consolidé mis à jour en 4-6 phrases maximum. "
            "Garde les informations essentielles : demandes du client, problèmes, "
            "actions prises, informations clés (montants, IBANs, dates). "
            "Résumé consolidé :"
        )
    else:
        prompt = (
            "Tu es un assistant bancaire. Résume cette conversation en 4-6 phrases. "
            "Capture : les demandes du client, les problèmes évoqués, "
            "les informations clés (montants, IBANs, dates, noms), "
            "et les actions ou réponses importantes.\n\n"
            f"CONVERSATION :\n{conversation_text}\n\n"
            "Résumé :"
        )

    try:
        llm = get_llm()
        response = llm.invoke(prompt)
        summary = response.content.strip()
        # Limiter à 1000 chars max dans PG (raisonnable pour un résumé)
        return summary[:1000] + "..." if len(summary) > 1000 else summary
    except Exception as e:
        logger.warning(f"LLM summary failed, using fallback: {e}")
        # Fallback : résumé textuel basique
        count = len(messages_to_archive)
        topics = set()
        for msg in messages_to_archive:
            content_lower = msg.content.lower()
            if any(k in content_lower for k in ["virement", "transfer"]):
                topics.add("virement")
            if any(k in content_lower for k in ["carte", "card"]):
                topics.add("carte bancaire")
            if any(k in content_lower for k in ["fraude", "fraud"]):
                topics.add("fraude")
            if any(k in content_lower for k in ["solde", "balance"]):
                topics.add("solde")
        topic_str = ", ".join(topics) if topics else "questions bancaires"
        fallback = (
            f"{existing_summary}\n" if existing_summary else ""
        )
        fallback += f"[Archivage de {count} messages supplémentaires — sujets : {topic_str}]"
        return fallback[:1000]


def archive_conversation(conversation) -> dict:
    """
    Archive une conversation spécifique.

    Algorithme :
    1. Charger tous les messages triés par date
    2. Séparer : anciens (à archiver) et récents (à garder)
    3. Générer le résumé consolidé via LLM
    4. Sauvegarder le résumé dans Conversation.summary
    5. Supprimer les anciens messages de la table Message
    6. Invalider le cache Redis

    Returns:
        dict avec stats de l'opération
    """
    from .models import Message
    from .memory_manager import cache_invalidate

    all_messages = list(conversation.messages.order_by('created_at'))
    total = len(all_messages)

    if total <= ARCHIVE_THRESHOLD:
        return {"skipped": True, "reason": f"only {total} messages"}

    # Messages à archiver = tout sauf les KEEP_RECENT derniers
    to_archive = all_messages[:total - KEEP_RECENT]
    to_keep    = all_messages[total - KEEP_RECENT:]

    if not to_archive:
        return {"skipped": True, "reason": "nothing to archive"}

    archived_count = len(to_archive)

    # Générer le résumé consolidé
    new_summary = _build_summary_text(
        existing_summary=conversation.summary or "",
        messages_to_archive=to_archive,
    )

    # Transaction atomique : résumé sauvé + messages supprimés ensemble
    with transaction.atomic():
        # Mettre à jour la conversation avec le résumé
        conversation.summary        = new_summary
        conversation.archived_count = (conversation.archived_count or 0) + archived_count
        conversation.last_archived_at = timezone.now()
        conversation.save(update_fields=['summary', 'archived_count', 'last_archived_at'])

        # Supprimer les anciens messages
        ids_to_delete = [m.id for m in to_archive]
        Message.objects.filter(id__in=ids_to_delete).delete()

    # Invalider le cache Redis (le résumé Redis sera recalculé depuis PG + summary)
    cache_invalidate(str(conversation.session_id))

    logger.info(
        f"[Archive] Conv {str(conversation.session_id)[:8]}... "
        f"archived {archived_count} msgs, kept {len(to_keep)}"
    )

    return {
        "skipped":        False,
        "archived_count": archived_count,
        "kept_count":     len(to_keep),
        "summary_length": len(new_summary),
    }


def run_archiving_batch() -> dict:
    """
    Archive toutes les conversations qui dépassent le seuil.
    Appelé par la tâche planifiée (management command ou cron).

    Returns:
        dict avec stats globales du run
    """
    from .models import Conversation

    # Trouver les conversations avec trop de messages
    # On utilise une sous-requête pour éviter de charger tous les messages
    from django.db.models import Count

    candidates = (
        Conversation.objects
        .annotate(msg_count=Count('messages'))
        .filter(msg_count__gt=ARCHIVE_THRESHOLD)
        .order_by('created_at')  # priorité aux plus anciennes
        [:BATCH_SIZE]
    )

    total_archived = 0
    total_skipped  = 0
    errors         = 0

    for conv in candidates:
        try:
            result = archive_conversation(conv)
            if result.get("skipped"):
                total_skipped += 1
            else:
                total_archived += result.get("archived_count", 0)
        except Exception as e:
            errors += 1
            logger.error(f"[Archive] Error on conv {conv.session_id}: {e}")

    logger.info(
        f"[Archive] Run complete — "
        f"archived {total_archived} msgs, "
        f"skipped {total_skipped}, errors {errors}"
    )

    return {
        "archived_messages": total_archived,
        "skipped":           total_skipped,
        "errors":            errors,
    }
