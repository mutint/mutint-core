"""`{% view_filter_fields %}`, `{% view_filter_form %}` and `{% view_filter_summary %}`.

The plugin-facing surface of filtering: the controls a reader changes their view with, and the
line a table shows about what it did. Tags rather than context keys, and that is what makes them
generic -- they read `experiment_id` and the request straight out of the context every table
page already has, so including them in `mutation_matrix/page.html` reaches mutint-compare,
mutint-fixation and mutint-converge without touching any of those repositories.

The library was `filter_summary`, which described a shared setting somebody else had configured.
It renders the controls now as well, because the filter belongs to whoever is reading.

**A control that does nothing is worse than no control.** The old checkbox this replaces was
gated on a `show_filter_toggles` flag each page had to remember to set, because it rendered inert
on three pages that never read it back. That flag is gone and the property is structural instead:
these tags render nothing at all without an `experiment_id` in the context, and any page that
has one resolves its filter through `get_view_filter`, so there is no way to draw a control that
is not connected to anything.
"""

from django import template

from mutint_experiment.ancestor import describe_ancestor
from mutint_filter.util import describe_filters
from mutint_filter.view_filter import GENES_PARAM, MAX_PARAM, MIN_PARAM, get_view_filter

register = template.Library()


def _resolved(context):
    """The experiment on this page and the filter the reader has on it, or `(None, None)`."""
    experiment_id = context.get("experiment_id")
    request = context.get("request")
    if not experiment_id or request is None:
        return None, None
    return experiment_id, get_view_filter(request, experiment_id)


def _fields_context(context, standalone):
    experiment_id, view_filter = _resolved(context)
    if experiment_id is None:
        return {"experiment_id": None}
    return {
        "experiment_id": experiment_id,
        "standalone": standalone,
        "view_filter": view_filter,
        "genes_text": ", ".join(view_filter.genes),
        "min_param": MIN_PARAM,
        "max_param": MAX_PARAM,
        "genes_param": GENES_PARAM,
    }


@register.inclusion_tag("filter/_fields.html", takes_context=True)
def view_filter_fields(context):
    """The filter's inputs, with no `<form>` of their own.

    For a page that already has one -- `mutation_matrix/page.html` puts every view control
    (ALE, sample type, the filter) in a single GET form behind a single Apply button, and a
    second form beside it would mean two Apply buttons that discard each other's pending edits.
    """
    return _fields_context(context, standalone=False)


@register.inclusion_tag("filter/_fields.html", takes_context=True)
def view_filter_form(context):
    """The same inputs wrapped in their own GET form, for a page with no form to join."""
    return _fields_context(context, standalone=True)


@register.inclusion_tag("filter/_summary.html", takes_context=True)
def view_filter_summary(context, own_rules=None, ancestor_subtracted=True):
    """Say what filtering shaped the rows on this page.

    `own_rules` is for a page filtering by rules of its own -- mutint-phylogeny encodes frequency
    in three states rather than excluding on it, and search spans experiments so no one reader's
    filter applies. A sentence says so, where an empty summary would read as "no filtering here"
    when the truth is "different filtering here".

    `ancestor_subtracted` is the designated ancestor, and it is **not** part of the reader's
    filter -- it is a fact about the dataset that nobody reading can turn off. It gets a
    sentence of its own for the same reason everything else here does: without one, the empty
    branch below claims "every stored mutation for this experiment is shown", which stops being
    true the moment an ancestor exists.

    It is resolved and stated **independently of `own_rules`**, because the pages that pass
    `own_rules` -- phylogeny and search -- are describing a *different frequency rule*, not
    opting out of the subtraction. Only `ancestor_subtracted=False` says a page did not
    subtract, and exactly one page passes it: the per-sample breseq table, which tints those
    rows rather than hiding them.
    """
    experiment_id, view_filter = _resolved(context)
    ancestor = describe_ancestor(experiment_id) if ancestor_subtracted else None

    if own_rules:
        # `experiment_id` is carried even here: the ancestor sentence links to that sample,
        # and phylogeny passes `own_rules` while still subtracting.
        return {"own_rules": own_rules, "summary": None, "experiment_id": experiment_id,
                "ancestor": ancestor}

    return {
        "own_rules": None,
        # The same object the exclusion is built from, so the sentence and the rows cannot
        # disagree -- a page confidently describing filtering it is not doing is worse than one
        # that says nothing.
        "summary": describe_filters(view_filter),
        "experiment_id": experiment_id,
        "ancestor": ancestor,
    }
