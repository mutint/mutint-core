"""Routes for the shared mutation-table actions.

Separate from `mutint_sample.urls` because that module is mounted at `^mutations/`, which is the
per-sample table's prefix. These are posted to by every page that renders the shared table --
Compare (in mutint-compare), Fixed Mutations, Converged Mutations and Search -- so naming them
after one of those pages is what the old `/mutations/toggle-mut-tag/` did wrong.
"""

from django.urls import re_path

import mutint_sample.views.table_actions


urlpatterns = [
    re_path(r'^toggle-mut-tag/$', mutint_sample.views.table_actions.save_mut_tag,
            name='toggle_mut_tag'),
    re_path(r'^toggle-rep-tag$', mutint_sample.views.table_actions.save_rep_tag,
            name='toggle_rep_tag'),
]
