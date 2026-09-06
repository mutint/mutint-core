import time
from django.http import HttpResponse, Http404, HttpResponseForbidden
from django.template import loader
from django.utils.safestring import mark_safe
from django.conf import settings
from mutint_sample.util import get_ordered_sample_queryset
from mutint_sample.views import common
from mutint_stats.util import count_per_population,\
    get_experiment_summary,\
    get_sample_info_list
from mutint_common.util import get_user_context
import logging
from mutint_common.context_registry import get_experiment_context
from mutint_common.panel_registry import render_overview_panels
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment, can_lock_experiment
from mutint_common.logger import user_extra, join_extras

logger = logging.getLogger(__name__)

__author__ = 'pphaneuf'
STATS_TEMPLATE = "stats.html"




def stats(request):
    logger.info("stats usage", extra=user_extra(request))
    try:
        start_time = time.time()
        context = get_user_context(request.user)
        experiment = common.get_experiment(request)
        if experiment:
            context.update(experiment.experiment_context())
            context.update(get_experiment_context(experiment))

        # This used to fetch the experiment a second time here. The file imported the same
        # module twice under two names -- `import mutint_sample.views.common` alongside
        # `from mutint_sample.views import common` -- which made the second call look like a
        # different one, so every view of this page paid for a second Experiment query, a
        # second project FK query and a second permission check, then discarded the object
        # the lines above had already fetched. The duplicate import is gone with it.
        exp_name = experiment.name
        experiment_id=experiment.id
        population = common.get_population(request)

        population = population
        sample_queryset = get_ordered_sample_queryset(experiment.id, population)
        population_counts = count_per_population(sample_queryset)
        population_count = len(population_counts)
        time_point_count = 0
        sample_count = 0
        for l in population_counts:
            time_point_count += l[1]
            sample_count += l[2]

        sample_info_list = get_sample_info_list(sample_queryset)

        # One row, not every MutationCall in the experiment. `get_experiment_summary`
        # rebuilds it first if anything has marked it stale, so the first view after an
        # import, a sample renumber or a filter change pays for the recomputation and every
        # view after it does not. See mutint_common/rebuild_registry.py.
        summary = get_experiment_summary(experiment.id)
        mutation_type_count_dict = summary.mutation_type_counts
        call_type_count_dict = summary.call_type_counts
        protein_change_type_count_dict = summary.protein_change_counts
        call_protein_change_type_count_dict = summary.call_protein_change_counts
        template = loader.get_template(STATS_TEMPLATE)

        # Whatever the installed components put on this page under what it owns itself.
        # The needle plot was the first, and lived in this app until it became one: it is
        # `mutint-needle` now, and a deployment without that component simply has no such
        # section. See mutint_common/panel_registry.py.
        panels = render_overview_panels(experiment, request)
        context.update({"experiment_name": exp_name,
                        "population": population,
                        "experiment_id": experiment_id,
                        "project_name": experiment.project.name,
                        "project_id": experiment.project.id,
                        "protein_change_type_count_dict": protein_change_type_count_dict,
                        "protein_change_sum": sum(protein_change_type_count_dict.values()),
                        "call_protein_change_type_count_dict": call_protein_change_type_count_dict,
                        "call_protein_change_sum": sum(call_protein_change_type_count_dict.values()),
                        "mutation_type_count_dict": mutation_type_count_dict,
                        "mutation_sum": sum(mutation_type_count_dict.values()),
                        "call_type_count_dict": call_type_count_dict,
                        "mutation_call_sum": sum(call_type_count_dict.values()),
                        "sample_info_list": sample_info_list,
                        "panels": panels,
                        # `seq_color_set` and `protein_types` stood here and are gone with the
                        # color machinery in mutint_sample.views.common: a palette and a vocabulary
                        # for a chart that was never built, and which no template has ever read.
                        # The functional-change counts below are rendered as a table instead.
                        "population_counts": population_counts,
                        "population_count": population_count,
                        "time_point_count": time_point_count,
                        "sample_count": sample_count,
                        "notes": experiment.notes,
                        # Gates the whole experiment_actions block. Delete used to render
                        # for everyone -- the POST refused it, so it was a dead end
                        # dressed as an action rather than a hole, but a dead end all the
                        # same. Add and Edit now come and go with it.
                        # `can_edit_experiment`, so the Add/Edit/Delete block also
                        # disappears while the experiment is locked -- every endpoint behind
                        # those buttons refuses a locked experiment, and an action that can
                        # only produce a refusal is the dead end this comment already
                        # describes.
                        "can_edit": can_edit_experiment(request.user, experiment),
                        # Separate, and admin-level: the whole point of the lock is that the
                        # people who ordinarily edit cannot lift it themselves.
                        "can_lock": can_lock_experiment(request.user, experiment),
                        "experiment": experiment,
                        })

        # Rendered before the log, not after: the sample table used to issue two queries per
        # sample while the template ran, so a timing taken above this line reported a page
        # that had not finished being built. Those queries are gone, and this stays below the
        # render so the number keeps meaning what it says.
        rendered = template.render(context, request)
        logger.info("stats performance",
                             extra=join_extras(user_extra(request), {"time taken": time.time() - start_time}))

        return HttpResponse(rendered, content_type="text/html")

    except Experiment.DoesNotExist:
        return common.no_experiment_selected(
            request, get_user_context(request.user), logger, "statistics")
    except Exception as e:
        logger.exception("stats broke", extra=user_extra(request))
        template = loader.get_template("500.html")
        context = get_user_context(request.user)
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")

