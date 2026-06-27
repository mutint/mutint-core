from django.urls import include, re_path

from aledb_filter.views import ale_exp_filter
from aledb_filter.views import global_filter

urlpatterns = [
    re_path(r'^$', ale_exp_filter.mutation_filter, name='filter'),
    re_path(r'^global_filter', global_filter.global_filter, name='global_filter')
]
