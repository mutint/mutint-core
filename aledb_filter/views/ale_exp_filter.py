from django.http import HttpResponse
from django.template import loader
from aledb_experiment.models import AleExperiment
from aledb_seq.views import common
from aledb_filter.forms.filter import FilterForm
from aledb_filter.models import AleExperimentFilter
import aledb_filter.models
from aledb_filter.common import DEFAULT_MUTATION_FREQ_MIN, DEFAULT_MUTATION_FREQ_MAX
from aledb_common.util import get_user_context
from aledb_common.rebuild_registry import INPUT_FILTERS, request_rebuild
from aledb_common.logger import user_extra
from aledb_experiment import permissions
import logging

logger = logging.getLogger(__name__)

__author__ = 'Denny Gosting, Patrick Phaneuf'

FILTER_TEMPLATE = "filter/experiment_filter.html"


def mutation_filter(request):
    logger.info("mutation filter", extra=user_extra(request))
    try:
        context = get_user_context(request.user)
        experiment = common.get_ale_experiment(request)

        template = loader.get_template(FILTER_TEMPLATE)

        filter_form_model, created = AleExperimentFilter.objects.get_or_create(
            ale_experiment=experiment,
            defaults=aledb_filter.models.get_default_experiment_filter_params(experiment))

        if request.method == 'POST':
            # check user permissions
            _handle_POST(request, filter_form_model, experiment)
            # After the save, not before: this marks what the save just invalidated, and
            # marking first would let a rebuild racing between the two clear it again.
            # `changed=`, so this marks what actually reads through the filter. A tree
            # inferred from unfiltered mutations is not invalidated by a cutoff, and marking
            # it would hide it behind a warning asking for a rebuild that would redraw the
            # identical topology.
            request_rebuild(experiment.ale_id, changed=INPUT_FILTERS,
                            reason='experiment filter changed')

        filter_form = FilterForm(filter_form_model.__dict__)

        context.update({
            "form": filter_form,
            "experiment": experiment,
            "ale_experiment_id": experiment.ale_id,
            "ale_experiment_name": experiment.name,
            "ale_project_name": experiment.project.name,
            "ale_project_id": experiment.project.id,
            # The same predicate `_handle_POST` enforces. The template used to ask Django's
            # `perms.filter.add_globalfilter`, which nothing in this codebase grants.
            "can_edit": permissions.can_add_experiment_filter(request.user, experiment)})
        return HttpResponse(template.render(context, request), content_type="text/html")
    except AleExperiment.DoesNotExist:
        return common.no_experiment_selected(request, context, logger, "filter settings")
    except Exception as e:
        logger.exception("mutation filter broke", extra=user_extra(request))
        template = loader.get_template("500.html")
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")


def _handle_POST(request, filter_form_model, experiment):
    filter_form = FilterForm(request.POST)
    if permissions.can_add_experiment_filter(request.user, experiment) and filter_form.is_valid():
        filter_form_model.min_cutoff = request.POST.get("min_cutoff", DEFAULT_MUTATION_FREQ_MIN)
        filter_form_model.max_cutoff = request.POST.get("max_cutoff", DEFAULT_MUTATION_FREQ_MAX)
        filter_form_model.ignored_genes = request.POST.get("ignored_genes", "")
        # The three lines that used to follow read the page's `deleted_mut_id` accumulator
        # back into `ignored_mutations`. That column is gone; removing a mutation is
        # aledb_mutation_editor's job, and it removes the row rather than hiding it.
        filter_form_model.save()
    elif filter_form.is_valid():
        raise Exception("User doesn't have permission to edit experiment filter")
    else:
        print(filter_form.errors)
