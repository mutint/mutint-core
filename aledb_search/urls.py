from django.urls import include, re_path

from aledb_search import views

__author__ = 'dgosting'

urlpatterns = [
    re_path('^$', views.search, name="search"),
]
