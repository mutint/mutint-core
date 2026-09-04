import re
from aledb_common.constants import SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED
from aledb_seq.models import UncalledRegion
from aledb_seq.functional_change import (
    FUNCTIONAL_CHANGE_TYPE_LIST, functional_change_bucket,
)
from aledb_seq.views.common import MUTATION_TYPE_LIST
import collections
import logging

from django.db.models import F, Sum


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



def uncalled_bases_per_sample(sample_ids):
    """``{sample_id: bases}`` -- how much of the genome could not be called, per sample.

    Summed in the database rather than in Python: one row per region and one aggregate,
    where the count it replaced was already a single query and it would be a shame to trade
    that for a loop.

    **Bounds are inclusive**, hence the `+ 1`: `_is_uncovered` in aledb-phylogeny asks
    `start <= position <= end`, so a region from 100 to 100 is one base and not zero. This
    also assumes a sample's regions do not overlap, which is what breseq's MC evidence
    gives -- overlapping ones would have their shared bases counted twice, and on a page
    that renders this as a percentage that can read as more than 100%.
    """
    return dict(
        UncalledRegion.objects
        .filter(sample_id__in=sample_ids)
        .values_list("sample_id")
        .annotate(total=Sum(F("end") - F("start") + 1)))


def _reference_length(experiment_id):
    """Total bases in the experiment's reference, or 0 if there is no usable figure.

    `ExperimentReference.total_length` defaults to 0, and an experiment can have no
    reference row at all -- so this is "unknown" as often as it is a number, which is why
    `_percent_of` answers None rather than dividing.
    """
    if experiment_id is None:
        return 0
    from aledb_seq.models import ExperimentReference

    return (ExperimentReference.objects
            .filter(experiment_id=experiment_id)
            .values_list("total_length", flat=True).first() or 0)


def _percent_of(bases, reference_length):
    """The share of the reference these bases are, or None when that cannot be said.

    None rather than 0: an experiment whose reference length is unknown has *no* answer
    here, and printing "0.0%" beside a real base count would be a claim that the genome is
    entirely called.
    """
    if not reference_length:
        return None
    return 100.0 * bases / reference_length

def get_reseq_experiment_info_list(reseq_experiments):
    """One dict per sample for the Overview's per-sample table.

    **Keyed by name, and this used to be an eleven-member tuple the template indexed.**
    `aledb_metadata.get_sample_info_list` was converted away from exactly that shape after
    two of its values drifted onto the wrong column, and this one was one insertion away
    from the same thing: adding the uncalled-bases percentage in the middle moved the
    mutation count from `.9` to `.10`, in a template that says neither name.

    Seven of those eleven members went with the conversion -- species, strain, the
    population's description, the sample type, two media fields and the carbon source. The
    template read `.0`, `.1` and `.9` and nothing else, so each of the seven was traversed
    off the sample on every request and rendered nowhere.

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
    sample_ids = [reseq.id for reseq in reseq_experiments]
    experiment_id = (reseq_experiments[0].experiment.id
                     if reseq_experiments else None)

    uncalled_bases = uncalled_bases_per_sample(sample_ids)
    reference_length = _reference_length(experiment_id)
    mutation_counts = dict(
        exclude_ancestry(
            ObservedMutation.objects.filter(sample_id__in=sample_ids),
            experiment_id)
        .values_list('sample_id')
        .annotate(total=Count('id')))

    rows = []
    for reseq in reseq_experiments:
        bases = uncalled_bases.get(reseq.id, 0)
        rows.append({
            "sample": reseq,
            "mutation_count": mutation_counts.get(reseq.id, 0),
            "uncalled_bases": bases,
            # None when the reference length is unknown -- see `_percent_of`.
            "uncalled_percent": _percent_of(bases, reference_length),
        })
    return rows


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


def compute_experiment_counts(experiment_id):
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
    return _count_in_sql(get_evolved_observation_queryset(experiment_id))


#: What `/stats` reads. An `ExperimentSummary` row stood here with these four field names,
#: which is why they are these four field names: the view and the template are unchanged.
ExperimentCounts = collections.namedtuple(
    "ExperimentCounts",
    "mutation_type_counts observed_mutation_type_counts "
    "protein_change_counts observed_protein_change_counts")


def get_experiment_summary(experiment_id):
    """The Overview's four count dicts, computed now.

    **Nothing is stored.** `ExperimentSummary` held them and the 'overview' rebuilder kept it
    current. The row was worth having while producing the counts meant materialising every
    observation in the experiment; it stopped being worth it when `compute_experiment_counts`
    moved the work into SQL, which is 0.07s on 52 139 observations.

    The zero-filled fallback went with the table. It existed because a rebuild could fail and
    leave nothing stored, so the page had to render *something* rather than 500 -- a state
    that cannot arise when the counts are computed by the request that needs them.
    """
    return ExperimentCounts(*compute_experiment_counts(experiment_id))
