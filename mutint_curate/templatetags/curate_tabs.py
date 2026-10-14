"""`{% curate_tabs active %}`: the strip of tabs at the top of the Curate pages.

Rendered by each of core's five pages and by any plugin page that registered a tab of its
own, so every page of the strip wears the same strip. Reads `experiment_id` from the context
and renders nothing without one.
"""

from django import template

from mutint_common.curate_tab_registry import get_curate_tabs

register = template.Library()


@register.inclusion_tag("curate/_tabs.html", takes_context=True)
def curate_tabs(context, active=None):
    experiment_id = context.get("experiment_id")
    if not experiment_id:
        return {"tabs": [], "active": active}
    return {"tabs": get_curate_tabs(experiment_id), "active": active,
            "experiment_id": experiment_id}
