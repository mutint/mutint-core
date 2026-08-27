
from aledb_filter.models import AleExperimentFilter, GlobalFilter
from django.db.models import Q
from aledb_common.util import get_gene_list

__author__ = 'Patrick Phaneuf, Muyao :)'


def filtered_observed_mutation_queryset(observed_mutation_queryset, experiment_id=None,
                                       skip_global_filter=False, skip_experiment_filter=False):
    """The part of filtering that SQL can express, plus the gene lists that it cannot.

    Returns `(queryset, global_filter_genes, exp_filter_genes_map)`. The queryset has the
    frequency-cutoff exclusions applied; the two gene collections are what
    `filter_observed_mutations` below still has to walk the rows to apply, because "every gene
    this mutation touches is in the ignore list" is a set-subset test over a parsed column and
    there is no SQL for it.

    **This filter no longer hides individual mutations.** `ignored_mutations` and
    `starting_strain_mutations` were comma-joined `Mutation.id` strings excluded here -- a way
    of deleting a mutation while keeping the row, which recorded nothing about who did it and
    could not be undone. Removing a mutation is `aledb_mutation_editor`'s job now, and what it
    removes is the observation rather than the mutation, per sample rather than per experiment,
    with an entry in a change log that can be restored from. What is left here is filtering
    proper: frequency cutoffs, and genes.

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
        global_filter_genes = _get_global_filter_genes()
    else:
        global_filter_genes = set()

    exp_filter_genes_map = dict()

    q_queries = Q()
    # filter muts by experiment filters
    for exp_filter in exp_filters:
        exp_filter_genes = _get_exp_filter_genes(exp_filter)
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
        # An empty q_exp would leave `exp_q_query` as the bare experiment match, and this
        # whole Q is *excluded* -- so every mutation in the experiment would vanish. That was
        # unreachable while a non-empty ignored-mutation list could carry the clause on its
        # own; with those gone, a filter set to 0-100 (no cutoff at either end) reaches it.
        if not q_exp:
            continue
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


def _get_global_filter_genes():
    """The site-wide ignored genes. Used to also return a list of ignored Mutation ids."""
    global_filter = get_global_filter()
    if not global_filter.ignored_genes:
        return set()
    return set(get_gene_list(global_filter.ignored_genes))


def _get_exp_filter_genes(exp_filter: AleExperimentFilter):
    """One experiment's ignored genes.

    `_get_ignored_gene_list_from_str` used to live below this and was never called by anything
    -- `get_gene_list` from aledb_common is what actually parses the column. It went with the
    mutation-id helpers rather than being left as a second, unused parser of the same field.
    """
    if not exp_filter.ignored_genes:
        return set()
    return set(get_gene_list(exp_filter.ignored_genes))


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
    # `ale_experiment` is the lookup and everything else is a default, which is the whole
    # point. Passing the defaults as *lookups* -- which this did -- asks for "a filter for
    # this experiment whose settings are all still the factory ones", so the moment anyone
    # edited a filter this stopped matching their row and created a second one beside it.
    # `filter_observed_mutations` then ORed both rows' exclusions together, and
    # `ale_exp_filter`'s `get_or_create(ale_experiment=...)` raised MultipleObjectsReturned.
    # The same shape as the view's call, which always had it right.
    AleExperimentFilter.objects.get_or_create(
        ale_experiment=experiment,
        defaults=get_default_experiment_filter_params(experiment))
