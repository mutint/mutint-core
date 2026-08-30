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


def get_aleid_ale_id_list(experiment_id):
    """The ALE labels the picker should offer: those that still have a visible sample.

    **This used to exclude the literal string "0".** `STARTING_STRAIN_ALE_ID` was the
    convention that ALE 0 held the starting strain, and four pages passed
    `exclude_starting_strain=True` to keep it out of the dropdown -- while its samples went
    on landing in every analysis the moment no ALE was picked, which is the bug that
    convention actually had.

    The question the picker is asking is "which ALEs can I show you", so it is answered from
    the samples rather than from a magic label. An ALE holding nothing but the designated
    ancestor now disappears on its own, and one legitimately called "0" stays.
    """
    # Imported here rather than at module scope: `aledb_seq.util` reaches the filter layer,
    # and this module is imported by most of it.
    from aledb_seq.util import get_ordered_reseq_queryset

    # `.order_by()` strips the sample ordering before the subquery. An ORDER BY left on a
    # queryset handed to `__in` adds its columns to the SELECT, which is an error on some
    # backends and silently wrong on others.
    visible = get_ordered_reseq_queryset(experiment_id).order_by().values("pk")
    return (aledb_experiment.models.AleId.objects
            .filter(flask__isolate__technicalreplicate__resequencingexperiment__in=visible)
            .distinct()
            .values_list("ale_id", flat=True))


def get_ale_id(request):
    """The ALE picked in the query string, or None for "all".

    No `int()` any more: `AleId.ale_id` is text (`aledb_experiment.0008`), and coercing
    would have raised a ValueError on the first lineage called `Ara-1`. An empty parameter
    reads as "all" too, so a cleared picker cannot filter to a nonexistent ALE.
    """
    ale_id = request.GET.get(REQUEST_ALE_ID)
    if ale_id is None or ale_id in ("", "all"):
        return None
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


# `filter_out_wt_reseq` and `get_wt_reseq_id` stood here. They were the ancestor subtraction
# that was meant to happen and never did -- neither had a single caller anywhere in the suite,
# and both compared against `STARTING_STRAIN_ALE_ID`. What they were reaching for is
# `aledb_experiment/ancestor.py`, which subtracts a designated sample rather than guessing
# from an ALE label.
