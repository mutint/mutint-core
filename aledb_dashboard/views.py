import time
from django.shortcuts import render

from aledb_seq.views import common
from django.utils.safestring import mark_safe
from aledb_common.util import get_user_context
from aledb_experiment.models import Experiment, Project, live
from aledb_common.rebuild_registry import ensure_fresh
from aledb_dashboard.models import (
    InventoryCounts, MutationCallCounts, UniqueMutationCounts,
)
from aledb_common.logger import user_extra, join_extras
import logging

DEFAULT_IGNORED_MUTATIONS = "[]"
DASHBOARD_TEMPLATE = "dashboard/dashboard.html"
__author__ = 'pphaneuf'

logger = logging.getLogger(__name__)


def dashboard(request):
    logger.info("populating dashboard", extra=user_extra(request))

    try:
        start_time = time.time()
        # The lazy half of the rebuild contract, and this is the page it was written for: a
        # global filter edit marks every experiment stale and rebuilds nothing, because
        # recomputing the installation inside the request that edited a form is exactly what
        # marking exists to avoid. So the first view after it pays, once, and every view
        # after that is free. `ensure_fresh` no-ops when fresh and cannot raise.
        ensure_fresh('sample_counts')
        ensure_fresh('mutation_counts')

        general_count_dict = get_general_count_dict()
        mutation_call_counts = MutationCallCounts.objects.first()
        unique_mutation_counts = UniqueMutationCounts.objects.first()

        if unique_mutation_counts and mutation_call_counts:
            general_count_dict['observed'] = mutation_call_counts.total
            general_count_dict['unique'] = unique_mutation_counts.total

        context = get_user_context(request.user)
        context.update({"count_dict": general_count_dict,
                        "unique_mutation_counts": unique_mutation_counts,
                        "mutation_call_counts": mutation_call_counts})
        logger.info("dashboard performance", extra=join_extras(user_extra(request), {"time taken": time.time() - start_time}))

        return render(request, DASHBOARD_TEMPLATE, context, content_type="text/html")

    except Exception as e:
        logger.exception(e, extra = user_extra(request))


def get_general_count_dict():
    count_dict = dict()
    # `live()`, not `.count()`. Deletion here is soft, so the unfiltered managers still
    # carry every project and experiment anybody has ever removed -- and this page was
    # counting them.
    count_dict['experiment'] = live(Experiment.objects).count()
    count_dict['project'] = live(Project.objects).count()

    inventory = InventoryCounts.objects.first()

    if inventory:
        count_dict['population'] = inventory.population_count
        count_dict['time_point'] = inventory.time_point_count
        count_dict['sample'] = inventory.sample_count
    return count_dict
