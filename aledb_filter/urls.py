from django.urls import include, re_path

from aledb_filter.views import ale_exp_filter

urlpatterns = [
    re_path(r'^$', ale_exp_filter.mutation_filter, name='filter'),
]
