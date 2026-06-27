from django.urls import include, re_path

from aledb_metadata import views

__author__ = 'dgosting'

urlpatterns = [
    re_path('^$', views.metadata, name="metadata"),
]
