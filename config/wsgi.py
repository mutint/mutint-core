"""WSGI entry point. `WSGI_APPLICATION` in config/defaults.py names this module."""
from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
