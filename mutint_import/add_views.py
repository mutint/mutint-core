"""The Add page: one place to put anything into an experiment.

Replaces the two-page flow (`/import/reference/` then `/import/`) and the ordering rule that
went with it. The page is scoped to one experiment by primary key, and what you drop is
classified by the import registry -- so a plugin's import type appears in the dropdown, and in
auto-detect, without this module knowing it exists.
"""

import logging

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from mutint_common.import_registry import get_import_types, get_import_types_for
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment

logger = logging.getLogger("mutint_import.add_views")


@ensure_csrf_cookie
def add_view(request):
    """GET renders the drop page for `?experiment_id=<pk>`.

    Always scoped to one experiment; there is no unscoped form of this page. Without a
    usable id it is a 404.

    There is no POST here: uploads go through the chunked session endpoints, which is what
    lets a multi-GB drop work at all.
    """
    experiment_id = request.GET.get("experiment_id")
    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except (Experiment.DoesNotExist, ValueError, TypeError):
        # There used to be an explanatory page here, reached from an "Add data" sidebar
        # entry. Both are gone: the only way in is an experiment's own Add data button, so
        # arriving without a usable id now means a stale link, not a user who took a wrong
        # turn in the nav.
        raise Http404("No such experiment.")

    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", get_user_context(request.user), status=403)

    has_reference = _has_reference(experiment)

    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        # Scoped here rather than in the template so the dropdown and the JSON the page
        # classifies a drop with cannot disagree. `/import/types/` stays unscoped -- it has
        # no experiment to scope by, and the handlers enforce their requirements anyway.
        "import_types": get_import_types_for(has_reference),
        # The unscoped registry as well, for one job only: naming the type a stray file in
        # the drop belongs to. That answer has to be able to name a type this experiment
        # cannot use yet -- "these look like GenomeDiff mutations, which needs a reference
        # first" is the whole point -- and the scoped list by construction cannot.
        "all_import_types": get_import_types(),
        "has_reference": has_reference,
    })
    return render(request, "import/add.html", context)


def import_types_view(request):
    """The registry, as JSON. Lets the page classify a drop before uploading anything."""
    return JsonResponse({"types": get_import_types()})


def _has_reference(experiment):
    from mutint_import.reference_store import has_reference

    return has_reference(experiment)
