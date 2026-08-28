import aledb_experiment.common
import aledb_experiment.models
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_view_project
from aledb_common.constants import REQUEST_ALE_EXPERIMENT_ID, REQUEST_ALE_ID, REQUEST_SAMPLE_TYPE
from aledb_common.logger import user_extra
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.template import loader


__author__ = 'Patrick Phaneuf'



# Re-exported so every existing importer keeps working. The vocabulary and the rule that reads it
# moved to `aledb_seq.functional_change`, which is a pure str -> str module: this one pulls in
# django.http, django.template and aledb_experiment.permissions at import time, and a rule the
# annotator's own tests might want should not require any of that.
#
# The names are still breseq's own, as the comment here always said. What changed is that they are
# `Mutation.snp_type`'s values, matched exactly, rather than substrings hunted in a rendered
# display string -- and that their *order* now carries severity. See that module for both.
from aledb_seq.functional_change import (  # noqa: F401  (re-export)
    FUNCTIONAL_CHANGE_TYPE_LIST, UNANNOTATED, functional_change_bucket,
)

MUTATION_TYPE_LIST = ['SNP', 'SUB', 'DEL', 'INS', 'MOB', 'AMP', 'CON', 'INV', UNANNOTATED]

# `GENE_COLORS`, `SEQ_COLORS`, `COLORS`, `DEFAULT_COLOR` and `_set_colors` stood here and are gone.
# They were read by exactly two context keys, `seq_color_set` and `protein_types`, which reached no
# template in core or in any plugin -- palettes for a chart that was never built. Their one
# remaining effect was that adding a token to the vocabulary silently reshuffled a colour list
# nobody rendered, which is a trap laid for precisely the change that added `nonsense`.

# TODO: change all instance of 'seq_experiment' to 'reseq'


def get_aleid_ale_id_list(experiment_id, exclude_starting_strain=False):
    if experiment_id:
        aleid_queryset = aledb_experiment.models.AleId.objects.filter(ale_experiment__ale_id=experiment_id)
    else:
        aleid_queryset = aledb_experiment.models.AleId.objects.all()

    if exclude_starting_strain:
        aleid_queryset = aleid_queryset.exclude(ale_id=aledb_experiment.common.STARTING_STRAIN_ALE_ID)
    return aleid_queryset.values_list("ale_id", flat=True)


def get_ale_id(request):
    ale_id = request.GET.get(REQUEST_ALE_ID)
    ale_id = None if ale_id is None or ale_id == "all" else int(ale_id)
    return ale_id

def get_sample_type(request):
    sample_type = request.GET.get(REQUEST_SAMPLE_TYPE)
    if sample_type == "all":
        sample_type = None
    return sample_type

def get_ale_experiment(request):
    """
    Parse experiment id and validate permission
    :param request:
    :return: experiment or raise exception
    """
    exp_id = request.GET.get(REQUEST_ALE_EXPERIMENT_ID)
    experiment = AleExperiment.objects.get(ale_id=exp_id)
    if experiment:
        if can_view_project(request.user, experiment.project):
            return experiment
    raise ValueError("You don't have permission to view the experiment")


def no_experiment_selected(request, context, logger, what):
    """Render the "pick an experiment first" page.

    Every experiment-scoped page reaches `get_ale_experiment` with whatever is in
    `?ale_experiment_id`, so opening one without a usable id raises DoesNotExist.
    That is how these pages open, not a breakage: caught by a view's catch-all it
    logs an ERROR-level traceback and shows the reader Django's raw "AleExperiment
    matching query does not exist". Catch it ahead of the catch-all instead and
    hand it here, so a genuine ERROR in the log still means something is wrong.

    `logger` is the calling view's, so the record still names the page it came from.
    """
    logger.info("%s with no experiment selected" % what, extra=user_extra(request))
    context["err_message"] = "Select an experiment to see its %s." % what
    template = loader.get_template("500.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


def get_ale_experiment_name(request):

    ale_experiment_id = request.GET.get(REQUEST_ALE_EXPERIMENT_ID)

    ale_experiment_name = "All ALE Experiments"

    if ale_experiment_id is not None and ale_experiment_id != "all":

        ale_experiment = aledb_experiment.models.AleExperiment.objects.filter(ale_id=ale_experiment_id)

        # TODO: should only ever be returning 1 experiment. Implement error handling for more than one returned.
        ale_experiment_name = ale_experiment[0].name

    return ale_experiment_name


def filter_out_wt_reseq(reseq_ordered_dict):
    for key, value in reseq_ordered_dict.items():
        if value.ale_id == aledb_experiment.common.STARTING_STRAIN_ALE_ID:
            del reseq_ordered_dict[key]
            break
    return reseq_ordered_dict


def get_wt_reseq_id(seq_experiment_ordered_dict):

    wt_id = None

    for key, value in seq_experiment_ordered_dict.items():

        if value.ale_id == aledb_experiment.common.STARTING_STRAIN_ALE_ID:

            wt_id = key

    return wt_id
