from django.urls import re_path

from aledb_import import add_views, reference_views, upload_session, views

urlpatterns = [
    re_path(r'^$', views.gd_import_view, name='gd_import'),
    re_path(r'^gd/(?P<reseq_id>\d+)/export$', views.gd_export_view, name='gd_export'),

    # One place to add anything to an experiment; what you drop is classified by the
    # import registry, so a plugin's type is reachable here without touching this file.
    re_path(r'^add/$', add_views.add_view, name='add'),
    re_path(r'^types/$', add_views.import_types_view, name='import_types'),

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
