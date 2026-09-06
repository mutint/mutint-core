"""Applying the reader's filter to a queryset, and saying what it did.

The *query* layer. The value it applies, and where that value comes from, is
`mutint_filter/view_filter.py` -- which is a separate module so that importing the filter does
not drag in `MutationCall`'s joins.

**There is one filter and it belongs to the reader.** `AleExperimentFilter` stood behind these
functions: one row per experiment, shared by everyone, edited at `/filter`. Every entry point
here used to *resolve rows* -- `filters_in_play` took either an experiment id or a queryset to
work them out from -- and now takes a `ViewFilter` instead. That is what `experiment_id` and
`skip_experiment_filter` were for, so both are gone rather than left as parameters that are
accepted and ignored: a control that does nothing is worse than no control, and the same is true
of an argument.

**One resolution, two consumers -- still, and more directly.** `describe_filters` turns the
filter into the sentence a page shows about itself and `filtered_mutation_call_queryset`
turns it into the exclusion. They take the *same object* now rather than reading the same rows
twice, so a page cannot describe filtering it is not doing. The failure that guards against is
unchanged and is worse than saying nothing: a page confidently describing a filter it did not
apply.

**Everything after the queryset is keyword-only.** Not style: `mutint_export/util.py` and
`mutint_sample/views/breseq_table.py` both passed an experiment id positionally into the second slot,
and this repo already carries a scar from exactly that shape -- `get_ordered_sample_dict` was once
called with a `request` in the `sample_type` slot, which silently dropped every population sample
from two plugin pages. Keyword-only turns a call site nobody updated into a `TypeError` instead
of a `ViewFilter` quietly landing in `filter_type`.
"""


from django.db.models import Q

from mutint_common.util import get_gene_list
from mutint_experiment.ordering import sample_order
from mutint_filter.view_filter import EMPTY
from mutint_experiment import paths

__author__ = 'Patrick Phaneuf, Muyao :)'


def filtered_mutation_call_queryset(mutation_call_queryset, *, view_filter=None):
    """The part of filtering that SQL can express, plus the genes that it cannot.

    Returns `(queryset, ignored_genes)`. The queryset has the frequency-cutoff exclusion applied;
    `ignored_genes` is the set `filter_mutation_calls` below still has to walk rows to apply,
    because "every gene this mutation touches is in the ignore list" is a set-subset test over a
    parsed column and there is no SQL for it.

    That second value used to be a `{experiment_id: genes}` map, because the old resolution could
    pull in several experiments' rows at once. A reader's filter is one filter, so it is a flat
    set -- and four callers each dropped a `values_list` column that existed only to key the map.

    Split out so a caller wanting *counts* rather than rows can aggregate this queryset in the
    database instead of materialising it. When `ignored_genes` comes back empty, this queryset
    alone *is* the filter.

    Deliberately not ordered or `select_related` here: those belong to rendering rows, and an
    `order_by` left on a queryset silently joins its columns into a later `.values().annotate()`
    GROUP BY, which would give a caller counting things the wrong number of groups.
    """
    view_filter = view_filter or EMPTY
    if view_filter.min_freq is None and view_filter.max_freq is None:
        # No `.exclude()` at all rather than an empty `Q`. An empty Q handed to `.exclude()`
        # excludes everything, which is how a 0-100 filter once emptied a whole experiment.
        return mutation_call_queryset, view_filter.genes_set

    # OR, not AND. This whole Q is *excluded*, so it has to read "below the floor **or** above
    # the ceiling". ANDed it said "below the floor and above the ceiling at the same time", which
    # no row can be -- so setting a maximum silently turned the minimum off as well.
    #
    # The cutoffs are percentages and `frequency` is a fraction, which is what the /100 is.
    #
    # **"20% means 0.2" has to be exactly true**, and this used to build the bound as a
    # `Decimal` to make it so: `frequency` was `DecimalField(5,4)`, `int / 100` is a binary
    # float that cannot represent 0.2, and a call stored as exactly 0.2000 then sat on
    # whichever side of the cutoff the backend's cross-type coercion put it.
    #
    # The column is a float now, so both sides of the comparison are the same IEEE double --
    # `20 / 100` and the stored value are bit-identical when the value really is 0.2 -- and
    # there is no coercion left to get wrong. The exactness is kept; what changed is what
    # keeps it. `test_a_call_at_exactly_the_floor_is_kept` is the guard.
    exclusion = Q()
    if view_filter.min_freq is not None:
        exclusion.add(Q(frequency__lt=view_filter.min_freq / 100), Q.OR)
    if view_filter.max_freq is not None:
        exclusion.add(Q(frequency__gt=view_filter.max_freq / 100), Q.OR)
    return mutation_call_queryset.exclude(exclusion), view_filter.genes_set


def filter_mutation_calls(mutation_call_queryset, *, filter_type=None, view_filter=None):
    """The reader's filter applied, as a list of rows ready to render.

    :param filter_type: 'AMP' to *exclude* amplifications, 'NOT_AMP' to keep only them. The
        values read backwards and always have; `get_table_body` is the caller that passes one.
    :param view_filter: the reader's filter, or None for unfiltered.
    :return: mutation calls, sorted and loaded with their related objects
    """
    queryset, ignored_genes = filtered_mutation_call_queryset(
        mutation_call_queryset, view_filter=view_filter)

    queryset = queryset.select_related(
        paths.to_experiment(paths.FROM_CALL), 'mutation'
    ).order_by(*sample_order("sample__"))
    if not filter_type and not ignored_genes:
        return list(queryset)

    mutation_calls = []
    for call in queryset:
        if filter_type == 'AMP' and call.mutation.mutation_type == 'AMP':
            continue
        if filter_type == 'NOT_AMP' and call.mutation.mutation_type != 'AMP':
            continue
        if gene_is_filtered(call.mutation.gene, ignored_genes):
            continue
        mutation_calls.append(call)
    return mutation_calls


def gene_is_filtered(gene, ignored_genes):
    """Whether a mutation on `gene` is hidden by an ignore list.

    **Subset, not intersection:** every gene the mutation touches has to be on the list. An
    intergenic mutation between an ignored gene and a kept one is still shown, because it is
    still evidence about the gene that was kept.
    """
    if not ignored_genes:
        return False
    genes = set(get_gene_list(gene))
    return len(ignored_genes) >= len(genes) and genes.issubset(ignored_genes)


def describe_filters(view_filter=None):
    """What filtering shaped a page's rows, for the page to say out loud.

    Takes the same `ViewFilter` the exclusion above is built from, so the sentence and the rows
    cannot disagree. Returns:

        {'applied': bool,      anything at all is being hidden
         'cutoff': (min, max), the range in force, or None
         'genes': [names]}     ignored genes, in the order they were typed

    `applied` is the question a page actually asks, and it is **not** "is a filter configured".
    It used to have to be careful about that, because a stored 0-100 row was configured and hid
    nothing. `ViewFilter` normalizes those ends away at construction, so the two questions have
    become one and this is simply `not is_empty`.
    """
    view_filter = view_filter or EMPTY
    cutoff = None
    if view_filter.min_freq is not None or view_filter.max_freq is not None:
        cutoff = (view_filter.min_freq or 0,
                  100 if view_filter.max_freq is None else view_filter.max_freq)
    return {
        "applied": not view_filter.is_empty,
        "cutoff": cutoff,
        "genes": list(view_filter.genes),
    }
