"""`{% import_tabs active %}`: the strip of tabs at the top of the Import data page.

Rendered by the Import page for each of core's types, and by any plugin page that registered
a tab of its own -- mutint-breseq's launcher -- so every way into an experiment wears the
same strip. Reads `experiment_id` from the context; renders nothing without one.
"""

from django import template

from mutint_common.import_tab_registry import get_import_tabs

register = template.Library()


@register.inclusion_tag("import/_tabs.html", takes_context=True)
def import_tabs(context, active=None):
    experiment_id = context.get("experiment_id")
    if not experiment_id:
        return {"tabs": [], "active": active}
    return {"tabs": get_import_tabs(experiment_id), "active": active}
