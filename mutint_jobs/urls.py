from django.urls import re_path

from mutint_jobs import views

urlpatterns = [
    re_path(r'^$', views.jobs, name="jobs"),
    re_path(r'^list$', views.jobs_json, name="jobs_json"),
    re_path(r'^(?P<pk>\d+)/cancel$', views.job_cancel, name="job_cancel"),
]
