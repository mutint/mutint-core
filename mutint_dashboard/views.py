import time
from django.shortcuts import render

from mutint_sample.views import common
from django.utils.safestring import mark_safe
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment, Project, live
from mutint_common.rebuild_registry import ensure_fresh
from mutint_common.storage_registry import (
    UNATTRIBUTED_REBUILD, bytes_by_kind, database_bytes, ensure_measured,
    stale_experiment_ids,
)
from mutint_dashboard.models import InstallationCounts
from mutint_dashboard.util import counts
from mutint_sample.functional_change import (
    FUNCTIONAL_CHANGE_LABELS, FUNCTIONAL_CHANGE_TYPE_LIST,
)
from mutint_sample.views.common import MUTATION_TYPE_LABELS, MUTATION_TYPE_LIST
from mutint_common.logger import user_extra, join_extras
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
        storage = storage_context()
        calls = counts(InstallationCounts.MUTATION_CALLS)
        unique = counts(InstallationCounts.UNIQUE_MUTATIONS)

        general_count_dict['observed'] = calls.get('total', 0)
        general_count_dict['unique'] = unique.get('total', 0)

        context = get_user_context(request.user)
        context.update({"count_dict": general_count_dict,
                        "call_total": calls.get('total', 0),
                        "unique_total": unique.get('total', 0),
                        # Ordered `(label, total, unique)` rows, built here rather than
                        # spelled out per row in the template. The template used to hardcode
                        # thirty-five attribute paths and every label beside them, so a
                        # renamed column emptied a cell in silence -- Django renders an
                        # unknown attribute as "". Driving both tables off the vocabularies
                        # means a token that exists is shown and one that does not cannot be
                        # asked for.
                        "type_rows": type_rows(calls, unique),
                        "change_rows": _rows(FUNCTIONAL_CHANGE_TYPE_LIST,
                                             FUNCTIONAL_CHANGE_LABELS,
                                             calls, unique, 'functional_change')})
        context.update(storage)
        logger.info("dashboard performance", extra=join_extras(user_extra(request), {"time taken": time.time() - start_time}))

        return render(request, DASHBOARD_TEMPLATE, context, content_type="text/html")

    except Exception as e:
        logger.exception(e, extra = user_extra(request))


def _rows(vocabulary, labels, calls, unique, section):
    """`(label, total, unique)` per token, in vocabulary order.

    Order is the vocabulary's own, which for functional change is severity order -- the same
    order that resolves a mutation found in two overlapping genes, so the table reads the way
    the bucketing works.
    """
    call_section = calls.get(section) or {}
    unique_section = unique.get(section) or {}
    return [(labels[token], call_section.get(token, 0), unique_section.get(token, 0))
            for token in vocabulary]


def type_rows(calls, unique):
    """The mutation-type rows, shared with a deployment's splash page.

    Public because `mutint_home` renders the same table. Two copies of these bindings is what
    left the splash showing nine blank cells when the context key was renamed under it.
    """
    return _rows(MUTATION_TYPE_LIST, MUTATION_TYPE_LABELS, calls, unique, 'type')


def get_general_count_dict():
    count_dict = dict()
    # `live()`, not `.count()`. Deletion here is soft, so the unfiltered managers still
    # carry every project and experiment anybody has ever removed -- and this page was
    # counting them.
    count_dict['experiment'] = live(Experiment.objects).count()
    count_dict['project'] = live(Project.objects).count()

    inventory = counts(InstallationCounts.INVENTORY)
    for thing in ('population', 'time_point', 'sample'):
        count_dict[thing] = inventory.get(thing, 0)
    return count_dict


def storage_context():
    """The Storage panel: what the store holds, and how much of it is accounted for.

    Measures every live experiment whose sizes are stale before summing, the way the
    Overview measures one -- this page is an inventory of the installation, and a total that
    silently leaves out the experiments imported since the last view is not one. The first
    view after this feature ships therefore walks every experiment once;
    `./mutint rebuild --all --only storage` pays that from a shell instead.

    Three numbers beside the total, each answering a different "where did the rest go":
    per kind, so a deployment knows whether it is the BAMs or the reports; awaiting purge,
    the sizes of soft-deleted experiments that `purge_deleted` will free; and unattributed,
    the bytes nothing in the database points at (see `rebuild_storage_unattributed`). And
    the database itself, which is the other thing a deployment's disk is holding.
    """
    from django.db.models import Q, Sum

    from mutint_common.models import StorageUsage

    live_experiments = live(Experiment.objects.all())
    live_ids = list(live_experiments.values_list("id", flat=True))
    unmeasured = 0
    for experiment_id in stale_experiment_ids(live_ids):
        if not ensure_measured(experiment_id):
            unmeasured += 1
    ensure_fresh(UNATTRIBUTED_REBUILD)

    kinds = bytes_by_kind(live_experiments)
    awaiting_purge = (StorageUsage.objects
                      .filter(Q(experiment__deleted_at__isnull=False)
                              | Q(experiment__project__deleted_at__isnull=False))
                      .aggregate(total=Sum("bytes"))["total"]) or 0
    unattributed = counts(InstallationCounts.STORAGE_UNATTRIBUTED)
    return {
        "storage_kinds": kinds,
        "storage_total": sum(size for _, _, size in kinds),
        "storage_awaiting_purge": awaiting_purge,
        "storage_unattributed": unattributed.get("total", 0),
        "storage_unmeasured": unmeasured,
        "storage_database": database_bytes(),
    }
