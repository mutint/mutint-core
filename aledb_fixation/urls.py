from django.urls import include, re_path

import aledb_fixation.views


urlpatterns = [
    re_path('^$', aledb_fixation.views.fixating_mutations, name='fixation')
]
