from django.http import HttpResponse
from django.template import loader
from aledb_filter.forms.filter import FilterForm
from aledb_filter.util import get_ignored_mut_id_list_from_str, get_global_filter
from aledb_common.util import get_user_context
from aledb_common.rebuild_registry import request_rebuild
from aledb_seq.util import get_mutation_objects
from aledb_common.logger import user_extra
from aledb_experiment import permissions
import logging

__author__ = 'Denny Gosting, Patrick Phaneuf'

GLOBAL_FILTER_TEMPLATE = "filter/global_filter.html"

logger = logging.getLogger(__name__)


def global_filter(request):
    logger.info("global filter usage", extra=user_extra(request))
    try:
        template = loader.get_template(GLOBAL_FILTER_TEMPLATE)

        filter_form_model = get_global_filter()

        if request.method == 'POST':
            _handle_post(request, filter_form_model)
            # Every experiment, because every experiment's mutation counts are computed
            # through this filter. Marked, never rebuilt here: recomputing the whole
            # installation inside the request that edited a form is exactly the case
            # marking exists for. Each page rebuilds its own on next view.
            request_rebuild(reason='global filter changed')

        initial_filter_form_data = {"ignored_genes": filter_form_model.ignored_genes}

        filter_form = FilterForm(initial=initial_filter_form_data)

        ignored_mutations = get_mutation_objects(filter_form_model.ignored_mutations)

        context = get_user_context(request.user)
        context.update({
            "form": filter_form,
            "ignored_mutations": ignored_mutations})

        return HttpResponse(template.render(context, request), content_type="text/html")

    except Exception as e:
        logger.exception("global filter broke", extra=user_extra(request))
        template = loader.get_template("500.html")
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")


def _handle_post(request, filter_form_model):
    if permissions.can_add_global_filter(request.user):
        filter_form_model.ignored_genes = request.POST.get("ignored_genes", "")
        deleted_mut_id = request.POST.get('deleted_mut_id', None)
        ignored_mutation_id_list = get_ignored_mut_id_list_from_str(get_global_filter().ignored_mutations, deleted_mut_id)
        cleaned_list = get_ignored_mut_id_list_from_str(",".join(ignored_mutation_id_list))
        filter_form_model.ignored_mutations = ",".join(cleaned_list)
        filter_form_model.save()
    else:
        raise Exception("User doesn't have permission to edit global filter")

