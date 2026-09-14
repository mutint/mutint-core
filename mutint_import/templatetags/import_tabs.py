"""`{% import_tabs active %}`: the strip of tabs at the top of the Import data page.

Rendered by the Import page for each of core's types, and by any plugin page that registered
a tab of its own -- mutint-breseq's launcher -- so every way into an experiment wears the
same strip. Reads `experiment_id` from the context; renders nothing without one.

**It also remembers which tab this was.** Recorded here rather than in each view because this
is the one place the active tab is already named, by core and by every plugin page alike --
a page that forgets to record it would have to have forgotten to draw the strip. The read is
needed anyway; the write happens only when the answer changed, so revisiting the same tab
costs nothing.
"""

from django import template

from mutint_common.import_tab_registry import get_import_tabs

register = template.Library()

#: One key for the whole strip, per user: which way in they used last. Not per experiment --
#: what somebody is doing is a habit rather than a property of the data, and a per-experiment
#: key would start every new experiment with no memory at all.
REMEMBERED_TAB = "import.tab"


def remembered_tab(user):
    """The tab this reader used last, or None. Anonymous readers remember nothing."""
    from mutint_common.preferences import get_preference

    if not getattr(user, "is_authenticated", False):
        return None
    stored = get_preference(user, REMEMBERED_TAB) or {}
    return stored.get("tab") or None


def remember_tab(user, key):
    """Record `key` as this reader's last tab, if it is not already."""
    from mutint_common.preferences import set_preference

    if not key or not getattr(user, "is_authenticated", False):
        return
    if remembered_tab(user) == key:
        return
    set_preference(user, REMEMBERED_TAB, {"tab": key})


@register.inclusion_tag("import/_tabs.html", takes_context=True)
def import_tabs(context, active=None):
    """The strip, and under it the annotation panel every import tab wears.

    **Nothing reaches `_tabs.html` that is not returned here.** `InclusionNode.render` builds
    its context with `context.new(...)`, which keeps the builtins and throws everything else
    away -- so the page's `request`, its `user`, and every context processor's variable are
    absent unless they are put in this dict. `asset_version` is the one that matters and the
    one that fails *silently*: a `?v={{ asset_version }}` in the template would simply render
    as `?v=`, serving last release's script from a browser cache, which is the exact failure
    `get_asset_version` exists to prevent. `AssetVersionTestCase` scans template text and
    would not notice, so `test_import_tabs_tag.py` guards this instead.

    The panel costs one indexed `Job` query plus a `status_of` per in-flight row, on every
    import tab page, plugins' included -- which is bounded by `annotation_jobs`' own cap. The
    tag already writes a preference on every render, so this was never a free call.
    """
    from django.urls import reverse

    from mutint_common.util import get_asset_version
    from mutint_experiment.models import Experiment
    from mutint_import.annotation_status import status_for

    experiment_id = context.get("experiment_id")
    if not experiment_id:
        return {"tabs": [], "active": active}
    request = context.get("request")
    if request is not None:
        remember_tab(request.user, active)

    # A page may have worked this out already -- core's import view does, because its own
    # script needs the same answer -- so take that rather than asking twice per render.
    status = context.get("annotation_status")
    if status is None:
        experiment = Experiment.objects.filter(pk=experiment_id).first()
        user = getattr(request, "user", None)
        status = (status_for(experiment, user) if experiment is not None
                  else {"busy": False, "stalled": None, "jobs": []})

    return {
        "tabs": get_import_tabs(experiment_id),
        "active": active,
        "experiment_id": experiment_id,
        "annotation_status": status,
        "status_url": reverse("import_annotator_status"),
        "asset_version": get_asset_version(),
    }
