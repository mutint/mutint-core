"""Build an ancestor from the mutations the samples share, when none was sequenced.

An experiment whose ancestor was never sequenced still has one: the mutations present in
every population and every sample are the starting line, not evolution, and leaving them in
place puts them at the top of every convergence table. This page finds them, lets the editor
tick which to keep, and makes a sample of them that it then designates through the same
column the picker writes (`Experiment.ancestor`).

**It reads raw calls, deliberately.** `calls_for_samples` and `get_evolved_call_queryset`
subtract the current ancestor's mutations, which are exactly the ones this page exists to
find -- an experiment whose ancestor is being *replaced* would be offered only what the old
one missed. The ancestor *sample* is still left out (`get_ordered_sample_queryset`'s
default), so it neither counts as a population nor inflates the denominator. This is one of
the stated exceptions to the subtraction rule in CLAUDE.md.

**The rule is decided in the browser**, as Compare decides its sets: the page carries every
(mutation, sample) presence pair and the script applies the two sliders, so moving one
recounts without a round trip. A mutation is shared when present in samples of at least N
distinct populations *and* in at least F% of the samples; both defaults are "all", because
"in every population and every sample" has no false positives and the sliders only relax it.
A looser default would quietly put evolved mutations into the ancestor, which is the
invisible kind of wrong.

**Creating is a copy.** The new sample is filed under `Unspecified` with no time point, the
coordinate the autonumbered import path uses for a sample with no place, and labelled by
the typed name through `description`, which is what `Sample.label` prefers. Its calls are
copied from one existing call per mutation at frequency 1 -- an ancestor is the clone
everything started from, so a mutation in it is fixed -- through
`mutint_curate.history.apply_edits` as a `KIND_COPY` edit set, so it is in the Curate history
and restorable like any other copy. Then the sample is designated and everything derived is
rebuilt once, the pair `experiment_ancestor_apply` runs.
"""

import json
import logging
from collections import defaultdict

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from mutint_common.logger import user_extra
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment, Population
from mutint_experiment.permissions import (
    ExperimentLocked, can_edit_experiment, can_view_project, experiment_lock_refusal,
)
from mutint_sample.breseq_report import describe_mutation
from mutint_sample.models import Mutation, MutationCall, Sample
from mutint_sample.util import get_ordered_sample_queryset

logger = logging.getLogger(__name__)

#: What the name box opens with.
DEFAULT_NAME = "Shared ancestor"


def shared_mutation_payload(experiment):
    """Every mutation any non-ancestor sample carries, with the samples carrying it.

    `samples` is in the order the Samples page lists them, each with its population so the
    script can count populations; `mutations` is one descriptive row per mutation (breseq's
    markup where annotated, as the matrix renders it) plus `sample_ids`. The two counts are
    the sliders' maxima.
    """
    samples = list(get_ordered_sample_queryset(experiment.id))
    sample_ids = [sample.id for sample in samples]

    carriers = defaultdict(set)
    pairs = (MutationCall.objects
             .filter(sample_id__in=sample_ids, present=True)
             .values_list("mutation_id", "sample_id"))
    for mutation_id, sample_id in pairs:
        carriers[mutation_id].add(sample_id)

    mutations = (Mutation.objects.filter(pk__in=carriers)
                 .order_by("seq_id", "start_position", "pk"))
    rows = []
    for mutation in mutations:
        row = describe_mutation(mutation)
        row.update({
            "id": mutation.id,
            "type": mutation.mutation_type or "",
            "seq_id_text": mutation.seq_id or "",
            "position_sort": mutation.start_position,
            "sample_ids": sorted(carriers[mutation.id]),
        })
        rows.append(row)

    return {
        "samples": [{"id": sample.id, "label": sample.label,
                     "population_id": sample.population_id,
                     "population": sample.population_name}
                    for sample in samples],
        "mutations": rows,
        "population_count": len({sample.population_id for sample in samples}),
        "sample_count": len(samples),
    }


def experiment_ancestor_shared(request, pk):
    """The page: two sliders, the mutations meeting them, a name box and Create ancestor.

    Rendered for anyone who can view the experiment, as the picker is; the controls that
    write render for an editor, and the apply endpoint refuses regardless.
    """
    experiment = get_object_or_404(Experiment, pk=pk)
    context = get_user_context(request.user)
    if not can_view_project(request.user, experiment.project):
        return render(request, "403.html", context, status=403)

    payload = shared_mutation_payload(experiment)
    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "payload": payload,
        "population_count": payload["population_count"],
        "sample_count": payload["sample_count"],
        "default_name": DEFAULT_NAME,
        "can_edit": can_edit_experiment(request.user, experiment),
        "lock_refusal": experiment_lock_refusal(experiment),
        "title": "%s shared ancestor" % experiment.name,
        "template_header": "Create ancestor from shared mutations",
    })
    return render(request, "experiment/ancestor_shared.html", context)


class _Refusal(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _mutation_ids(request):
    """The ticked mutation ids, as one JSON array string -- `mutintPost` stringifies every
    value, the convention `mutint_curate`'s endpoints use."""
    raw = request.POST.get("mutation_ids") or "[]"
    try:
        values = json.loads(raw)
    except ValueError:
        raise _Refusal("Malformed request: mutation_ids was not valid JSON.")
    if not isinstance(values, list):
        raise _Refusal("Malformed request: mutation_ids was not a list.")
    try:
        ids = sorted({int(value) for value in values})
    except (TypeError, ValueError):
        raise _Refusal("Malformed request: a mutation id was not a number.")
    if not ids:
        raise _Refusal("Select at least one mutation.")
    return ids


def _sources(experiment, mutation_ids):
    """One existing call per mutation to copy from: the lowest pk carrying it, among the
    experiment's non-ancestor samples. Refuses if any id has none -- an id from another
    experiment, or a mutation nothing carries any more."""
    from mutint_curate import history
    sample_ids = list(get_ordered_sample_queryset(experiment.id).values_list("id", flat=True))
    calls = (history.calls_for(experiment, sample_ids=sample_ids)
             .filter(mutation_id__in=mutation_ids, present=True)
             .order_by("mutation_id", "pk"))
    chosen = {}
    for call in calls:
        chosen.setdefault(call.mutation_id, call)
    missing = [mutation_id for mutation_id in mutation_ids if mutation_id not in chosen]
    if missing:
        raise _Refusal("Those mutations are not in this experiment's samples.", status=404)
    return [chosen[mutation_id] for mutation_id in mutation_ids]


def _create_ancestor_sample(experiment, name):
    """A clonal sample under `Unspecified` with no time point, labelled by `name`."""
    from mutint_import.gd_import import UNSPECIFIED_POPULATION, _next_sample_number
    population, _ = Population.objects.get_or_create(
        experiment=experiment, name=UNSPECIFIED_POPULATION)
    return Sample.objects.create(
        population=population, time_point=None,
        name=_next_sample_number(population, None),
        description=name[:300], source_name=name[:200], is_clonal=True)


@require_POST
def experiment_ancestor_shared_apply(request, pk):
    """Create the sample, copy the ticked mutations onto it, designate it. JSON.

    POST `name` and `mutation_ids` (a JSON array string). Write access and not while locked,
    the picker's 403; 400 for an empty name or no mutations; 404 for an id no sample carries.
    """
    experiment = get_object_or_404(Experiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse(
            {"error": (experiment_lock_refusal(experiment)
                       or "You do not have permission to change this experiment.")},
            status=403)

    from mutint_curate import history
    from mutint_curate.models import KIND_COPY
    from mutint_curate.views import _plan_copy

    try:
        name = (request.POST.get("name") or "").strip()
        if not name:
            raise _Refusal("Name the ancestor sample.")
        mutation_ids = _mutation_ids(request)
        sources = _sources(experiment, mutation_ids)

        # One transaction, so a refusal from `apply_edits` -- the lock check it repeats --
        # cannot leave a sample with no calls behind.
        with transaction.atomic():
            sample = _create_ancestor_sample(experiment, name)
            additions, _skipped = _plan_copy(experiment, sources, {sample.id: sample},
                                             frequency=1.0)
            edit_set = history.apply_edits(
                experiment, request.user, KIND_COPY, additions=additions,
                note="Created ancestor %s from %d shared mutation(s)." % (name, len(sources)))
            experiment.set_ancestor(sample, request.user)
    except _Refusal as refusal:
        return JsonResponse({"error": refusal.message}, status=refusal.status)
    except ExperimentLocked as locked:
        return JsonResponse({"error": str(locked)}, status=403)

    # The picker's pair: marked and run at experiment scope, site scope left marked for the
    # dashboard's own `ensure_fresh`. Outside the transaction, as every rebuild here is.
    from mutint_common.rebuild_registry import EXPERIMENT_SCOPE, request_rebuild, run_rebuilds
    request_rebuild(experiment.id, reason="ancestor created from shared mutations")
    run_rebuilds(experiment.id, scope=EXPERIMENT_SCOPE)

    logger.info("ancestor created from shared mutations", extra=user_extra(request))
    return JsonResponse({
        "experiment_id": experiment.id,
        "sample_id": sample.id,
        "added": len(additions),
        "edit_set_id": edit_set.pk if edit_set else None,
    })
