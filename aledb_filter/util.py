
from aledb_filter.models import AleExperimentFilter, GlobalFilter
import aledb_seq.models
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from aledb_common.util import get_gene_list
from aledb_common.util import is_int

__author__ = 'Patrick Phaneuf, Muyao :)'

NO_BREAK_STRING_CODE = u'\xa0'


def filtered_observed_mutation_queryset(observed_mutation_queryset, experiment_id=None,
                                       skip_global_filter=False, skip_experiment_filter=False):
    """The part of filtering that SQL can express, plus the gene lists that it cannot.

    Returns `(queryset, global_filter_genes, exp_filter_genes_map)`. The queryset has the
    mutation-id and frequency-cutoff exclusions applied; the two gene collections are what
    `filter_observed_mutations` below still has to walk the rows to apply, because "every gene
    this mutation touches is in the ignore list" is a set-subset test over a parsed column and
    there is no SQL for it.

    Split out so that a caller wanting *counts* rather than rows can aggregate this queryset in
    the database instead of materialising it -- see `aledb_stats.util.build_experiment_summary`,
    which is the whole reason the Overview page no longer pulls an experiment's every mutation
    into Python. When both gene collections come back empty, this queryset alone *is* the
    filter, exactly as the `else` branch at the end of `filter_observed_mutations` says.

    Deliberately not ordered or `select_related` here: those belong to rendering rows, and an
    `order_by` left on a queryset silently joins its columns into a later `.values().annotate()`
    GROUP BY, which would give a caller counting things the wrong number of groups.
    """
    if not skip_experiment_filter:
        if experiment_id:
            exp_filters = AleExperimentFilter.objects.filter(ale_experiment_id=experiment_id)
        else:
            exp_filters = AleExperimentFilter.objects.filter(ale_experiment_id__in=observed_mutation_queryset.values(
                "sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment_id"))
    else:
        exp_filters = AleExperimentFilter.objects.none()

    if not skip_global_filter:
        global_filter_genes, global_filter_muts = _get_global_filter_genes_muts()
    else:
        global_filter_genes, global_filter_muts = set(), []

    exp_filter_genes_map = dict()

    # filter muts by global filter
    q_queries = Q()
    if len(global_filter_muts) > 0:
        q_queries.add(Q(mutation__id__in=global_filter_muts), Q.OR)
    # filter muts by experiment filters
    for exp_filter in exp_filters:
        exp_filter_genes, exp_filter_muts = _get_exp_filter_genes_muts(exp_filter)
        if len(exp_filter_genes) > 0:
            exp_filter_genes_map[exp_filter.ale_experiment_id] = exp_filter_genes

        q_exp = Q()
        if exp_filter.min_cutoff and exp_filter.min_cutoff > 0:
            q_exp.add(Q(frequency__lt=exp_filter.min_cutoff / 100), Q.AND)
        if exp_filter.min_gatk_cutoff and exp_filter.min_gatk_cutoff > 0:
            q_exp.add(Q(frequency__lt=exp_filter.min_cutoff / 100), Q.AND)
        if exp_filter.max_cutoff and exp_filter.max_cutoff < 100:
            q_exp.add(Q(frequency__gt=exp_filter.max_cutoff / 100), Q.AND)
        if exp_filter.min_gatk_cutoff and exp_filter.min_gatk_cutoff > 0:
            q_exp.add(Q(frequency_gatk__lt=exp_filter.min_cutoff / 100), Q.AND)
        if exp_filter.max_gatk_cutoff and exp_filter.max_gatk_cutoff < 100:
            q_exp.add(Q(frequency_gatk__gt=exp_filter.max_cutoff / 100), Q.AND)
        if len(exp_filter_muts) > 0:
            q_exp.add(Q(mutation__id__in=exp_filter_muts), Q.OR)
        exp_q_query = Q(
            sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=exp_filter.ale_experiment_id)
        exp_q_query.add(q_exp, Q.AND)
        q_queries.add(exp_q_query, Q.OR)

    return observed_mutation_queryset.exclude(q_queries), global_filter_genes, exp_filter_genes_map


def filter_observed_mutations(observed_mutation_queryset, experiment_id=None, filter_type=None,
                              skip_global_filter=False, skip_experiment_filter=False):
    """
    R. Cai - 1/19/2019
    :param observed_mutation_queryset:
    :param experiment_id: experiment_id for the observed_mutation_queryset
    :param filter_type: 'AMP' or 'NOT_AMP' to filter by mutation type
    :param skip_global_filter: if True, do not apply global filter exclusions
    :param skip_experiment_filter: if True, do not apply experiment filter exclusions
    :return: list of observed_mutations sorted and loaded with related objects
    """
    queryset, global_filter_genes, exp_filter_genes_map = filtered_observed_mutation_queryset(
        observed_mutation_queryset, experiment_id,
        skip_global_filter=skip_global_filter, skip_experiment_filter=skip_experiment_filter)

    # filter genes
    queryset = queryset.select_related(
        'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment', 'mutation'
    ).order_by(
        'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__name',
        'sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_id',
        'sequencing_experiment__tech_rep__isolate__flask__flask_number',
        'sequencing_experiment__tech_rep__isolate__isolate_number',
        'sequencing_experiment__tech_rep__tech_rep_number'
    )
    observed_mutations = []
    deleted_global_mutations = set()
    if filter_type or len(global_filter_genes) > 0 or len(exp_filter_genes_map) > 0:
        for obs_mut in queryset:
            if filter_type == 'AMP':
                if obs_mut.mutation.mutation_type == 'AMP':
                    continue
            if filter_type == 'NOT_AMP':
                if obs_mut.mutation.mutation_type != 'AMP':
                    continue

            deleted = obs_mut.mutation.id in deleted_global_mutations
            if not deleted and len(global_filter_genes) > 0 or obs_mut.get_experiment_id() in exp_filter_genes_map:
                genes = set(get_gene_list(obs_mut.mutation.gene))
                if len(global_filter_genes) >= len(genes) and genes.issubset(global_filter_genes):
                    deleted_global_mutations.add(obs_mut.mutation.id)
                    deleted = True
                elif obs_mut.get_experiment_id() in exp_filter_genes_map:
                    exp_filter_genes = exp_filter_genes_map[obs_mut.get_experiment_id()]
                    if len(exp_filter_genes) >= len(genes) and genes.issubset(exp_filter_genes):
                        deleted = True
            if not deleted:
                observed_mutations.append(obs_mut)
    else:
        observed_mutations = [obs_mut for obs_mut in queryset]
    return observed_mutations


def _get_global_filter_genes_muts():
    ignored_genes = []
    ignored_mutations = []
    f = get_global_filter()
    if f.ignored_mutations:
        ignored_mutations = get_ignored_mut_id_list_from_str(f.ignored_mutations)
    if f.ignored_genes:
        ignored_genes = get_gene_list(f.ignored_genes)
    return set(ignored_genes), ignored_mutations


def _get_exp_filter_genes_muts(exp_filter: AleExperimentFilter):
    ignored_genes = []
    ignored_mutations = []
    if exp_filter.ignored_mutations:
        ignored_mutations = get_ignored_mut_id_list_from_str(exp_filter.ignored_mutations)
    if exp_filter.starting_strain_mutations:
        ignored_mutations += get_ignored_mut_id_list_from_str(exp_filter.starting_strain_mutations)
    if exp_filter.ignored_genes:
        ignored_genes = get_gene_list(exp_filter.ignored_genes)
    return set(ignored_genes), ignored_mutations


def get_ignored_mut_id_list_from_str(ignored_mutation_id_str, deleted_mutation_id=None):
    if not ignored_mutation_id_str:
        return []

    ignored_mutation_ids = ignored_mutation_id_str.split(",")
    deleted_mutation_ids = []
    if deleted_mutation_id:
        deleted_mutation_ids = deleted_mutation_id.split(",")

    new_list = [mut_id for mut_id in ignored_mutation_ids if is_int(mut_id) and mut_id not in deleted_mutation_ids]
    return new_list


def _get_ignored_gene_list_from_str(ignored_genes):

    if not ignored_genes:
        return []

    if ignored_genes.endswith(','):
        ignored_genes = ignored_genes[:-1]

    if ignored_genes.startswith(','):
        ignored_genes = ignored_genes[1:]

    ignored_genes = ignored_genes.replace(" ", "").replace('\n', '').replace('\r', '').split(',')
    cleaned_list = []

    for gene in ignored_genes:

        if gene == '' or not gene:
            continue

        cleaned_list.append(gene)

    return cleaned_list


def _mutation_exists(mut_id):
    try:
        aledb_seq.models.Mutation.objects.get(id=mut_id)
        return True
    except ObjectDoesNotExist:
        return False


def get_global_filter():
    global_filter, created = GlobalFilter.objects.get_or_create(id=1)
    return global_filter


def ensure_default_experiment_filter(ale_experiment_id):
    """Give an experiment a filter row if it has none. The 'experiment_filter' rebuilder.

    Registered ahead of every other rebuild (PRIORITY_SETTINGS), because every one of them
    counts mutations through `filter_observed_mutations`, which reads this row. It used to be
    the first statement of `gd_import.run_post_processing`, where only the import path reached
    it -- so an experiment created any other way had no filter until its first import.
    """
    from aledb_experiment.models import AleExperiment
    from aledb_filter.models import get_default_experiment_filter_params

    experiment = AleExperiment.objects.filter(ale_id=ale_experiment_id).first()
    if experiment is None:
        return
    AleExperimentFilter.objects.get_or_create(
        **get_default_experiment_filter_params(experiment))
