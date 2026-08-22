"""The Add page: one place to put anything into an experiment.

Replaces the two-page flow (`/import/reference/` then `/import/`) and the ordering rule that
went with it. The page is scoped to one experiment by primary key, and what you drop is
classified by the import registry -- so a plugin's import type appears in the dropdown, and in
auto-detect, without this module knowing it exists.
"""

import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from aledb_common.import_registry import get_import_types
from aledb_common.util import get_user_context
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_edit_project

logger = logging.getLogger("aledb_import.add_views")


@ensure_csrf_cookie
def add_view(request):
    """GET renders the drop page for `?ale_experiment_id=<pk>`.

    There is no POST here: uploads go through the chunked session endpoints, which is what
    lets a multi-GB drop work at all.
    """
    experiment_id = request.GET.get("ale_experiment_id")
    try:
        experiment = AleExperiment.objects.get(pk=experiment_id)
    except (AleExperiment.DoesNotExist, ValueError, TypeError):
        return render(request, "import/add_no_experiment.html",
                      get_user_context(request.user), status=404)

    if not can_edit_project(request.user, experiment.project):
        return render(request, "403.html", get_user_context(request.user), status=403)

    has_reference = _has_reference(experiment)

    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        # Filtered here rather than in the template so the dropdown and the JSON the page
        # classifies a drop with cannot disagree. `/import/types/` stays unfiltered -- it has
        # no experiment to scope by, and the handler enforces the requirement anyway.
        "import_types": [t for t in get_import_types()
                         if has_reference or not t["requires_reference"]],
        "has_reference": has_reference,
    })
    return render(request, "import/add.html", context)


def import_types_view(request):
    """The registry, as JSON. Lets the page classify a drop before uploading anything."""
    return JsonResponse({"types": get_import_types()})


def _has_reference(experiment):
    from aledb_import.reference_store import has_reference

    return has_reference(experiment)
