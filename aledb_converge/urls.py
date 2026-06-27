from django.urls import include, re_path

import aledb_converge.views


urlpatterns = [
    re_path('^$', aledb_converge.views.converge_mutations, name="converge")
]
