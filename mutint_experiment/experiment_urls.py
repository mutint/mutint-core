"""Routes for an experiment, mounted at ``/experiment/``.

**One prefix where there were two.** The old `/ale/` urlconf carried a plural
`^experiments` beside a singular `^experiment/<pk>/`, and the plural had no `$` -- so it was
a *prefix match* that swallowed anything beginning with those characters. Four routes had to
be declared above it to escape, and `/experiment/<pk>/samples/` was spelled singular
purely to get out of its way.

Every pattern here is anchored, which is what makes that ordering irrelevant: `new/` and
`create/` cannot be read as a `[0-9]+` pk, and nothing can be read as a prefix of anything
else. A route added below is a route, not a trap.
"""

from django.urls import re_path

from mutint_experiment.sample_views import (
    experiment_samples, experiment_samples_metadata, experiment_samples_read_metadata,
    experiment_samples_selected, experiment_samples_update,
)
from mutint_experiment.storage_views import experiment_storage_clear
from mutint_experiment.views import (
    experiment_ancestor, experiment_ancestor_apply, experiment_create, experiment_delete,
    experiment_detail, experiment_edit, experiment_lock, experiment_new,
    experiment_treatment_rename, experiment_update, experiments,
)

urlpatterns = [
    re_path(r'^$', experiments, name="experiments_view"),
    re_path(r'^new/$', experiment_new, name="experiment_new"),
    re_path(r'^create/$', experiment_create, name="experiment_create"),

    re_path(r'^(?P<pk>[0-9]+)/$', experiment_detail, name="experiment_detail"),
    re_path(r'^(?P<pk>[0-9]+)/edit/$', experiment_edit, name="experiment_edit"),
    re_path(r'^(?P<pk>[0-9]+)/update/$', experiment_update, name="experiment_update"),
    re_path(r'^(?P<pk>[0-9]+)/delete/$', experiment_delete, name="experiment_delete"),

    # An experiment's samples, listed and saved in bulk. Under the experiment because that
    # is whose page it is; one sample's own routes are in `sample_urls`.
    re_path(r'^(?P<pk>[0-9]+)/samples/$', experiment_samples, name="experiment_samples"),
    re_path(r'^(?P<pk>[0-9]+)/samples/update/$',
            experiment_samples_update, name="experiment_samples_update"),
    # The same samples as a metadata.csv -- the import's own format -- and that file read
    # back into the table. Reading writes nothing; the page's Save does.
    re_path(r'^(?P<pk>[0-9]+)/samples/metadata\.csv$',
            experiment_samples_metadata, name="experiment_samples_metadata"),
    re_path(r'^(?P<pk>[0-9]+)/samples/metadata/$',
            experiment_samples_read_metadata, name="experiment_samples_read_metadata"),
    # The sidebar's Samples entry. Experiment-section links carry the experiment as
    # `?experiment_id=`, and this page names it in the path, so this redirects there.
    re_path(r'^samples/$', experiment_samples_selected, name="experiment_samples_selected"),

    # Renaming one of the experiment's treatments across every sample carrying it. On the
    # experiment's edit page, since that is where the list of them is maintained; the label
    # itself is a column on the sample. See mutint_experiment/treatments.py.
    re_path(r'^(?P<pk>[0-9]+)/treatments/rename/$',
            experiment_treatment_rename, name="experiment_treatment_rename"),

    # Locking. Its own endpoint rather than a field on the edit form: a locked experiment
    # refuses `experiment_update` outright, so a checkbox there could lock and never unlock.
    re_path(r'^(?P<pk>[0-9]+)/lock/$', experiment_lock, name="experiment_lock"),

    # Clearing one kind of stored data -- the alignments, the report -- for every sample.
    # Gated on `can_edit_experiment`, so a locked experiment refuses. See storage_views.
    re_path(r'^(?P<pk>[0-9]+)/storage/clear/$',
            experiment_storage_clear, name="experiment_storage_clear"),

    # The designated ancestor: the sample this experiment started from, whose mutations are
    # subtracted from every other sample. A page and an apply endpoint, the pairing
    # `mutint_curate` established -- the page checks permission itself and the write
    # checks it again, because the button being hidden is not a permission check.
    re_path(r'^(?P<pk>[0-9]+)/ancestor/$', experiment_ancestor, name="experiment_ancestor"),
    re_path(r'^(?P<pk>[0-9]+)/ancestor/apply/$',
            experiment_ancestor_apply, name="experiment_ancestor_apply"),
]
