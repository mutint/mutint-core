from django.urls import re_path

from mutint_filter.views import filter_set

urlpatterns = [
    re_path(r'^set/$', filter_set, name='filter_set'),
]
