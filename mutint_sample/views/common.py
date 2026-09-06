import mutint_experiment.models
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_view_project
from mutint_common.constants import (REQUEST_EXPERIMENT_ID, REQUEST_POPULATION,
                                    REQUEST_ALL, REQUEST_SAMPLE_TYPE, SAMPLE_TYPES)
import logging

from mutint_common.logger import user_extra
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.template import loader


__author__ = 'Patrick Phaneuf'



# Re-exported so every existing importer keeps working. The vocabulary and the rule that reads it
# moved to `mutint_sample.functional_change`, which is a pure str -> str module: this one pulls in
# django.http, django.template and mutint_experiment.permissions at import time, and a rule the
# annotator's own tests might want should not require any of that.
#
# The names are still breseq's own, as the comment here always said. What changed is that they are
# `Mutation.snp_type`'s values, matched exactly, rather than substrings hunted in a rendered
# display string -- and that their *order* now carries severity. See that module for both.
from mutint_sample.functional_change import (  # noqa: F401  (re-export)
    FUNCTIONAL_CHANGE_TYPE_LIST, UNANNOTATED, functional_change_bucket,
)
from mutint_experiment import paths

MUTATION_TYPE_LIST = ['SNP', 'SUB', 'DEL', 'INS', 'MOB', 'AMP', 'CON', 'INV', UNANNOTATED]

#: What each type is called on a page. A real mapping between two vocabularies -- breseq's
#: three-letter codes and English -- so unlike the functional-change tokens it cannot be
#: derived, and it lives here because beside the list is the one place it cannot drift from
#: it. It was written out twice in templates (`dashboard.html` and `stats.html`) and the
#: dashboard's copy was a *column name* per row, which is what made adding `unannotated` to
#: the vocabulary a migration rather than a line.
MUTATION_TYPE_LABELS = {
    'SNP': 'Single Base Substitutions',
    'SUB': 'Multiple Base Substitutions',
    'DEL': 'Deletions',
    'INS': 'Insertions',
    'MOB': 'Mobile Element Insertions',
    'AMP': 'Amplifications',
    'CON': 'Gene Conversions',
    'INV': 'Inversions',
    UNANNOTATED: 'Unannotated',
}

# `GENE_COLORS`, `SEQ_COLORS`, `COLORS`, `DEFAULT_COLOR` and `_set_colors` stood here and are gone.
# They were read by exactly two context keys, `seq_color_set` and `protein_types`, which reached no
# template in core or in any plugin -- palettes for a chart that was never built. Their one
# remaining effect was that adding a token to the vocabulary silently reshuffled a color list
# nobody rendered, which is a trap laid for precisely the change that added `nonsense`.



def get_population_names(experiment_id):
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
    # Imported here rather than at module scope: `mutint_sample.util` reaches the filter layer,
    # and this module is imported by most of it.
    from mutint_sample.util import get_ordered_sample_queryset

    # `.order_by()` strips the sample ordering before the subquery. An ORDER BY left on a
    # queryset handed to `__in` adds its columns to the SELECT, which is an error on some
    # backends and silently wrong on others.
    from mutint_experiment.ordering import natural

    visible = get_ordered_sample_queryset(experiment_id).order_by().values("pk")
    # Ordered by the same natural sort a sample list uses. `Population.name` is text, so the
    # database's own order puts ALE 10 above ALE 2 -- and this dropdown had no `order_by` at
    # all, so it was whatever the join happened to produce.
    return (mutint_experiment.models.Population.objects
            .filter(**{paths.down_chain("population") + "__in": visible})
            .distinct()
            .order_by(natural("name"))
            .values_list("name", flat=True))


def get_population(request):
    """The ALE picked in the query string, or None for "all".

    No `int()` any more: `Population.name` is text (`mutint_experiment.0008`), and coercing
    would have raised a ValueError on the first lineage called `Ara-1`. An empty parameter
    reads as "all" too, so a cleared picker cannot filter to a nonexistent ALE.
    """
    population = request.GET.get(REQUEST_POPULATION)
    if population is None or population in ("", "all"):
        return None
    return population

logger = logging.getLogger(__name__)


def get_sample_type(request):
    """The `?sample_type=` filter, or None for "all".

    **An unrecognized value answers None rather than passing through**, and that is a fix
    rather than politeness. `get_ordered_sample_queryset` used to read anything that was not
    the population token as clonal, so `?sample_type=anything` quietly showed half the
    samples with the picker still reading "All sample types" -- a page that is subset and
    says it is not. Answering None makes the rows and the control agree.

    It matters more than it did: the accepted values are about to change, so every existing
    link carrying the old one arrives here.
    """
    sample_type = request.GET.get(REQUEST_SAMPLE_TYPE)
    if sample_type in (None, "", REQUEST_ALL):
        return None
    if sample_type not in SAMPLE_TYPES:
        logger.warning("ignoring unrecognized sample_type %r", sample_type)
        return None
    return sample_type

def get_experiment(request):
    """
    Parse experiment id and validate permission
    :param request:
    :return: experiment or raise exception
    """
    exp_id = request.GET.get(REQUEST_EXPERIMENT_ID)
    experiment = Experiment.objects.get(pk=exp_id)
    if experiment:
        if can_view_project(request.user, experiment.project):
            return experiment
    raise ValueError("You don't have permission to view the experiment")


def no_experiment_selected(request, context, logger, what):
    """Render the "pick an experiment first" page.

    Every experiment-scoped page reaches `get_experiment` with whatever is in
    `?experiment_id`, so opening one without a usable id raises DoesNotExist.
    That is how these pages open, not a breakage: caught by a view's catch-all it
    logs an ERROR-level traceback and shows the reader Django's raw "Experiment
    matching query does not exist". Catch it ahead of the catch-all instead and
    hand it here, so a genuine ERROR in the log still means something is wrong.

    `logger` is the calling view's, so the record still names the page it came from.
    """
    logger.info("%s with no experiment selected" % what, extra=user_extra(request))
    context["err_message"] = "Select an experiment to see its %s." % what
    template = loader.get_template("500.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


# `filter_out_wt_sample` and `get_wt_sample_id` stood here. They were the ancestor subtraction
# that was meant to happen and never did -- neither had a single caller anywhere in the suite,
# and both compared against `STARTING_STRAIN_ALE_ID`. What they were reaching for is
# `mutint_experiment/ancestor.py`, which subtracts a designated sample rather than guessing
# from an ALE label.
