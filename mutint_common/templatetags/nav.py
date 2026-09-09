"""The sidebar's experiment section, with what this reader cannot act on left out.

`nav_registry` carries `requires_edit` and does not apply it, because applying it needs two
things the registry never sees: **which** experiment is selected, and **who** is reading. Only
the template has both -- the experiment comes from the view's own context
(`Experiment.experiment_context`) rather than from the query string, since several pages take
it from their URL instead.

So this is a tag rather than a context processor: a context processor runs before the view's
context is merged and would have to re-derive the experiment from the request, which is exactly
the guess that would be wrong on `/sample/<pk>/edit/`.
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def experiment_nav_items(context):
    """The experiment section's entries this reader should see.

    **The question is the role, not the lock.** `can_edit_project` rather than
    `can_edit_experiment`: a locked experiment refuses every write, but its editing pages say
    so in a sentence, and hiding them would leave somebody unable to find out why the buttons
    they remember are gone. A reader with no write role is a different case -- those pages have
    nothing to tell them and never will.

    One permission question per render, whatever the section holds, and the role cache in
    `mutint_experiment.permissions` makes a repeat of it free.
    """
    items = context.get('nav_experiment_items') or []
    if not any(item.get('requires_edit') for item in items):
        return items

    if _may_edit(context):
        return items
    return [item for item in items if not item.get('requires_edit')]


def _may_edit(context):
    """Whether this reader may write to the selected experiment. False whenever that cannot
    be established -- an entry hidden from somebody who could have used it is a smaller
    failure than one that leads them to a refusal."""
    from mutint_experiment.models import Experiment
    from mutint_experiment.permissions import can_edit_project

    request = context.get('request')
    experiment_id = context.get('experiment_id')
    if request is None or not experiment_id:
        return False

    experiment = Experiment.objects.filter(pk=experiment_id).select_related('project').first()
    if experiment is None:
        return False
    return can_edit_project(request.user, experiment.project)
