import re
from django.db.models import Count
from aledb_seq.models import UnassignedMissingCoverageEvidence
from aledb_seq.util import get_observed_mutation_queryset
from aledb_seq.views.common import MUTATION_TYPE_LIST, FUNCTIONAL_CHANGE_TYPE_LIST
from aledb_filter.util import filtered_observed_mutation_queryset, gene_is_filtered
import collections
import logging


logger = logging.getLogger(__name__)

#: The experiment a row belongs to, fetched alongside it so that the gene half of the filter
#: -- a set-subset test that has no SQL -- can be applied per row without a second query.
EXPERIMENT_PATH = "sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment_id"

#: The order `filter_observed_mutations` returns rows in. Kept here so the computed needle
#: plot is element-for-element what the stored one was, rather than the same points shuffled.
ROW_ORDER = (
    'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__name',
    'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_id',
    'sequencing_experiment__tech_rep__isolate__flask__flask_number',
    'sequencing_experiment__tech_rep__isolate__isolate_number',
    'sequencing_experiment__tech_rep__tech_rep_number',
)


def get_needle_plot_data(experiment_id):
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
    """
    queryset, exp_filter_genes_map = filtered_observed_mutation_queryset(
        get_observed_mutation_queryset(experiment_id), experiment_id)
    rows = queryset.order_by(*ROW_ORDER).values_list(
        "mutation__position", "mutation__mutation_type", "mutation__gene", EXPERIMENT_PATH,
    ).iterator(chunk_size=2000)

    needle_plot_data = []
    for position, mutation_type, gene, exp_id in rows:
        if exp_filter_genes_map and gene_is_filtered(gene, exp_filter_genes_map.get(exp_id)):
            continue
        needle_plot_data.append({'coord': str(position),
                                 'category': mutation_type,
                                 'value': 1})
    return needle_plot_data


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
    """
    from django.db.models import Count
    from aledb_seq.models import ObservedMutation

    reseq_experiments = list(reseq_experiments)
    reseq_ids = [reseq.id for reseq in reseq_experiments]

    missing_coverage_counts = dict(
        UnassignedMissingCoverageEvidence.objects
        .filter(sequencing_experiment_id__in=reseq_ids)
        .values_list('sequencing_experiment_id')
        .annotate(total=Count('id')))
    mutation_counts = dict(
        ObservedMutation.objects
        .filter(sequencing_experiment_id__in=reseq_ids)
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
# There are two paths, and they are the same two the filter itself already has (see the
# branch at the end of `aledb_filter.util.filter_observed_mutations`):
#
#   * No gene filters -- the filter is entirely expressible as SQL, so the counts are two
#     aggregate queries and no row ever reaches Python.
#   * Gene filters set -- "every gene this mutation touches is in the ignore list" is a
#     set-subset test over a parsed column, which SQL cannot express, so the rows must be
#     walked. They are walked as `values_list` tuples rather than model instances: same
#     rows, same decisions, none of the JSON deserialisation or the fat columns.
#
# Both must produce identical dictionaries, and `test_summary.py` asserts that directly --
# the same fixture counted down each branch. It used to assert against a second, row-walking
# implementation kept in this file for the purpose; the literal counts it also pinned are the
# half that was doing the work, and a duplicate implementation is one more thing to keep in
# step for no coverage the literals do not already give.
# --------------------------------------------------------------------------------------


def _empty_counts():
    return ({mut_type: 0 for mut_type in MUTATION_TYPE_LIST},
            {mut_type: 0 for mut_type in MUTATION_TYPE_LIST},
            {change: 0 for change in FUNCTIONAL_CHANGE_TYPE_LIST},
            {change: 0 for change in FUNCTIONAL_CHANGE_TYPE_LIST})


def _count_in_sql(queryset):
    """The four count dicts as two aggregate queries. Requires no gene filters to be set.

    One caveat worth knowing rather than discovering: `__contains` compiles to `LIKE`, which
    SQLite applies case-insensitively to ASCII while PostgreSQL does not, where the Python
    `in` test it replaces is always case-sensitive. Every token in FUNCTIONAL_CHANGE_TYPE_LIST
    is lowercase and so is every `protein_change` the annotator writes
    (`aledb_import.annotate.display.text_mutation_annotation`), so the two agree on any data
    the import path can produce. Mixed-case data would count on SQLite where it did not
    before, which is the one difference this rewrite can make.
    """
    from django.db.models import Count, Q

    observed_types = {}
    unique_types = {}
    for row in (queryset.values('mutation__mutation_type')
                        .annotate(observed=Count('id'),
                                  unique=Count('mutation_id', distinct=True))):
        observed_types[row['mutation__mutation_type']] = row['observed']
        unique_types[row['mutation__mutation_type']] = row['unique']

    aggregates = {}
    for index, change in enumerate(FUNCTIONAL_CHANGE_TYPE_LIST):
        matches = Q(mutation__protein_change__contains=change)
        # Aliased by index: the tokens overlap as substrings ('synonymous' inside
        # 'nonsynonymous'), and using them as identifiers invites reading one for the other.
        aggregates['observed_%d' % index] = Count('id', filter=matches)
        aggregates['unique_%d' % index] = Count('mutation_id', distinct=True, filter=matches)
    protein = queryset.aggregate(**aggregates) if aggregates else {}

    mutation_type_counts, observed_mutation_type_counts, \
        protein_change_counts, observed_protein_change_counts = _empty_counts()

    # A mutation_type outside MUTATION_TYPE_LIST is dropped, as it always was --
    # `get_mutation_type_count_dict` only increments a key it already holds.
    for mut_type in MUTATION_TYPE_LIST:
        mutation_type_counts[mut_type] = unique_types.get(mut_type, 0)
        observed_mutation_type_counts[mut_type] = observed_types.get(mut_type, 0)

    for index, change in enumerate(FUNCTIONAL_CHANGE_TYPE_LIST):
        protein_change_counts[change] = protein.get('unique_%d' % index, 0)
        observed_protein_change_counts[change] = protein.get('observed_%d' % index, 0)

    return (mutation_type_counts, observed_mutation_type_counts,
            protein_change_counts, observed_protein_change_counts)


def _count_in_python(queryset, exp_filter_genes_map):
    """The four count dicts when a gene filter makes SQL alone insufficient.

    The exclusion logic below is `aledb_filter.util.filter_observed_mutations`' loop, applied
    to tuples instead of model instances. It is duplicated rather than shared because that
    function returns *rows* and this one returns counts, and materialising the rows to count
    them is the whole cost being removed -- but it must stay in step with it.

    It got shorter with the global filter. The `and`/`or` precedence in the old
    `should_test_genes` condition was a copied quirk, and the cross-experiment
    `deleted_global_mutations` cache existed only because a globally ignored gene meant the
    same everywhere. One list, one experiment, one test.
    """
    mutation_type_counts, observed_mutation_type_counts, \
        protein_change_counts, observed_protein_change_counts = _empty_counts()

    rows = queryset.values_list(
        'mutation_id',
        'mutation__mutation_type',
        'mutation__protein_change',
        'mutation__gene',
        'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment_id',
    ).iterator(chunk_size=2000)

    seen_mutations = set()
    for mutation_id, mutation_type, protein_change, gene, experiment_id in rows:
        if gene_is_filtered(gene, exp_filter_genes_map.get(experiment_id)):
            continue

        first_time = mutation_id not in seen_mutations
        seen_mutations.add(mutation_id)

        if mutation_type in observed_mutation_type_counts:
            observed_mutation_type_counts[mutation_type] += 1
            if first_time:
                mutation_type_counts[mutation_type] += 1
        for change in FUNCTIONAL_CHANGE_TYPE_LIST:
            if change in (protein_change or ""):
                observed_protein_change_counts[change] += 1
                if first_time:
                    protein_change_counts[change] += 1

    return (mutation_type_counts, observed_mutation_type_counts,
            protein_change_counts, observed_protein_change_counts)


def compute_experiment_counts(ale_experiment_id):
    """The Overview's four count dicts for one experiment, without materialising its rows."""
    from aledb_filter.util import filtered_observed_mutation_queryset
    from aledb_seq.util import get_observed_mutation_queryset

    # The join, not `sequencing_experiment_id__in=[every sample]`: the same rows, without an
    # IN clause carrying one literal per sample.
    queryset = get_observed_mutation_queryset(ale_experiment_id)
    queryset, exp_filter_genes_map = filtered_observed_mutation_queryset(
        queryset, ale_experiment_id)

    if not exp_filter_genes_map:
        return _count_in_sql(queryset)
    return _count_in_python(queryset, exp_filter_genes_map)


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
