#!/bin/bash
export DJANGO_SETTINGS_MODULE=aleinfo.settings_local
python3 manage.py test
