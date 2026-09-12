"""Clearing stored data, and the Storage panel that offers it.

Two endpoints and one panel context. The endpoints are `@require_POST` JSON views that
check permission themselves, the shape every write under `/experiment/` and `/project/`
takes; the panel is registered on the Overview through `panel_registry` from
`mutint_experiment.apps`, which makes core its own first user of that seam.

**Clearing asks `can_edit_experiment`, never `can_edit_project`.** Removing a sample's BAM
is a write everybody sees, so a locked experiment refuses it -- and a predicate handed the
project cannot see the lock. The project-level endpoint asks per experiment for the same
reason, and is **partial by design**, as `project_access_bulk` is: a locked experiment in
the project is skipped and named, the rest are cleared. A whole-project refusal would make
one lock hold every sibling's disk hostage.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from mutint_common.rebuild_registry import is_stale
from mutint_common.storage_registry import (
    STORAGE_REBUILD, NotClearable, UnknownStorageKind, bytes_by_kind, clear_kind,
    ensure_measured, get_storage_kind, stale_experiment_ids, usage_for,
)
from mutint_experiment.models import Experiment, Project, live
from mutint_experiment.permissions import (
    can_edit_experiment, can_edit_project, experiment_lock_refusal,
)


def _kind_or_refusal(key):
    """The kind, or a `JsonResponse` refusing it -- checked before anything is touched."""
    try:
        kind = get_storage_kind(key)
    except UnknownStorageKind:
        return None, JsonResponse({"error": "Unknown kind of stored data: %r." % key},
                                  status=400)
    if kind['clear'] is None:
        return None, JsonResponse(
            {"error": "%s is measured but cannot be cleared here." % kind['label']},
            status=409)
    return kind, None


def _json_usage(usage):
    return [dict(u, measured_at=u['measured_at'].isoformat() if u['measured_at'] else None)
            for u in usage]


@require_POST
def experiment_storage_clear(request, pk):
    experiment = get_object_or_404(Experiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(experiment)
                      or "You cannot change this experiment."}, status=403)
    key = request.POST.get("kind", "")
    kind, refusal = _kind_or_refusal(key)
    if refusal is not None:
        return refusal
    try:
        freed = clear_kind(experiment, key)
    except NotClearable:  # pragma: no cover -- refused above; a race with a reload
        return JsonResponse({"error": "%s cannot be cleared." % kind['label']}, status=409)
    return JsonResponse({"experiment_id": experiment.id, "kind": key,
                         "freed_bytes": freed, "usage": _json_usage(usage_for(experiment))})


@require_POST
def project_storage_clear(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit_project(request.user, project):
        return JsonResponse({"error": "You cannot change this project."}, status=403)
    key = request.POST.get("kind", "")
    kind, refusal = _kind_or_refusal(key)
    if refusal is not None:
        return refusal
    cleared, skipped, freed = [], [], 0
    for experiment in live(project.experiment_set.all()).order_by("id"):
        # Per experiment, not per project: the lock lives on the experiment and only the
        # experiment can answer for it. Sequential and partial -- see the module docstring.
        if not can_edit_experiment(request.user, experiment):
            skipped.append(experiment.name)
            continue
        freed += clear_kind(experiment, key)
        cleared.append(experiment.name)
    return JsonResponse({"project_id": project.id, "kind": key, "cleared": cleared,
                         "skipped": skipped, "freed_bytes": freed})


def storage_panel_context(experiment, request):
    """Context for the Storage panel on the Overview.

    The reader closes the gap: `ensure_measured` rebuilds a stale row before it is shown,
    and cannot raise. `storage_stale` is therefore true only when that rebuild *failed*, and
    the panel says so rather than showing a 0 as though it were measured.
    """
    ensure_measured(experiment.id)
    rows = usage_for(experiment)
    return {
        "experiment": experiment,
        "storage_rows": rows,
        "storage_total": sum(row["bytes"] for row in rows),
        "storage_can_edit": can_edit_experiment(request.user, experiment),
        "storage_stale": is_stale(STORAGE_REBUILD, experiment.id),
    }


def project_storage_context(experiments):
    """What `project_detail` adds: sizes across the project's live experiments.

    Deliberately no `ensure_measured` loop here. The Overview and the dashboard are the
    readers that pay for a measurement; a project page walking every experiment's report
    tree on the first view after an import is the cost this table exists to avoid. What it
    does say is how many experiments the numbers do not yet cover.
    """
    ids = list(experiments.values_list("id", flat=True))
    kinds = bytes_by_kind(experiments)
    return {
        "storage_kinds": [(key, label, size, get_storage_kind(key)['clear'] is not None)
                          for key, label, size in kinds],
        "storage_total": sum(size for _, _, size in kinds),
        "storage_unmeasured": len(stale_experiment_ids(ids)),
    }
