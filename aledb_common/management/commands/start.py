import subprocess
import threading

from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = 'Run migrations, create default admin user, and start the development server'

    def handle(self, *args, **options):
        call_command('migrate', '--run-syncdb')

        from django.contrib.auth.models import User
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin')
            self.stdout.write('Created superuser: admin / admin')
        else:
            self.stdout.write('Superuser already exists')

        url = 'http://127.0.0.1:8000'
        threading.Timer(1.0, lambda: subprocess.run(
            ['open', url], capture_output=True
        )).start()

        self.stdout.write(f'\nStarting ALEdb at {url}')
        self.stdout.write(f'  Admin interface: {url}/admin/  (login: admin / admin)\n')
        call_command('runserver')
