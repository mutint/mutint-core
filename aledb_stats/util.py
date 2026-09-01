import re
from django.db.models import Count
from aledb_experiment.ordering import sample_order
from aledb_seq.models import UnassignedMissingCoverageEvidence
from aledb_seq.util import get_evolved_observation_queryset
from aledb_seq.functional_change import (
    FUNCTIONAL_CHANGE_TYPE_LIST, functional_change_bucket,
)
from aledb_seq.views.common import MUTATION_TYPE_LIST
import collections
import logging


logger = logging.getLogger(__name__)

#: The order `filter_observed_mutations` returns rows in. Kept here so the computed needle
#: plot is element-for-element what the stored one was, rather than the same points shuffled.
ROW_ORDER = sample_order("sequencing_experiment__")


def needle_plot_axis(experiment_id, contig=None):
    """Which sequence the needle plot draws, how long it is, and what else it could draw.

    The plot had **no idea what genome it was describing**. Its axis was a hardcoded
    `maxCoord: 5000000` in `muts_needle_plot.js` -- roughly E. coli, and wrong for anything
    else -- and `get_needle_plot_data` emitted a bare `coord` with no `seq_id`, so on a
    multi-contig reference every contig's positions were plotted on top of each other on one
    axis. Both failed silently: the plot rendered, it was simply not about this genome.

    One sequence at a time is the honest fix. The alternative -- laying contigs end to end on a
    concatenated axis -- needs offsets the reader cannot see and turns every coordinate into
    one that matches nothing in the tables.

    **Which one is the reader's to choose**, and that half was missing: one contig was
    hardcoded as the answer rather than as the default, so a plasmid's mutations were on no
    page in the product. `contig` is what the reader asked for, and an unrecognised one falls
    back to the default rather than drawing an empty plot -- the same posture
    `breseq_table._selected_reseq` takes with a sample id that its own filters exclude.

    Returns `{contig, length, contigs}`. `contigs` is every sequence the plot could draw,
    longest first, each carrying its own `count` and `length` -- the list the picker is built
    from, and the reason there is no separate count of it to disagree with.

    **The default is the longest sequence, not the busiest.** Those are usually the same and
    the difference matters when they are not: the chromosome is what somebody opening an
    experiment means by "the genome", while a small plasmid under strong selection can
    outnumber it and would then be what the page opened on. Length is a property of the
    reference; a mutation count is a property of this experiment's data, and it moves.

    **A sequence with no mutations is offered too.** It draws an empty axis, which is an
    answer -- the reader asked what is on the plasmid and the plot says nothing is. Left out,
    it is indistinguishable from a sequence the reference does not have. Its entry carries
    `count` 0, so the menu says which is which without a sentence beside it.

    `length` is None only when the experiment has no stored reference, and the plot then falls
    back to the largest coordinate it was given, which is still a better axis than a constant.
    That is also the one case where the sequences are not known independently of the
    mutations, so the list is what the mutations name and the busiest is the default.
    """
    from aledb_seq.models import ExperimentReference

    counts = {row["mutation__reseq_reference"]: row["n"]
              for row in (get_evolved_observation_queryset(experiment_id)
                          .values("mutation__reseq_reference")
                          .annotate(n=Count("id")))
              if row["mutation__reseq_reference"]}

    lengths = {}
    try:
        reference = ExperimentReference.objects.get(ale_experiment_id=experiment_id)
    except ExperimentReference.DoesNotExist:
        reference = None
    if reference:
        for entry in reference.seq_ids or []:
            lengths[entry.get("id")] = entry.get("length")

    # The reference says what sequences there are; the mutations can only add to that, and a
    # contig named by a mutation but absent from the reference is a state worth still being
    # able to plot rather than one to drop silently.
    names = set(lengths) | set(counts)

    contigs = [{"id": name,
                "count": counts.get(name, 0),
                "length": lengths.get(name)}
               # Longest first, so the chromosome leads and the plasmids follow it. With no
               # stored reference every length is None and this degrades to busiest first,
               # which is the most the data alone can say. The name is the final tie-break, or
               # two equal contigs swap places between page loads and the default becomes
               # whichever the database felt like.
               for name in sorted(names, key=lambda n: (-(lengths.get(n) or 0),
                                                        -counts.get(n, 0), n))]

    ids = [entry["id"] for entry in contigs]
    chosen = contig if contig in ids else (ids[0] if ids else None)

    return {"contig": chosen,
            "length": lengths.get(chosen),
            "contigs": contigs}


def get_needle_plot_data(experiment_id, contig=None):
    """`{coord, category, value}` per observed mutation, computed now.

    **Nothing is stored.** `StaticData` held this as a JSON blob kept current through the
    `static_data` rebuilder, and it was the oldest cache on the page -- precomputed at import
    since long before the rebuild registry existed. Reading three columns as tuples instead of
    instantiating the rows costs 0.05s on the largest experiment in the dev database, 52 139
    observations, against 3.38s for the model-instance path above.

    That number is what removes the failure mode the `ensure_fresh` here existed for. `/stats`
    renders this *and* `get_experiment_summary` from the same mutations, and while both were
    stored they could disagree in the same viewport -- one refreshed, the other stale. Neither
    is stored now and both read `get_observed_mutation_queryset`, so they cannot.

    Ordered, and deliberately: the plot does not care, but "the same points in a different
    order" is a difference a reader would have to rule out by hand every time this is compared
    against the reference implementation, and the sort is free at this size.

    **Unfiltered.** It applied the shared experiment filter until that filter became a
    per-reader one, and `/stats` is not one of the pages that honours it -- this is a summary of
    what the experiment holds, like the dashboard, rather than a table you are reading through.
    Two columns rather than four: the gene and the experiment were fetched only to apply the
    ignored-gene list per row.
    """
    queryset = get_evolved_observation_queryset(experiment_id)
    if contig:
        # Scoped to one contig, because `coord` carries no sequence name and two contigs'
        # positions on one axis is a plot of nothing. See `needle_plot_axis`.
        queryset = queryset.filter(mutation__reseq_reference=contig)
    rows = queryset.order_by(*ROW_ORDER).values_list(
        "mutation__position", "mutation__mutation_type",
    ).iterator(chunk_size=2000)

    return [{'coord': str(position), 'category': mutation_type, 'value': 1}
            for position, mutation_type in rows]


def get_ale_flask_isolate_count_list(reseq_queryset):
    ale_flask_isolate_count_dict = {}
    for reseq in reseq_queryset:
        if reseq.ale_id not in ale_flask_isolate_count_dict.keys():
            ale_flask_isolate_count_dict[reseq.ale_id] = {reseq.flask_number: 1}
        else:
            if reseq.flask_number not in ale_flask_isolate_count_dict[reseq.ale_id].keys():
                ale_flask_isolate_count_dict[reseq.ale_id][reseq.flask_number] = 1
            else:
                ale_flask_isolate_count_dict[reseq.ale_id][reseq.flask_number] += 1

    ale_flask_isolate_count_list = []
    for ale_id, flask_isolate_count_dict in ale_flask_isolate_count_dict.items():
        ale_flask_count = 0
        ale_isolate_count = 0
        for flask_isolate_count in flask_isolate_count_dict.values():
            ale_flask_count += 1
            ale_isolate_count += flask_isolate_count
        ale_flask_isolate_count_list.append((ale_id, ale_flask_count, ale_isolate_count))

    return ale_flask_isolate_count_list


def get_reseq_experiment_info_list(reseq_experiments):
    """One tuple per sample for the Sample Resequencing Stats table.

    **Two counts, two queries, however many samples there are.** Both used to be per-sample:
    the missing-coverage list was a queryset built in this loop, and the mutation count was
    `{{ ...0.mutations.count }}` in the template. Neither was evaluated here, so both fired
    during template rendering -- after the view's "stats performance" log had already been
    written, which is why that number never showed them. For an experiment with N samples the
    Overview issued 2N queries nobody was counting.

    `Count('id')` on ObservedMutation rather than a `distinct` count of mutations, because that
    is what `reseq.mutations.count()` did: the M2M goes through ObservedMutation, so its count
    is of join rows, and ObservedMutation has no unique constraint on
    (sequencing_experiment, mutation). Counting distinct mutations would quietly differ for a
    sample that observed one twice.

    They are two queries rather than two annotations on one, for the standard reason: two
    joined aggregates on the same row multiply each other's counts.

    **The mutation count subtracts the designated ancestor**, because the Overview totals
    above it do. These two numbers sit on one page: a per-sample column counting ancestral
    rows beside a summary that excluded them would not add up, and the reader has no way to
    tell which of the two is answering their question.
    """
    from django.db.models import Count
    from aledb_experiment.ancestor import exclude_ancestry
    from aledb_seq.models import ObservedMutation

    reseq_experiments = list(reseq_experiments)
    reseq_ids = [reseq.id for reseq in reseq_experiments]
    experiment_id = (reseq_experiments[0].ale_experiment.ale_id
                     if reseq_experiments else None)

    missing_coverage_counts = dict(
        UnassignedMissingCoverageEvidence.objects
        .filter(sequencing_experiment_id__in=reseq_ids)
        .values_list('sequencing_experiment_id')
        .annotate(total=Count('id')))
    mutation_counts = dict(
        exclude_ancestry(
            ObservedMutation.objects.filter(sequencing_experiment_id__in=reseq_ids),
            experiment_id)
        .values_list('sequencing_experiment_id')
        .annotate(total=Count('id')))

    reseq_experiments_info_list = []
    for reseq in reseq_experiments:
        species = reseq.tech_rep.isolate.flask.ale_id.species
        strain = reseq.tech_rep.isolate.flask.ale_id.strain
        knockouts = reseq.tech_rep.isolate.flask.ale_id.description
        clonal_or_population = "clonal"
        if reseq.tech_rep.isolate.is_population:
            clonal_or_population = "population"
        media_temperature = reseq.tech_rep.isolate.flask.media.temperature
        media_description = reseq.tech_rep.isolate.flask.media.description
        # carbon_source, not substrate: the metadata parser stopped writing `substrate`
        # in 2019 when media moved to per-component columns, so it is None for anything
        # imported with metadata.
        substrate = reseq.tech_rep.isolate.flask.media.carbon_source

        # Using tuple because immutable; the counts must remain associated with particular
        # experiment. Position 1 holds the missing-coverage *count* -- it held the queryset
        # itself until the template's `|length` on it turned out to be a per-row query.
        experiment_info_tuple = (reseq,
                                 missing_coverage_counts.get(reseq.id, 0),
                                 clonal_or_population,
                                 media_temperature,
                                 media_description,
                                 substrate,
                                 species,
                                 strain,
                                 knockouts,
                                 mutation_counts.get(reseq.id, 0))
        reseq_experiments_info_list.append(experiment_info_tuple)
    return reseq_experiments_info_list


# --------------------------------------------------------------------------------------
# The Overview page's mutation counts
#
# These four dictionaries are what `/stats` renders in its two count tables, and producing
# them used to mean pulling every ObservedMutation in the experiment -- each joined across six
# tables and instantiated as a model carrying two JSONFields and a gene column of up to 19 000
# characters -- to arrive at about sixteen integers. They are counted where the rows already
# are instead, which is why nothing is stored: 0.07s on the largest experiment in the dev
# database is not worth an `ExperimentSummary` row to keep correct.
#
# There was a second, row-walking path beside this one, chosen when the experiment's filter
# ignored genes -- a set-subset test over a parsed column that SQL cannot express. `/stats` no
# longer filters at all, so there is nothing here SQL cannot do, and that path went with the
# branch that selected it. `test_summary.py` pins the counts literally, which is what was
# doing the work; asserting one implementation against another was the half that was not.
# --------------------------------------------------------------------------------------


def _empty_counts():
    return ({mut_type: 0 for mut_type in MUTATION_TYPE_LIST},
            {mut_type: 0 for mut_type in MUTATION_TYPE_LIST},
            {change: 0 for change in FUNCTIONAL_CHANGE_TYPE_LIST},
            {change: 0 for change in FUNCTIONAL_CHANGE_TYPE_LIST})


def _count_in_sql(queryset):
    """The four count dicts as two aggregate queries. Requires no gene filters to be set.

    **Both halves group; neither matches substrings.** The functional-change half used to be one
    `Count(filter=Q(mutation__protein_change__contains=change))` per token, aliased by index
    because the tokens overlap (`synonymous` inside `nonsynonymous`). It groups by
    `mutation__snp_type` instead and resolves each distinct value in Python, because a severity
    hierarchy over `|`-joined values is not something `LIKE` can express -- see
    `aledb_seq.functional_change`.

    **Summing per-group distinct counts is exact, not an approximation**, and the reason is a
    constraint on anyone editing this: the group key is a column of `Mutation`, reached by a
    forward foreign key, so every observation of a mutation falls in exactly one group and the
    per-group sets of `mutation_id` are disjoint. Group by anything reached through a reverse or
    many-to-many relation -- the sample, a tag -- and one mutation lands in several groups, and
    the sums silently exceed the true distinct count.

    A caveat that used to live here is now void, and is retracted rather than deleted so that a
    reader who remembers it is told: `__contains` compiled to `LIKE`, which SQLite applies
    case-insensitively to ASCII while PostgreSQL does not. There is no `LIKE` here any more.
    """
    from django.db.models import Count

    observed_types = {}
    unique_types = {}
    for row in (queryset.values('mutation__mutation_type')
                        .annotate(observed=Count('id'),
                                  unique=Count('mutation_id', distinct=True))):
        observed_types[row['mutation__mutation_type']] = row['observed']
        unique_types[row['mutation__mutation_type']] = row['unique']

    mutation_type_counts, observed_mutation_type_counts, \
        protein_change_counts, observed_protein_change_counts = _empty_counts()

    # One group per distinct snp_type -- 19 of them across the whole dev database, including a
    # NULL group for rows imported before the annotator existed, which resolves to UNANNOTATED
    # like every other value with no answer in it.
    for row in (queryset.values('mutation__snp_type')
                        .annotate(observed=Count('id'),
                                  unique=Count('mutation_id', distinct=True))):
        change = functional_change_bucket(row['mutation__snp_type'])
        observed_protein_change_counts[change] += row['observed']
        protein_change_counts[change] += row['unique']

    # A mutation_type outside MUTATION_TYPE_LIST is dropped, as it always was: the original
    # helper only incremented a key it already held. Note the asymmetry with the functional
    # change half, where an unknown value is *bucketed* as unannotated rather than dropped --
    # so these two sets of counts have different totals, deliberately.
    for mut_type in MUTATION_TYPE_LIST:
        mutation_type_counts[mut_type] = unique_types.get(mut_type, 0)
        observed_mutation_type_counts[mut_type] = observed_types.get(mut_type, 0)

    return (mutation_type_counts, observed_mutation_type_counts,
            protein_change_counts, observed_protein_change_counts)


def compute_experiment_counts(ale_experiment_id):
    """The Overview's four count dicts for one experiment, without materialising its rows.

    **Unfiltered**, for the same reason as the needle plot above: `/stats` summarises what the
    experiment holds rather than showing rows you are reading through, and filtering became a
    per-reader affair that this page does not take part in.

    There used to be two paths here. The gene half of the old filter -- "every gene this
    mutation touches is in the ignore list" -- is a set-subset test over a parsed column with no
    SQL equivalent, so a filter with ignored genes forced the rows to be walked in Python.
    Nothing is filtered now, so there is nothing SQL cannot express, and `_count_in_python` went
    with the branch that chose it.
    """
    from aledb_seq.util import get_evolved_observation_queryset

    # The join, not `sequencing_experiment_id__in=[every sample]`: the same rows, without an
    # IN clause carrying one literal per sample.
    return _count_in_sql(get_evolved_observation_queryset(ale_experiment_id))


#: What `/stats` reads. An `ExperimentSummary` row stood here with these four field names,
#: which is why they are these four field names: the view and the template are unchanged.
ExperimentCounts = collections.namedtuple(
    "ExperimentCounts",
    "mutation_type_counts observed_mutation_type_counts "
    "protein_change_counts observed_protein_change_counts")


def get_experiment_summary(ale_experiment_id):
    """The Overview's four count dicts, computed now.

    **Nothing is stored.** `ExperimentSummary` held them and the 'overview' rebuilder kept it
    current. The row was worth having while producing the counts meant materialising every
    observation in the experiment; it stopped being worth it when `compute_experiment_counts`
    moved the work into SQL, which is 0.07s on 52 139 observations.

    The zero-filled fallback went with the table. It existed because a rebuild could fail and
    leave nothing stored, so the page had to render *something* rather than 500 -- a state
    that cannot arise when the counts are computed by the request that needs them.
    """
    return ExperimentCounts(*compute_experiment_counts(ale_experiment_id))
