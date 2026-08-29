import collections
import aledb_seq.models
from aledb_common.util import is_int
from aledb_experiment.ordering import sample_order
from aledb_filter.util import filter_observed_mutations

HTML_ECOCYC = """<a href = "https://ecocyc.org/ECOLI/substring-search?type=GENE&object={gene}">{gene}</a>"""


def get_observed_mutation_queryset(experiment_id):
    return aledb_seq.models.ObservedMutation.objects.filter(sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=experiment_id)


def get_all_observed_mutations_filtered(experiment_id, *, filter_type=None, view_filter=None):
    """An experiment's observations, through the reader's filter.

    `view_filter` comes from `aledb_filter.view_filter.get_view_filter(request, experiment_id)`
    and is the reader's own; None means unfiltered. It replaced `skip_experiment_filter`, which
    asked to see through a *shared* filter -- a question that stops meaning anything once the
    filter is yours to clear.
    """
    queryset = get_observed_mutation_queryset(experiment_id)
    return filter_observed_mutations(queryset, filter_type=filter_type, view_filter=view_filter)


def get_all_observed_mutations(reseq_id_list):
    return aledb_seq.models.ObservedMutation.objects.filter(sequencing_experiment_id__in=reseq_id_list)


def get_ordered_reseq_queryset(ale_experiment_id, ale_id=None, sample_type=None):
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
    return reseq_qryset


def get_reseq_ordered_dict(ale_experiment_id, ale_no=None, sample_type=None, request=None):
    """
    Args:
        ale_experiment_id:
        ale_no:
        sample_type: population sample

    Returns:
        reseq_ordered_dict: a ordered dictionary of reseq values and their ID's as keys.
        The reseq values within the dictionary will be ordered according to that
        defined within RESEQ_QUERY
        :param request:

    """
    reseq_queryset = get_ordered_reseq_queryset(ale_experiment_id, ale_no, sample_type)
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
