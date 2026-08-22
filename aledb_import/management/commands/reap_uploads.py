"""Remove staging directories for upload sessions that were never finalized."""

from django.core.management.base import BaseCommand

from aledb_import.upload_session import reap_expired_sessions


class Command(BaseCommand):
    help = "Delete staged files for upload sessions older than ALEDB_UPLOAD_SESSION_TTL_HOURS."

    def handle(self, *args, **options):
        removed = reap_expired_sessions()
        self.stdout.write("Reaped %d expired upload session(s)." % removed)
