import os
import sys


def manage():
    """Entry point — equivalent to manage.py with settings_local pre-set."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aleinfo.settings_local')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
