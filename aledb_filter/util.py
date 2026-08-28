
from aledb_filter.models import AleExperimentFilter
from django.db.models import Q
from aledb_common.util import get_gene_list

__author__ = 'Patrick Phaneuf, Muyao :)'


def filters_in_play(observed_mutation_queryset=None, experiment_id=None,
                    skip_experiment_filter=False):
    """The `AleExperimentFilter` rows that apply to these mutations.

    **One resolution, two consumers.** `filtered_observed_mutation_queryset` turns these rows
    into the exclusions, and `describe_filters` turns the same rows into the sentence a page
    shows about itself. Deriving the description separately would be a second opinion about
    which filters apply, and the failure mode of a description that drifts is worse than no
    description at all -- a page confidently describing filtering it is not doing.

    `experiment_id` narrows to one experiment. Without it the filters are found from the
    queryset's own experiments, which is what a cross-experiment page like search needs.
    """
    if skip_experiment_filter:
        return AleExperimentFilter.objects.none()
    if experiment_id:
        return AleExperimentFilter.objects.filter(ale_experiment_id=experiment_id)
    if observed_mutation_queryset is None:
        return AleExperimentFilter.objects.none()
    return AleExperimentFilter.objects.filter(
        ale_experiment_id__in=observed_mutation_queryset.values(
            "sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment_id"))


def filtered_observed_mutation_queryset(observed_mutation_queryset, experiment_id=None,
                                       skip_experiment_filter=False):
    """The part of filtering that SQL can express, plus the gene lists that it cannot.

    Returns `(queryset, exp_filter_genes_map)`. The queryset has the frequency-cutoff
    exclusions applied; the gene map is what `filter_observed_mutations` below still has to
    walk the rows to apply, because "every gene this mutation touches is in the ignore list"
    is a set-subset test over a parsed column and there is no SQL for it.

    **There is one filter, and it belongs to an experiment.** `GlobalFilter` was a second,
    installation-wide ignored-gene list, superuser-only, linked from nowhere -- the only
    reference to its page was a commented-out sidebar entry -- and empty in practice. It went
    the way its `ignored_mutations` column had already gone, with `aledb_filter.0005` folding
    whatever any deployment had into each experiment's own list first, so nothing that was
    hidden became visible.

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
    into Python. When the gene map comes back empty, this queryset alone *is* the filter,
    exactly as the `else` branch at the end of `filter_observed_mutations` says.

    Deliberately not ordered or `select_related` here: those belong to rendering rows, and an
    `order_by` left on a queryset silently joins its columns into a later `.values().annotate()`
    GROUP BY, which would give a caller counting things the wrong number of groups.
    """
    exp_filters = filters_in_play(observed_mutation_queryset, experiment_id,
                                  skip_experiment_filter)

    exp_filter_genes_map = dict()

    q_queries = Q()
    # filter muts by experiment filters
    for exp_filter in exp_filters:
        exp_filter_genes = _get_exp_filter_genes(exp_filter)
        if len(exp_filter_genes) > 0:
            exp_filter_genes_map[exp_filter.ale_experiment_id] = exp_filter_genes

        # OR, not AND. This whole Q is *excluded*, so it has to read "below the floor **or**
        # above the ceiling". ANDed it said "below the floor and above the ceiling at the
        # same time", which no row can be -- so setting a maximum silently turned the
        # minimum off as well, and neither end filtered anything.
        #
        # Five terms stood here. Two named `frequency_gatk`, which no import path has ever
        # written; a comparison against null is never true, so ANDing one in made the whole
        # clause unsatisfiable and the cutoff excluded nothing at all. A third was a straight
        # duplicate of the first, and the two gatk branches read `min_cutoff`/`max_cutoff`
        # rather than their own settings -- so those settings were never values, only
        # switches. All of it is gone with the column.
        q_exp = Q()
        if exp_filter.min_cutoff and exp_filter.min_cutoff > 0:
            q_exp.add(Q(frequency__lt=exp_filter.min_cutoff / 100), Q.OR)
        if exp_filter.max_cutoff and exp_filter.max_cutoff < 100:
            q_exp.add(Q(frequency__gt=exp_filter.max_cutoff / 100), Q.OR)
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

    return observed_mutation_queryset.exclude(q_queries), exp_filter_genes_map


def filter_observed_mutations(observed_mutation_queryset, experiment_id=None, filter_type=None,
                              skip_experiment_filter=False):
    """The experiment's filter applied, as a list of rows ready to render.

    :param observed_mutation_queryset:
    :param experiment_id: experiment_id for the observed_mutation_queryset
    :param filter_type: 'AMP' or 'NOT_AMP' to filter by mutation type
    :param skip_experiment_filter: if True, do not apply experiment filter exclusions
    :return: list of observed_mutations sorted and loaded with related objects
    """
    queryset, exp_filter_genes_map = filtered_observed_mutation_queryset(
        observed_mutation_queryset, experiment_id,
        skip_experiment_filter=skip_experiment_filter)

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
    if filter_type or len(exp_filter_genes_map) > 0:
        for obs_mut in queryset:
            if filter_type == 'AMP':
                if obs_mut.mutation.mutation_type == 'AMP':
                    continue
            if filter_type == 'NOT_AMP':
                if obs_mut.mutation.mutation_type != 'AMP':
                    continue

            deleted = gene_is_filtered(
                obs_mut.mutation.gene,
                exp_filter_genes_map.get(obs_mut.get_experiment_id()))
            if not deleted:
                observed_mutations.append(obs_mut)
    else:
        observed_mutations = [obs_mut for obs_mut in queryset]
    return observed_mutations


def gene_is_filtered(gene, ignored_genes):
    """Whether a mutation on `gene` is hidden by an ignore list.

    **Subset, not intersection**: every gene the mutation touches has to be on the list.
    Ignoring one gene of an intergenic pair ignores nothing, because the mutation is still
    partly about a gene nobody asked to hide.

    Extracted because this rule is applied in three places and there is no SQL for it --
    `filter_observed_mutations` walks model instances, `aledb_stats._count_in_python` walks
    tuples because it is counting rather than returning rows, and `aledb_converge` walks tuples
    because it only needs the gene and the ALE. Three transcriptions of a subset test is one
    too many; the shapes differ, the rule does not.
    """
    if not ignored_genes:
        return False
    genes = set(get_gene_list(gene))
    return len(ignored_genes) >= len(genes) and genes.issubset(ignored_genes)


def _get_exp_filter_genes(exp_filter: AleExperimentFilter):
    """One experiment's ignored genes.

    `_get_ignored_gene_list_from_str` used to live below this and was never called by anything
    -- `get_gene_list` from aledb_common is what actually parses the column. It went with the
    mutation-id helpers rather than being left as a second, unused parser of the same field.
    """
    if not exp_filter.ignored_genes:
        return set()
    return set(get_gene_list(exp_filter.ignored_genes))


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


def describe_filters(observed_mutation_queryset=None, experiment_id=None,
                     skip_experiment_filter=False):
    """What filtering shaped a page's rows, for the page to say out loud.

    Takes the same arguments as `filter_observed_mutations` and reads the same rows through
    `filters_in_play`, so the sentence and the exclusion cannot disagree about which filters
    apply. Returns:

        {'applied': bool,          anything at all is being hidden
         'skipped': bool,          the reader asked to see through it
         'cutoffs': [(min, max)],  the ranges in force, one per experiment
         'genes': [names],         ignored genes, deduplicated across experiments
         'experiments': int}       how many filters were consulted

    `applied` is the question a page actually asks, and it is **not** "is a filter
    configured". A filter of 0-100 with no ignored genes is configured and hides nothing, and
    saying "filtered" about it would train people to ignore the word. The two are different
    facts about a deployment and the summary distinguishes them.
    """
    filters = list(filters_in_play(observed_mutation_queryset, experiment_id,
                                   skip_experiment_filter))

    cutoffs = []
    genes = []
    for exp_filter in filters:
        low = exp_filter.min_cutoff or 0
        high = exp_filter.max_cutoff if exp_filter.max_cutoff is not None else 100
        if low > 0 or high < 100:
            pair = (low, high)
            if pair not in cutoffs:
                cutoffs.append(pair)
        for gene in _get_exp_filter_genes(exp_filter):
            if gene not in genes:
                genes.append(gene)

    return {
        "applied": bool(cutoffs or genes),
        "skipped": bool(skip_experiment_filter),
        "cutoffs": cutoffs,
        "genes": sorted(genes),
        "experiments": len(filters),
    }
