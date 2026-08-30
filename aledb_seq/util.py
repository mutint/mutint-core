import collections
import aledb_seq.models
from aledb_common.util import is_int
from aledb_experiment.ordering import sample_order
from aledb_filter.util import filter_observed_mutations

HTML_ECOCYC = """<a href = "https://ecocyc.org/ECOLI/substring-search?type=GENE&object={gene}">{gene}</a>"""


def get_observed_mutation_queryset(experiment_id):
    """Every observation this experiment holds -- what is *stored*, ancestor included.

    **Usually not what you want.** An experiment may designate an ancestor, whose mutations
    are the starting line rather than evolution; anything analysing or summarising the data
    wants `get_evolved_observation_queryset` below. This raw form is for the three places
    that mean "what is stored": the CSV export, the mutation editor, and the per-sample
    breseq page, which tints ancestral rows rather than hiding them.
    """
    return aledb_seq.models.ObservedMutation.objects.filter(sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=experiment_id)


def get_evolved_observation_queryset(experiment_id):
    """This experiment's observations with its designated ancestor subtracted.

    The default choice for anything that analyses or counts. See
    `aledb_experiment/ancestor.py` for what subtraction means and why it is not the reader's
    filter. With no ancestor designated this is `get_observed_mutation_queryset` exactly.
    """
    from aledb_experiment.ancestor import exclude_ancestry
    return exclude_ancestry(get_observed_mutation_queryset(experiment_id), experiment_id)


def get_all_observed_mutations_filtered(experiment_id, *, filter_type=None, view_filter=None):
    """An experiment's observations, through the reader's filter.

    `view_filter` comes from `aledb_filter.view_filter.get_view_filter(request, experiment_id)`
    and is the reader's own; None means unfiltered. It replaced `skip_experiment_filter`, which
    asked to see through a *shared* filter -- a question that stops meaning anything once the
    filter is yours to clear.
    """
    queryset = get_evolved_observation_queryset(experiment_id)
    return filter_observed_mutations(queryset, filter_type=filter_type, view_filter=view_filter)


def observations_for_samples(reseq_id_list, experiment_id):
    """Observations in these samples, with the experiment's designated ancestor subtracted.

    The entry point for a plugin that derives something. It was `get_all_observed_mutations`,
    which did the `filter` half and had no callers left -- aledb-compare, aledb-converge,
    aledb-fixation and aledb-phylogeny had each written that one line out by hand instead.

    That hand-copying is exactly what this fixes. Dropping the ancestor from a *sample* list
    removes its column from a table but leaves its mutations sitting in every other sample --
    and because an ancestral mutation is present in every ALE, convergence would report all of
    them as convergent and fixation all of them as fixed. **The subtraction has to reach the
    derivation, not the render**, the same rule `docs/plugin/filtering.md` states for the
    reader's filter and for the same reason.
    """
    from aledb_experiment.ancestor import exclude_ancestry
    queryset = aledb_seq.models.ObservedMutation.objects.filter(
        sequencing_experiment_id__in=reseq_id_list)
    return exclude_ancestry(queryset, experiment_id)


def get_ordered_reseq_queryset(ale_experiment_id, ale_id=None, sample_type=None, *,
                               include_ancestor=False):
    """An experiment's samples in A/F/I/R order, without its designated ancestor.

    **The ancestor is excluded by default**, and the few callers that curate rather than
    read pass `include_ancestor=True`: the Edit-samples page and the mutation editor, which
    must still be able to see and change it.

    Defaulting this way round is deliberate, and it is the opposite of what
    `get_observed_mutation_queryset` does. The two mistakes are not symmetric. Forgetting to
    opt *in* hides the ancestor from a curation page, which is visible and gets reported the
    same day; forgetting to opt *out* leaves ancestral data in an analysis, which is
    invisible and wrong. It also means aledb-compare, aledb-converge and aledb-fixation need
    no edit here at all.

    Keyword-only: this module already carries a scar from `get_reseq_ordered_dict` being
    called with a `request` in the `sample_type` slot, which silently dropped every
    population sample from two plugin pages.
    """
    reseq_qryset = aledb_seq.models.ResequencingExperiment.objects.select_related(
        'tech_rep__isolate__flask__ale_id__ale_experiment', 'tech_rep__isolate__flask__media'
    ).order_by(*sample_order())
    if ale_experiment_id:
        reseq_qryset = reseq_qryset.filter(tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=ale_experiment_id)
    # `is not None`, not truthiness: the starting strain's ALE is "0"
    # (`common.STARTING_STRAIN_ALE_ID`), which was falsy while this column held integers and
    # so quietly selected every ALE instead of that one.
    if ale_id is not None and ale_id != "":
        reseq_qryset = reseq_qryset.filter(tech_rep__isolate__flask__ale_id__ale_id=ale_id)
    if sample_type:
        flag = 0
        if sample_type == 'population':
            flag = 1
        reseq_qryset = reseq_qryset.filter(tech_rep__isolate__is_population=flag)
    if not include_ancestor:
        from aledb_experiment.ancestor import exclude_ancestor_samples
        reseq_qryset = exclude_ancestor_samples(reseq_qryset, ale_experiment_id)
    return reseq_qryset


def get_reseq_ordered_dict(ale_experiment_id, ale_no=None, sample_type=None, request=None,
                           *, include_ancestor=False):
    """
    Args:
        ale_experiment_id:
        ale_no:
        sample_type: population sample
        include_ancestor: keep the designated ancestor, for a page that curates rather
            than reads. See `get_ordered_reseq_queryset`, which this wraps.

    Returns:
        reseq_ordered_dict: a ordered dictionary of reseq values and their ID's as keys.
        The reseq values within the dictionary will be ordered according to that
        defined within RESEQ_QUERY
        :param request:

    """
    reseq_queryset = get_ordered_reseq_queryset(ale_experiment_id, ale_no, sample_type,
                                                include_ancestor=include_ancestor)
    if request and request.GET.get('tag_select'):
        tag = request.GET.get('tag_select').split(':')
        if tag[0] == 'Hide Tag':
            reseq_queryset = reseq_queryset.exclude(tech_rep__tags__contains=tag[1].replace(' ', ''))
        elif tag[0] == 'Show Tag':
            reseq_queryset = reseq_queryset.filter(tech_rep__tags__contains=tag[1].replace(' ', ''))
    reseq_ordered_dict = collections.OrderedDict((reseq.id, reseq) for reseq in reseq_queryset)
    return reseq_ordered_dict


def get_mutations_from_observed_muations(observed_mutations):
    mut_map = {obs_mut.mutation.id: obs_mut.mutation for obs_mut in observed_mutations}
    return mut_map.values()


def get_ordered_reseq_dict(observed_mutations):
    """
    Get reseq {id: reseq} map
    :param observed_mutations:
    :return: ordered map
    """
    seq_experiment_ordered_dict = collections.OrderedDict()
    for observed_mutation in observed_mutations:
        seq_experiment_ordered_dict[observed_mutation.sequencing_experiment.id] = observed_mutation.sequencing_experiment
    return seq_experiment_ordered_dict


def get_ecocyc_gene_list(gene_list, is_ecocyc_gene: bool = False):
    url_list = []
    for each in gene_list:
        if each.startswith("<"):
            each = each.split(">")[-1]
        if is_ecocyc_gene:
            url_list.append(HTML_ECOCYC.format(gene=each))
        else:
            url_list.append(each)
    return url_list


def get_ref_sequences():
    return sorted(
        aledb_seq.models.Mutation.objects.exclude(reseq_reference__isnull=True).exclude(reseq_reference='')
        .values_list('reseq_reference', flat=True).distinct()
    )


def get_matching_observed_mutation_ids(mutation_id, experiment_id):
    local_observed_mutations = aledb_seq.models.ObservedMutation.objects.filter(
        sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=experiment_id,
        mutation__id=mutation_id).order_by(*sample_order("sequencing_experiment__"))
    matching_observed_mutation_ids = []
    for local_observed_mutation in local_observed_mutations:
        matching_observed_mutation_ids.append(local_observed_mutation.id)
    return matching_observed_mutation_ids
