from django.urls import re_path

from aledb_import import reference_views, upload_session, views

urlpatterns = [
    re_path(r'^$', views.gd_import_view, name='gd_import'),
    re_path(r'^gd/(?P<reseq_id>\d+)/export$', views.gd_export_view, name='gd_export'),

    # Step one of the two-step import: give an experiment its reference genome.
    re_path(r'^reference/$', reference_views.reference_upload_view, name='reference_upload'),

    # Chunked upload of breseq result folders. Split into three short requests so a
    # multi-GB drop never depends on a single long-lived POST.
    re_path(r'^uploads/$',
            upload_session.create_upload_session, name='upload_create'),
    re_path(r'^uploads/(?P<upload_id>[0-9a-fA-F-]{36})/chunk$',
            upload_session.upload_chunk, name='upload_chunk'),
    re_path(r'^uploads/(?P<upload_id>[0-9a-fA-F-]{36})/finalize$',
            upload_session.finalize_upload, name='upload_finalize'),
]
