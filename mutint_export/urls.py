from django.urls import re_path

from mutint_export import views

__author__ = 'dgosting'

# Mounted at ^export/ (mutint_common/urls.py). It used to be ^export with no slash,
# which forced this file to spell the separator itself as '^/experiment_index$' --
# a route beginning with a slash, which is what urls.W002 was reporting on every
# management command. The paths served are unchanged: /export/ and
# /export/experiment_index.
urlpatterns = [
    re_path(r'^$', views.export, name="export"),
    re_path(r'^experiment_index$', views.export_experiment_index,
            name="export_experiment_index"),
]
