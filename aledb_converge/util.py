from aledb_seq.util import get_all_observed_mutations, get_reseq_ordered_dict
from aledb_seq.models import ObservedMutation
from aledb_converge.models import ConvergeMutation
from aledb_converge import converge
from django.db.models import Q



__author__ = "Patrick Phaneuf"


# TODO optimize by only using ORM for finding converge mutations.
def get_converge_mutation_list(ale_experiment_id):
    reseq_dict = get_reseq_ordered_dict(ale_experiment_id)
    ale_exp_reseq_obs_mut_qryset_list = []
    for reseq_id in reseq_dict:
        ale_exp_reseq_obs_mut_qryset_list.append(get_all_observed_mutations([reseq_id]))
    converge_mutation_list = converge.get_converge_mutation_list(ale_exp_reseq_obs_mut_qryset_list)
    return converge_mutation_list


def get_converge_obs_mut_qryset(experiment_id):
    converge_mut_qryset = ConvergeMutation.objects.filter(ale_experiment_id=experiment_id)
    q_query = Q(mutation__id__in=converge_mut_qryset.values('mutation_id'))
    q_query.add(Q(sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=experiment_id), Q.AND)
    return ObservedMutation.objects.filter(q_query)
