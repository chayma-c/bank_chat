# backend/chatbot/management/commands/archive_messages.py
#
# Usage :
#   python manage.py archive_messages           # archivage batch complet
#   python manage.py archive_messages --session abc-123  # une seule conversation
#   python manage.py archive_messages --dry-run          # simulation sans modifier PG
#
# Cron (chaque nuit à 2h) :
#   0 2 * * * docker exec bank_chat_backend python manage.py archive_messages

from django.core.management.base import BaseCommand
from chatbot.archiving import run_archiving_batch, archive_conversation, ARCHIVE_THRESHOLD


class Command(BaseCommand):
    help = 'Archive les anciens messages et les remplace par un résumé consolidé'

    def add_arguments(self, parser):
        parser.add_argument(
            '--session',
            type=str,
            help='Archiver une seule conversation (session_id)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simuler sans modifier la base de données',
        )

    def handle(self, *args, **options):
        dry_run    = options['dry_run']
        session_id = options.get('session')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — aucune modification'))

        if session_id:
            # Archiver une seule conversation
            from chatbot.models import Conversation
            try:
                conv = Conversation.objects.get(session_id=session_id)
                msg_count = conv.messages.count()
                self.stdout.write(f'Conversation {session_id[:8]}... : {msg_count} messages')

                if msg_count <= ARCHIVE_THRESHOLD:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Seuil non atteint ({msg_count} <= {ARCHIVE_THRESHOLD}), skip'
                        )
                    )
                    return

                if not dry_run:
                    result = archive_conversation(conv)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Archivé : {result["archived_count"]} msgs supprimés, '
                            f'{result["kept_count"]} conservés, '
                            f'résumé {result["summary_length"]} chars'
                        )
                    )
                else:
                    self.stdout.write(f'[DRY] Archiverait {msg_count - 12} messages')

            except Conversation.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Session {session_id} introuvable'))

        else:
            # Archivage batch complet
            self.stdout.write('Démarrage de l\'archivage batch...')

            if not dry_run:
                result = run_archiving_batch()
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Terminé — {result["archived_messages"]} messages archivés, '
                        f'{result["skipped"]} conversations ignorées, '
                        f'{result["errors"]} erreurs'
                    )
                )
            else:
                from chatbot.models import Conversation
                from django.db.models import Count
                candidates = (
                    Conversation.objects
                    .annotate(msg_count=Count('messages'))
                    .filter(msg_count__gt=ARCHIVE_THRESHOLD)
                )
                total = sum(
                    max(0, c.msg_count - 12)
                    for c in candidates
                )
                self.stdout.write(
                    f'[DRY] {candidates.count()} conversations à traiter, '
                    f'~{total} messages à supprimer'
                )