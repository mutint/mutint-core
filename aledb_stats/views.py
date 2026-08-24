import time
from django.http import HttpResponse, Http404, HttpResponseForbidden
from django.template import loader
from django.utils.safestring import mark_safe
from django.conf import settings
from aledb_seq.util import get_ordered_reseq_queryset
import aledb_seq.views.common
from aledb_seq.views import common
from aledb_stats.util import get_needle_plot_data,\
    get_mutation_type_count_dict, \
    get_observed_mutation_type_count_dict,\
    get_protein_change_type_count_dict,\
    get_observed_protein_change_type_count_dict,\
    get_ale_flask_isolate_count_list,\
    get_reseq_experiment_info_list
from aledb_common.util import get_user_context
from aledb_stats.util import get_observed_mutation_list
import logging
from aledb_common.context_registry import get_experiment_context
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_edit_project
from aledb_common.logger import user_extra, join_extras

logger = logging.getLogger(__name__)

__author__ = 'pphaneuf'
STATS_TEMPLATE = "stats.html"


# TODO: used by multiple views. Also implemented within ale_exp_filter.py; implement in one location.


def stats(request):
    logger.info("stats usage", extra=user_extra(request))
    try:
        start_time = time.time()
        context = get_user_context(request.user)
        experiment = common.get_ale_experiment(request)
        if experiment:
            context.update(experiment.experiment_context())
            context.update(get_experiment_context(experiment))

        experiment = aledb_seq.views.common.get_ale_experiment(request)
        exp_name = experiment.name
        ale_experiment_id = experiment.ale_id
        ale_number = aledb_seq.views.common.get_ale_id(request)

        ale_id = common.get_ale_id(request)
        reseq_queryset = get_ordered_reseq_queryset(experiment.ale_id, ale_id)
        ale_flask_isolate_count_list = get_ale_flask_isolate_count_list(reseq_queryset)
        ale_sum = len(ale_flask_isolate_count_list)
        flask_sum = 0
        isolate_sum = 0
        for l in ale_flask_isolate_count_list:
            flask_sum += l[1]
            isolate_sum += l[2]

        experiments_info_list = get_reseq_experiment_info_list(reseq_queryset)
        obs_mutations = get_observed_mutation_list(experiment.ale_id)
        mutations_dict = {obs_mut.mutation.id: obs_mut.mutation for obs_mut in obs_mutations}
        mutations = mutations_dict.values()
        mutation_type_count_dict = get_mutation_type_count_dict(mutations)
        observed_mutation_type_count_dict = get_observed_mutation_type_count_dict(obs_mutations)
        protein_change_type_count_dict = get_protein_change_type_count_dict(mutations)
        observed_protein_change_type_count_dict = get_observed_protein_change_type_count_dict(obs_mutations)
        template = loader.get_template(STATS_TEMPLATE)

        needle_plot_data = get_needle_plot_data(experiment.ale_id)
        context.update({"ale_experiment_name": exp_name,
                        "ale_no": ale_number,
                        "ale_experiment_id": ale_experiment_id,
                        "ale_project_name": experiment.project.name,
                        "ale_project_id": experiment.project.id,
                        "protein_change_type_count_dict": protein_change_type_count_dict,
                        "protein_change_sum": sum(protein_change_type_count_dict.values()),
                        "observed_protein_change_type_count_dict": observed_protein_change_type_count_dict,
                        "observed_protein_change_sum": sum(observed_protein_change_type_count_dict.values()),
                        "mutation_type_count_dict": mutation_type_count_dict,
                        "mutation_sum": sum(mutation_type_count_dict.values()),
                        "observed_mutation_type_count_dict": observed_mutation_type_count_dict,
                        "observed_mutation_sum": sum(observed_mutation_type_count_dict.values()),
                        "experiments_info_list": experiments_info_list,
                        "needle_plot_data": mark_safe(list(needle_plot_data)),
                        "seq_color_set": mark_safe(common.SEQ_COLORS),
                        "protein_types": mark_safe(common.FUNCTIONAL_CHANGE_TYPE_LIST),
                        "ale_flask_isolate_count_list": ale_flask_isolate_count_list,
                        "ale_sum": ale_sum,
                        "flask_sum": flask_sum,
                        "isolate_sum": isolate_sum,
                        "notes": experiment.notes,
                        # Gates the whole experiment_actions block. Delete used to render
                        # for everyone -- the POST refused it, so it was a dead end
                        # dressed as an action rather than a hole, but a dead end all the
                        # same. Add and Edit now come and go with it.
                        "can_edit": can_edit_project(request.user, experiment.project),
                        })

        logger.info("stats performance",
                             extra=join_extras(user_extra(request), {"time taken": time.time() - start_time}))

        return HttpResponse(template.render(context, request), content_type="text/html")

    except AleExperiment.DoesNotExist:
        return common.no_experiment_selected(
            request, get_user_context(request.user), logger, "statistics")
    except Exception as e:
        logger.exception("stats broke", extra=user_extra(request))
        template = loader.get_template("500.html")
        context = get_user_context(request.user)
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")

