#!/usr/bin/env bash
set -e

VENV=.venv

if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

echo "Installing dependencies..."
pip install -q -r requirements-local.txt

export DJANGO_SETTINGS_MODULE=aleinfo.settings_local

echo "Running migrations..."
python manage.py migrate --run-syncdb


# Create default superuser on first run
python manage.py shell -c "
from django.contrib.auth.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin')
    print('Created superuser: admin / admin')
else:
    print('Superuser already exists')
" 2>/dev/null || true

echo ""
echo "Starting ALEdb at http://127.0.0.1:8000"
echo "  Admin interface: http://127.0.0.1:8000/admin/  (login: admin / admin)"
echo ""

# Open browser (macOS: open, Linux: xdg-open)
(sleep 1 && (open "http://127.0.0.1:8000" 2>/dev/null || xdg-open "http://127.0.0.1:8000" 2>/dev/null)) &

python manage.py runserver
