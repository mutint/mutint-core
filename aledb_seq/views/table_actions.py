"""Curation actions the mutation table performs: tagging.

These are **not** Compare's, which is why they stayed in core when Compare moved out to
`aledb-compare`. Every page that renders `base_table_template.html` or includes
`table_template.js` posts here -- Compare, Fixed Mutations, Converged Mutations and Search --
and the state they write is shared: the sample's `tags` is what the Show/Hide Tag
control filters sample columns on in `aledb_seq.util.get_reseq_ordered_dict`, so tagging a
replicate from one table changes what the other three show.

They are mounted at `/mutation-table/` rather than `/mutations/`, where they used to live
beside the Compare view. The prefix named a page that is no longer here, and three of the
four callers were never that page.

`add_to_exp_filter` used to sit here too, appending a mutation id to
`AleExperimentFilter.ignored_mutations` so the row would stop appearing. That was a way of
deleting a mutation while keeping it, per experiment and unattributed, and it is
`aledb_mutation_editor`'s job now -- per sample, recorded, and restorable.

`@ajax` puts the real status in the JSON body and always sends HTTP 200 (see
`aledb_common.ajax`), so a refusal returns HttpResponseForbidden and the caller reads
`content` for the reason -- which the existing `swal()` handler already does.
"""

import logging

from django.http import HttpResponseForbidden

from aledb_common.ajax import ajax

from aledb_experiment import permissions
from aledb_seq.models import Mutation, Sample

logger = logging.getLogger(__name__)

_REFUSED = "You do not have permission to curate this experiment."


def _may_curate(user, experiment):
    """Who may tag a mutation or a replicate.

    Deliberately the same predicate `mutation_table_builder` already uses to decide whether
    to render the tag dropdowns at all (see its `get_table_header` / `get_mutation_table_body`).
    The controls were gated from the start; the endpoints behind them were not, so anyone who
    could reach the URL could tag anything by primary key -- and with `LoginRequiredMiddleware`
    only present in `settings_private.py`, that included anonymous callers. This enforces
    server-side what the templates already assumed.

    This used to read `can_add_global_filter(user) or can_add_experiment_filter(...)`, whose
    left half was `user.is_superuser` -- so the `or` short-circuited and a superuser went on
    tagging a locked experiment without the right half, where the lock lives, ever being
    reached. The lock check below was written to sit *before* the disjunction for that reason.

    `permissions.can_curate` asks it once now, including the reason the disjunction existed:
    a mutation with no experiment has no project to grant against and stays superuser-only.
    The explicit lock check stays because this is a write path, and reading a refusal out of
    two delegations is not the same as stating it.
    """
    if experiment is not None and experiment.is_locked:
        return False
    return permissions.can_curate(user, experiment)


def _toggle(existing, selected_tag):
    """Add the tag, or remove it if it is already there.

    Tags are a comma-joined string rather than a relation, so this is the whole storage
    format. A tag containing a comma would corrupt the field; the vocabulary is the fixed
    dict in `aledb_common.constants`, which contains none.
    """
    if not existing:
        return selected_tag
    tag_list = existing.split(',')
    if selected_tag in tag_list:
        tag_list.remove(selected_tag)
    else:
        tag_list.append(selected_tag)
    return ','.join(tag_list)


@ajax
def save_mut_tag(request):
    mut_id = request.POST['mut_id']
    selected_tag = request.POST.get('tag_name')
    mutation = Mutation.objects.get(id=mut_id)
    # A mutation with no experiment cannot be scoped to a project, so only a superuser
    # passes -- can_add_experiment_filter answers False for a null experiment.
    if not _may_curate(request.user, mutation.experiment):
        return HttpResponseForbidden(_REFUSED)
    mutation.tags = _toggle(mutation.tags, selected_tag)
    mutation.save()
    return 'ok'


@ajax
def save_rep_tag(request):
    rep_id = request.POST.get('rep_id')
    selected_tag = request.POST.get('tag_name')
    replicate = Sample.objects.get(id=rep_id)
    experiment = replicate.experiment
    if not _may_curate(request.user, experiment):
        return HttpResponseForbidden(_REFUSED)
    replicate.tags = _toggle(replicate.tags, selected_tag)
    replicate.save()
    return 'ok'
