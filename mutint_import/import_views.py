"""The Import data page: one place to put anything into an experiment, one tab per way in.

It was the Import data page, with a dropdown of import types. The dropdown is a strip of tabs now,
from `mutint_common.import_tab_registry`: core registers one per type it ships, and a plugin
registers its own -- which may be a page of its own that wears the same strip, the way
mutint-breseq's Run breseq does. `?type=` names the tab; without one the first is shown.

The page is scoped to one experiment by primary key, and what you drop on a tab is
classified against that tab's type alone -- so a plugin's import type is reachable here, and
in auto-detect, without this module knowing it exists. It replaced the two-page flow
(`/import/reference/` then `/import/`) and the ordering rule that went with it.
"""

import logging

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from mutint_common.import_registry import get_import_types, get_import_types_for
from mutint_common.import_tab_registry import get_import_tabs
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment

logger = logging.getLogger("mutint_import.add_views")


@ensure_csrf_cookie
def import_view(request):
    """GET renders the drop page for `?experiment_id=<pk>`, on the tab `?type=` names.

    Always scoped to one experiment; there is no unscoped form of this page. Without a
    usable id it is a 404, and so is a type no tab offers.

    There is no POST here: uploads go through the chunked session endpoints, which is what
    lets a multi-GB drop work at all.
    """
    experiment_id = request.GET.get("experiment_id")
    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except (Experiment.DoesNotExist, ValueError, TypeError):
        # There used to be an explanatory page here, reached from an "Add data" sidebar
        # entry. Both are gone: the only way in is an experiment's own Import data button,
        # so arriving without a usable id now means a stale link, not a user who took a
        # wrong turn in the nav.
        raise Http404("No such experiment.")

    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", get_user_context(request.user), status=403)

    has_reference = _has_reference(experiment)

    tabs = get_import_tabs(experiment.id)
    type_tabs = [tab for tab in tabs if tab["import_type"]]
    wanted = request.GET.get("type") or (type_tabs[0]["import_type"] if type_tabs else None)
    active = next((tab for tab in type_tabs if tab["import_type"] == wanted), None)
    if active is None:
        raise Http404("No such import type.")
    import_type = next(t for t in get_import_types() if t["name"] == wanted)

    # Whether this tab can run now, and if not, why -- said on the tab rather than by
    # hiding it. The dropdown used to leave a type out; a tab you can see that tells you
    # what it is waiting for is the better way to say the same thing.
    offered_names = {t["name"] for t in get_import_types_for(has_reference)}
    offered = wanted in offered_names
    if offered:
        reason = ""
    elif import_type["only_without_reference"]:
        reason = ("This experiment already has a reference genome. To change its "
                  "annotation, use Replace Annotation; swapping in a different genome "
                  "is deliberately shell-only.")
    else:
        reason = ("This needs the experiment to have a reference genome first. Drop one "
                  "(GenBank, GFF3 or FASTA) on the Reference Sequence tab, or import a "
                  "breseq output folder, which brings its own.")

    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "experiment_id": experiment.id,
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        "active_tab": active["key"],
        "import_type": import_type,
        "offered": offered,
        "reason": reason,
        # The chosen type alone, scoped here rather than in the template so the page and
        # the JSON it classifies a drop with cannot disagree. `/import/types/` stays
        # unscoped -- it has no experiment to scope by, and the handlers enforce their
        # requirements anyway.
        "import_types": [import_type] if offered else [],
        # The unscoped registry as well, for one job only: naming the type a stray file in
        # the drop belongs to. That answer has to be able to name a type this experiment
        # cannot use yet -- "these look like GenomeDiff mutations, which needs a reference
        # first" is the whole point -- and the scoped list by construction cannot.
        "all_import_types": get_import_types(),
        "has_reference": has_reference,
    })
    return render(request, "import/import.html", context)


def import_types_view(request):
    """The registry, as JSON. Lets the page classify a drop before uploading anything."""
    return JsonResponse({"types": get_import_types()})


def _has_reference(experiment):
    from mutint_import.reference_store import has_reference

    return has_reference(experiment)
