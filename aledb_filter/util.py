"""Applying the reader's filter to a queryset, and saying what it did.

The *query* layer. The value it applies, and where that value comes from, is
`aledb_filter/view_filter.py` -- which is a separate module so that importing the filter does
not drag in `ObservedMutation`'s joins.

**There is one filter and it belongs to the reader.** `AleExperimentFilter` stood behind these
functions: one row per experiment, shared by everyone, edited at `/filter`. Every entry point
here used to *resolve rows* -- `filters_in_play` took either an experiment id or a queryset to
work them out from -- and now takes a `ViewFilter` instead. That is what `experiment_id` and
`skip_experiment_filter` were for, so both are gone rather than left as parameters that are
accepted and ignored: a control that does nothing is worse than no control, and the same is true
of an argument.

**One resolution, two consumers -- still, and more directly.** `describe_filters` turns the
filter into the sentence a page shows about itself and `filtered_observed_mutation_queryset`
turns it into the exclusion. They take the *same object* now rather than reading the same rows
twice, so a page cannot describe filtering it is not doing. The failure that guards against is
unchanged and is worse than saying nothing: a page confidently describing a filter it did not
apply.

**Everything after the queryset is keyword-only.** Not style: `aledb_export/util.py` and
`aledb_seq/views/breseq_table.py` both passed an experiment id positionally into the second slot,
and this repo already carries a scar from exactly that shape -- `get_reseq_ordered_dict` was once
called with a `request` in the `sample_type` slot, which silently dropped every population sample
from two plugin pages. Keyword-only turns a call site nobody updated into a `TypeError` instead
of a `ViewFilter` quietly landing in `filter_type`.
"""

from decimal import Decimal

from django.db.models import Q

from aledb_common.util import get_gene_list
from aledb_experiment.ordering import sample_order
from aledb_filter.view_filter import EMPTY

__author__ = 'Patrick Phaneuf, Muyao :)'


def filtered_observed_mutation_queryset(observed_mutation_queryset, *, view_filter=None):
    """The part of filtering that SQL can express, plus the genes that it cannot.

    Returns `(queryset, ignored_genes)`. The queryset has the frequency-cutoff exclusion applied;
    `ignored_genes` is the set `filter_observed_mutations` below still has to walk rows to apply,
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
        return observed_mutation_queryset, view_filter.genes_set

    # OR, not AND. This whole Q is *excluded*, so it has to read "below the floor **or** above
    # the ceiling". ANDed it said "below the floor and above the ceiling at the same time", which
    # no row can be -- so setting a maximum silently turned the minimum off as well.
    #
    # The cutoffs are percentages and `frequency` is a fraction, which is what the /100 is.
    #
    # Decimal, not float. `frequency` is a DecimalField(max_digits=5, decimal_places=4), and
    # `int / 100` is a binary float that cannot represent 0.2 exactly -- so a call stored as
    # exactly 0.2000 sits on the wrong side of a 20% cutoff depending on how the backend
    # coerces the two types to compare them. Building the bound as a Decimal makes the
    # comparison exact, and makes "20% means 0.2000" true rather than nearly true.
    exclusion = Q()
    if view_filter.min_freq is not None:
        exclusion.add(Q(frequency__lt=Decimal(view_filter.min_freq) / 100), Q.OR)
    if view_filter.max_freq is not None:
        exclusion.add(Q(frequency__gt=Decimal(view_filter.max_freq) / 100), Q.OR)
    return observed_mutation_queryset.exclude(exclusion), view_filter.genes_set


def filter_observed_mutations(observed_mutation_queryset, *, filter_type=None, view_filter=None):
    """The reader's filter applied, as a list of rows ready to render.

    :param filter_type: 'AMP' to *exclude* amplifications, 'NOT_AMP' to keep only them. The
        values read backwards and always have; `get_table_body` is the caller that passes one.
    :param view_filter: the reader's filter, or None for unfiltered.
    :return: observed mutations, sorted and loaded with their related objects
    """
    queryset, ignored_genes = filtered_observed_mutation_queryset(
        observed_mutation_queryset, view_filter=view_filter)

    queryset = queryset.select_related(
        'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment', 'mutation'
    ).order_by(*sample_order("sequencing_experiment__"))
    if not filter_type and not ignored_genes:
        return list(queryset)

    observed_mutations = []
    for obs_mut in queryset:
        if filter_type == 'AMP' and obs_mut.mutation.mutation_type == 'AMP':
            continue
        if filter_type == 'NOT_AMP' and obs_mut.mutation.mutation_type != 'AMP':
            continue
        if gene_is_filtered(obs_mut.mutation.gene, ignored_genes):
            continue
        observed_mutations.append(obs_mut)
    return observed_mutations


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
    nothing. `ViewFilter` normalises those ends away at construction, so the two questions have
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
