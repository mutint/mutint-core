from django.urls import re_path

from aledb_import import views

urlpatterns = [
    re_path(r'^$', views.gd_import_view, name='gd_import'),
    re_path(r'^gd/(?P<reseq_id>\d+)/export$', views.gd_export_view, name='gd_export'),
]
