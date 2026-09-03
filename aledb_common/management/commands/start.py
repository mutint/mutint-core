import os
import subprocess
import threading

from django.core.management.base import BaseCommand
from django.core.management import call_command

# Django's autoreloader does not swap code into the running process -- it
# re-executes the whole command line in a loop (see
# django.utils.autoreload.restart_with_reloader), respawning the child every time
# a watched file changes. So everything in handle() runs again on every reload.
#
# Ungated, that meant each touched file re-ran `migrate --run-syncdb` and popped a
# fresh browser window, which reads as the server rebooting rather than reloading.
# RUN_MAIN is set only in the reloader's child, so its absence marks the launch.
RELOADER_CHILD_ENV = 'RUN_MAIN'


def is_first_launch():
    return os.environ.get(RELOADER_CHILD_ENV) != 'true'


class Command(BaseCommand):
    help = 'Run migrations, create default admin user, and start the development server'

    def handle(self, *args, **options):
        url = 'http://127.0.0.1:8000'

        if is_first_launch():
            call_command('migrate', '--run-syncdb')

            from django.contrib.auth.models import User
            if not User.objects.filter(username='admin').exists():
                User.objects.create_superuser('admin', 'admin@example.com', 'admin')
                self.stdout.write('Created superuser: admin / admin')
            else:
                self.stdout.write('Superuser already exists')

            threading.Timer(1.0, lambda: subprocess.run(
                ['open', url], capture_output=True
            )).start()

            self.stdout.write(f'\nStarting ALEdb at {url}')
            self.stdout.write(f'  Admin interface: {url}/admin/  (login: admin / admin)')
            # Said here rather than left to be discovered, because the symptom of not knowing
            # is a sample that imports perfectly and quietly has no coverage track. No worker
            # is spawned: runserver re-executes this whole command line on every code change,
            # so a spawned one becomes an orphan-management problem, and a background worker
            # that dies silently is worse than one you can see.
            self.stdout.write(
                '  Coverage is derived in the background. Run `./aledb db_worker` in another\n'
                '  terminal, or `./aledb coverage` afterwards.\n')

        call_command('runserver')
