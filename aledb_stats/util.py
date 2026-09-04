import re
from aledb_common.constants import SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED
from aledb_seq.models import UnassignedMissingCoverageEvidence
from aledb_seq.functional_change import (
    FUNCTIONAL_CHANGE_TYPE_LIST, functional_change_bucket,
)
from aledb_seq.views.common import MUTATION_TYPE_LIST
import collections
import logging


logger = logging.getLogger(__name__)


def count_per_population(reseq_queryset):
    """`[(population name, time points, samples), ...]` for the Overview's table.

    It was `get_ale_flask_isolate_count_list`, and it counted samples per flask per ALE --
    which is what it still does, under the names those three things have now. The third
    number was called an isolate count and was always a count of samples.
    """
    per_population = {}
    for reseq in reseq_queryset:
        time_points = per_population.setdefault(reseq.population_name, {})
        time_points[reseq.time_point_value] = time_points.get(reseq.time_point_value, 0) + 1

    return [(name, len(time_points), sum(time_points.values()))
            for name, time_points in per_population.items()]


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
    (sample, mutation). Counting distinct mutations would quietly differ for a
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
    experiment_id = (reseq_experiments[0].experiment.id
                     if reseq_experiments else None)

    missing_coverage_counts = dict(
        UnassignedMissingCoverageEvidence.objects
        .filter(sample_id__in=reseq_ids)
        .values_list('sample_id')
        .annotate(total=Count('id')))
    mutation_counts = dict(
        exclude_ancestry(
            ObservedMutation.objects.filter(sample_id__in=reseq_ids),
            experiment_id)
        .values_list('sample_id')
        .annotate(total=Count('id')))

    reseq_experiments_info_list = []
    for reseq in reseq_experiments:
        species = reseq.time_point.population.species
        strain = reseq.time_point.population.strain
        knockouts = reseq.time_point.population.description
        clonal_or_population = (SAMPLE_TYPE_MIXED if reseq.is_mixed
                                else SAMPLE_TYPE_CLONAL)
        media_temperature = reseq.time_point.media.temperature
        media_description = reseq.time_point.media.description
        # carbon_source, not substrate: the metadata parser stopped writing `substrate`
        # in 2019 when media moved to per-component columns, so it is None for anything
        # imported with metadata.
        substrate = reseq.time_point.media.carbon_source

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

    # The join, not `sample_id__in=[every sample]`: the same rows, without an
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
