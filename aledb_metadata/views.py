import time

from django.http import HttpResponse

from django.template import loader

from django.conf import settings
from aledb_experiment.models import AleExperiment
from aledb_seq.views import common
from aledb_common.util import get_user_context
from aledb_seq.util import get_ordered_reseq_queryset

from aledb_common.constants import (REQUEST_ALE_EXPERIMENT_ID, REQUEST_ALE_ID,
                                    SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED)
from aledb_common.logger import user_extra, join_extras
import logging

logger = logging.getLogger(__name__)

__author__ = 'Patrick Phaneuf'

# TODO: use the template location described within settings.py
META_DATA_TEMPLATE = "metadata/index.html"


# TODO: used by multiple views. Also implemented within ale_exp_filter.py; implement in one location.


def metadata(request):
    logger.info("metadata usage", extra=user_extra(request))

    try:
        start_time = time.time()
        context = get_user_context(request.user)
        experiment = common.get_ale_experiment(request)
        ale_experiment_id = experiment.id
        ale_id = request.GET.get(REQUEST_ALE_ID)

        reseq_queryset = get_ordered_reseq_queryset(ale_experiment_id, ale_id)

        reseq_info_list = get_reseq_info_list(reseq_queryset)

        context = get_user_context(request.user)
        context.update({"reseq_info_list": reseq_info_list,
                        "ale_experiment_name": experiment.name,
                        "ale_project_name": experiment.project.name,
                        "ale_project_id": experiment.project.id,
                        "multiple": False,
                        "ale_experiment_id": ale_experiment_id
                        })

        template = loader.get_template(META_DATA_TEMPLATE)
        logger.info("metadata performance", extra=join_extras(user_extra(request), {"time taken": time.time() - start_time}))
        return HttpResponse(template.render(context, request), content_type="text/html")
    except AleExperiment.DoesNotExist:
        return common.no_experiment_selected(request, context, logger, "metadata")
    except Exception as e:
        logger.exception("metadata broke", extra=user_extra(request))
        template = loader.get_template("500.html")
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")


def get_reseq_info_list(reseq_queryset):
    """One row of sample metadata per sample, keyed by name.

    **This was a positional tuple of eighteen values, and the bug that caused is the whole
    reason it is not.** It was built here and unpacked *by index* in
    `aledb_interop_query.views._serialize_metadata`, in another app, against a hand-written
    list of key names. The two agreed only by position, so they had drifted:

    - index 12 is the ALE's description and was published as ``knockouts``
    - index 13 is the isolate's library prep and was published as ``taxonomy_id`` -- and
      rendered on this page under a column headed *Taxonomy ID*
    - indices 15, 16 and 17 (breseq version, resequencing date, experiment name) were built
      on every request and read by nobody

    Nothing raised. Inserting a field anywhere in the chain above would silently relabel
    everything after it, which is presumably how it happened in the first place.

    Named keys make that class of error impossible rather than unlikely: a consumer that asks
    for the wrong name gets a `KeyError` or an empty template variable, not somebody else's
    column.
    """
    rows = []

    for reseq in reseq_queryset:
        tech_rep = reseq.tech_rep
        isolate = tech_rep.isolate
        flask = isolate.flask
        media = flask.media
        ale = flask.ale_id

        rows.append({
            "sample": reseq,
            "clonal_or_population": (SAMPLE_TYPE_MIXED if isolate.is_population
                                     else SAMPLE_TYPE_CLONAL),
            "tech_rep_description": tech_rep.description,
            "media_description": media.description,
            "carbon_source": media.carbon_source,
            "nitrogen_source": media.nitrogen_source,
            "phosphorus_source": media.phosphorus_source,
            "sulfur_source": media.sulfur_source,
            "calcium_source": media.calcium_source,
            "supplement": media.supplement,
            "temperature": media.temperature,
            "strain": ale.strain,
            "ale_description": ale.description,
            "library_prep": isolate.library_prep,
            "reseq_reference": isolate.reseq_reference,
            "breseq_version": isolate.breseq_version,
            "reseq_date": isolate.reseq_date,
            "experiment_name": ale.ale_experiment.name,
        })

    return rows
