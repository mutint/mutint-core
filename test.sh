#!/bin/bash
export DJANGO_SETTINGS_MODULE=config.settings_local
python3 manage.py test
