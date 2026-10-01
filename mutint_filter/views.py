"""`/filter/set`: change the reader's filter without reloading the page.

Compare applies the reader's filter in the browser, so its boxes take effect as they are
typed in rather than behind an Apply button. The filter is still **one filter, and the
reader's** -- the per-sample Mutations page, the export and every other page read it from the
session -- so each change is written back here, and the page applies what comes back: the
value as `ViewFilter.parse` normalized it, which is the only parse there is. A value it
refuses is a 400 with the sentence, and nothing is stored.

The same endpoint flips `ancestral_shown`, the other per-reader choice the matrix page
honours, for the same reason.

Nothing is gated beyond the session: what is written is the caller's own display preference
for one experiment id, it carries no authority, and nothing about the experiment is returned.
The query-string forms (`?min_freq=`, `?ancestral=show`) have always been writable by anyone
who can load a page.
"""

import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from mutint_experiment.ancestor import set_ancestral_shown
from mutint_filter.view_filter import ViewFilter, get_view_filter, set_view_filter


def filter_json(view_filter):
    """What the browser keeps: the normalized filter, cutoffs as whole percentages."""
    return {"min_freq": view_filter.min_freq, "max_freq": view_filter.max_freq,
            "genes": list(view_filter.genes)}


@require_POST
def filter_set(request):
    try:
        body = json.loads(request.body or b"{}")
        experiment_id = int(body.get("experiment_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Send {\"experiment_id\": ..., ...} as JSON."}, status=400)

    if "filter" in body:
        given = body["filter"] or {}
        if not isinstance(given, dict):
            return JsonResponse({"error": "`filter` must be an object."}, status=400)
        try:
            view_filter = ViewFilter.parse(min_freq=given.get("min_freq"),
                                           max_freq=given.get("max_freq"),
                                           genes=given.get("genes"))
        except ValueError as bad:
            return JsonResponse({"error": str(bad)}, status=400)
        set_view_filter(request, experiment_id, view_filter)
    if "ancestral" in body:
        set_ancestral_shown(request, experiment_id, bool(body["ancestral"]))

    return JsonResponse({"filter": filter_json(get_view_filter(request, experiment_id))})
