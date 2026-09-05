from django.urls import re_path

from mutint_import import add_views, staging, upload_session, views

urlpatterns = [
    re_path(r'^gd/(?P<sample_id>\d+)/export$', views.gd_export_view, name='gd_export'),
    re_path(r'^vcf/(?P<sample_id>\d+)/export$', views.vcf_export_view, name='vcf_export'),

    # One place to add anything to an experiment; what you drop is classified by the
    # import registry, so a plugin's type is reachable here without touching this file.
    re_path(r'^add/$', add_views.add_view, name='add'),
    re_path(r'^types/$', add_views.import_types_view, name='import_types'),

    # Chunked upload of breseq result folders. Split into three short requests so a
    # multi-GB drop never depends on a single long-lived POST.
    re_path(r'^uploads/$',
            upload_session.create_upload_session, name='upload_create'),
    # The same staging area opened for a component instead of for the import registry. The
    # chunk endpoint below serves both; there is deliberately no `staging/<id>/finalize`,
    # because what happens next is exactly what core does not know. See mutint_import/staging.py.
    re_path(r'^staging/$',
            staging.create_staging_session, name='staging_create'),
    re_path(r'^uploads/(?P<upload_id>[0-9a-fA-F-]{36})/chunk$',
            upload_session.upload_chunk, name='upload_chunk'),
    re_path(r'^uploads/(?P<upload_id>[0-9a-fA-F-]{36})/finalize$',
            upload_session.finalize_upload, name='upload_finalize'),
    # Polled while finalize runs. A GET, and the only one of these that is: it reads a
    # snapshot the finalize request is writing as it goes.
    re_path(r'^uploads/(?P<upload_id>[0-9a-fA-F-]{36})/progress$',
            upload_session.upload_progress, name='upload_progress'),
    # Abandoning a staged drop -- a declined rename, most often -- rather than leaving it
    # for the TTL reaper.
    re_path(r'^uploads/(?P<upload_id>[0-9a-fA-F-]{36})/cancel$',
            upload_session.cancel_upload, name='upload_cancel'),
]
