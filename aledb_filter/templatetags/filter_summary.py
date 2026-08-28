"""`{% filter_summary %}` -- the line a table shows about the filtering behind it.

A template tag rather than a context key, and that is what makes it generic: it reads
`ale_experiment_id` and `show_exp_filtered` out of the context every table page already has, so
including it in `base_table_template.html` reaches aledb-compare, aledb-fixation and
aledb-converge without touching any of their repositories.

A page that filters by rules of its own passes them in instead -- see `own_rules` below.
"""

from django import template

from aledb_filter.util import describe_filters

register = template.Library()


@register.inclusion_tag("filter/_summary.html", takes_context=True)
def filter_summary(context, own_rules=None):
    """Describe the filtering behind the table on this page.

    `own_rules` is for a page that does not use the experiment filter at all -- aledb-phylogeny
    encodes frequency itself rather than excluding on it. Passing a sentence says so, instead
    of the page rendering an empty summary that reads as "no filtering here" when the truth is
    "different filtering here".
    """
    if own_rules:
        return {"own_rules": own_rules, "summary": None, "experiment_id": None,
                "has_toggle": False}

    experiment_id = context.get("ale_experiment_id")
    return {
        "own_rules": None,
        "summary": describe_filters(
            experiment_id=experiment_id,
            skip_experiment_filter=bool(context.get("show_exp_filtered"))),
        "experiment_id": experiment_id,
        # Only Compare renders the Show Experiment Filtered checkbox -- the shared template
        # gates it on this flag. Telling a reader to tick a control their page does not have
        # is worse than not mentioning it.
        "has_toggle": bool(context.get("show_filter_toggles")),
    }
