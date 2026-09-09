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
    experiment_id = context.get("experiment_id")
    if not experiment_id:
        return {"tabs": [], "active": active}
    request = context.get("request")
    if request is not None:
        remember_tab(request.user, active)
    return {"tabs": get_import_tabs(experiment_id), "active": active}
