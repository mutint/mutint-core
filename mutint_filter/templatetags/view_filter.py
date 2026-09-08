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

from mutint_experiment.ancestor import ancestral_shown, ancestral_toggle_url, describe_ancestor
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


#: What a page says about the designated ancestor, the `ancestral=` argument below.
ANCESTRAL_SUBTRACTED = "subtracted"   # the default: its rows are gone, and the page says so
ANCESTRAL_TOGGLE = "toggle"           # the page draws them on request, and offers the button
ANCESTRAL_OWN = "own"                 # the ancestor's own sample page: nothing to subtract


@register.inclusion_tag("filter/_summary.html", takes_context=True)
def view_filter_summary(context, own_rules=None, ancestral=ANCESTRAL_SUBTRACTED,
                        ancestral_count=None):
    """Say what filtering shaped the rows on this page.

    `own_rules` is for a page filtering by rules of its own -- mutint-phylogeny encodes frequency
    in three states rather than excluding on it, and search spans experiments so no one reader's
    filter applies. A sentence says so, where an empty summary would read as "no filtering here"
    when the truth is "different filtering here".

    The designated ancestor is **not** part of the reader's filter -- it is a fact about the
    dataset, subtracted from everything computed, that no reader can turn off. It gets a
    sentence of its own for the same reason everything else here does: without one, the empty
    branch below claims "every stored mutation for this experiment is shown", which stops being
    true the moment an ancestor exists.

    It is resolved and stated **independently of `own_rules`**, because the pages that pass
    `own_rules` -- phylogeny and search -- are describing a *different frequency rule*, not
    opting out of the subtraction. `ancestral` says which of three things the page does:

    - `"subtracted"`, the default: the rows are gone and the sentence says so.
    - `"toggle"`: the page draws the subtracted rows, tinted red, when the reader asks
      (`ancestral_shown`), and the sentence carries the Show/Hide button. The view has to
      honour the same call, or the button is a control that does nothing -- which is why the
      matrix page renders this mode only when its view put `ancestral_mode` in the context.
      Two pages pass it: the per-sample breseq table and Compare. `ancestral_count` is how
      many rows the button would reveal, when the page knows cheaply.
    - `"own"`: the ancestor's own sample page, where there is nothing to subtract and the
      banner above already says what the page is.
    """
    experiment_id, view_filter = _resolved(context)
    ancestor = describe_ancestor(experiment_id) if ancestral != ANCESTRAL_OWN else None
    shown, toggle_url = False, None
    if ancestral == ANCESTRAL_TOGGLE and ancestor is not None:
        request = context.get("request")
        shown = ancestral_shown(request, experiment_id)
        toggle_url = ancestral_toggle_url(request, shown)

    about_ancestor = {
        "experiment_id": experiment_id,
        "ancestor": ancestor,
        "ancestral": ancestral,
        "ancestral_shown": shown,
        "ancestral_toggle_url": toggle_url,
        "ancestral_count": ancestral_count,
    }
    if own_rules:
        # `experiment_id` is carried even here: the ancestor sentence links to that sample,
        # and phylogeny passes `own_rules` while still subtracting.
        return {"own_rules": own_rules, "summary": None, **about_ancestor}

    return {
        "own_rules": None,
        # The same object the exclusion is built from, so the sentence and the rows cannot
        # disagree -- a page confidently describing filtering it is not doing is worse than one
        # that says nothing.
        "summary": describe_filters(view_filter),
        **about_ancestor,
    }
