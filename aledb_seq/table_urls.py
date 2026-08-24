"""Routes for the shared mutation-table actions.

Separate from `aledb_seq.urls` because that module is mounted at `^mutations/`, which is the
per-sample table's prefix. These are posted to by every page that renders the shared table --
Compare (in aledb-compare), Fixed Mutations, Converged Mutations and Search -- so naming them
after one of those pages is what the old `/mutations/toggle-mut-tag/` did wrong.
"""

from django.urls import re_path

import aledb_seq.views.table_actions


urlpatterns = [
    re_path(r'^add_to_exp_filter$', aledb_seq.views.table_actions.add_to_exp_filter,
            name='mutation_to_exp_filter'),
    re_path(r'^toggle-mut-tag/$', aledb_seq.views.table_actions.save_mut_tag,
            name='toggle_mut_tag'),
    re_path(r'^toggle-rep-tag$', aledb_seq.views.table_actions.save_rep_tag,
            name='toggle_rep_tag'),
]
