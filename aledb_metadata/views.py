import time

from django.http import HttpResponse

from django.template import loader

from django.conf import settings
from aledb_experiment.models import Experiment
from aledb_seq.views import common
from aledb_common.util import get_user_context
from aledb_seq.util import get_ordered_reseq_queryset

from aledb_common.constants import (REQUEST_EXPERIMENT_ID, REQUEST_POPULATION,
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
        experiment = common.get_experiment(request)
        experiment_id=experiment.id
        ale_id = request.GET.get(REQUEST_POPULATION)

        reseq_queryset = get_ordered_reseq_queryset(experiment_id, ale_id)

        sample_info_list = get_sample_info_list(reseq_queryset)

        context = get_user_context(request.user)
        context.update({"sample_info_list": sample_info_list,
                        "experiment_name": experiment.name,
                        "ale_project_name": experiment.project.name,
                        "ale_project_id": experiment.project.id,
                        "multiple": False,
                        "experiment_id": experiment_id
                        })

        template = loader.get_template(META_DATA_TEMPLATE)
        logger.info("metadata performance", extra=join_extras(user_extra(request), {"time taken": time.time() - start_time}))
        return HttpResponse(template.render(context, request), content_type="text/html")
    except Experiment.DoesNotExist:
        return common.no_experiment_selected(request, context, logger, "metadata")
    except Exception as e:
        logger.exception("metadata broke", extra=user_extra(request))
        template = loader.get_template("500.html")
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")


def get_sample_info_list(reseq_queryset):
    """One row of sample metadata per sample, keyed by name.

    **This was a positional tuple of eighteen values, and the bug that caused is the whole
    reason it is not.** It was built here and unpacked *by index* in
    `aledb_interop_query.views._serialize_metadata`, in another app, against a hand-written
    list of key names. The two agreed only by position, so they had drifted:

    - index 12 is the population's description and was published as ``knockouts``
    - index 13 is the sample's library prep and was published as ``taxonomy_id`` -- and
      rendered on this page under a column headed *Taxonomy ID*
    - indices 15, 16 and 17 (breseq version, sequencing date, experiment name) were built
      on every request and read by nobody

    Two more keys were wrong in the same way and are fixed with the vocabulary: the payload
    called a *formatted frequency* ``genotype``, and called the sample's own medium note
    ``tech_rep_description`` after a model that no longer exists.

    Nothing raised. Inserting a field anywhere in the chain above would silently relabel
    everything after it, which is presumably how it happened in the first place.

    Named keys make that class of error impossible rather than unlikely: a consumer that asks
    for the wrong name gets a `KeyError` or an empty template variable, not somebody else's
    column.
    """
    rows = []

    for reseq in reseq_queryset:
        time_point = reseq.time_point
        media = time_point.media
        population = time_point.population

        rows.append({
            "sample": reseq,
            # `clonal` or `mixed` -- the same two words `?sample_type=` accepts, which is
            # why the key is that and not `clonal_or_population`. That name listed the
            # possible answers, and one of them is no longer one of them.
            "sample_type": (SAMPLE_TYPE_MIXED if reseq.is_mixed else SAMPLE_TYPE_CLONAL),
            # Two different columns, a letter apart if both were called "medium": this one
            # is the sample's own note (the CSV's "medium description"), the next is the
            # medium's own (`Media.description`, from "medium derived from").
            "sample_medium_description": reseq.medium_description,
            "media_description": media.description,
            "carbon_source": media.carbon_source,
            "nitrogen_source": media.nitrogen_source,
            "phosphorus_source": media.phosphorus_source,
            "sulfur_source": media.sulfur_source,
            "calcium_source": media.calcium_source,
            "supplement": media.supplement,
            "temperature": media.temperature,
            "strain": population.strain,
            "population_description": population.description,
            "library_prep": reseq.library_prep,
            "reference_genome": reseq.reference_genome,
            "breseq_version": reseq.breseq_version,
            "sequencing_date": reseq.sequencing_date,
            "experiment_name": population.experiment.name,
        })

    return rows
