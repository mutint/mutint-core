"""Work that leaves the request.

`django.tasks` is Django's own API for this, new in 6.0: `@task` to declare, `.enqueue()` to
ask for it. What Django does **not** ship is anything that runs the work -- the built-in
backends are `ImmediateBackend`, which runs it inline, and `DummyBackend`, which never runs
it, and both are documented as development and testing only. A worker process is explicitly
out of scope for core (DEP 0014: *"a generic way of executing task runners ... will remain
the responsibility of the underlying implementation"*), so it is not something to wait for a
later Django to provide.

So the call sites are Django's standard API and the queue is a settings value:
`TASKS['default']['BACKEND']` names `django_tasks_db.DatabaseBackend` and its `db_worker`
management command runs them. Swapping that for Redis, RQ or Celery later is a settings
change and touches nothing here -- which is the property this was chosen for, and the reason
to prefer the standard decorator over a vendor's.

**This is also why PostgreSQL had to come first.** A database-backed queue claims rows with
`SELECT ... FOR UPDATE SKIP LOCKED`, which SQLite has no equivalent of; one worker on SQLite
is genuinely queued and several are not.

### Why coverage is the first thing moved

`coverage.build_for` walks the whole BAM and shells out to `bedGraphToBigWig` under a
900-second timeout, per sample, inside the import's POST. It is also the *only* candidate
whose failure mode was already designed for: a sample keeps its reads whether or not its
coverage builds, the genome browser falls back to igv's own coverage row when there is no
BigWig, and `./mutint coverage` backfills. So "no worker is running" is a state the product
already handles and already has a recovery path for, rather than a new one to invent.
"""

import logging

from django.tasks import task

from mutint_import import coverage
from mutint_jobs import jobs
from mutint_sample.models import Sample

logger = logging.getLogger(__name__)


@task(takes_context=True)
def build_coverage(context, sample_id):
    """Derive and store one sample's coverage BigWig.

    **`takes_context=True` is how it learns its own queue id**, which is what `mutint_jobs`
    keys a cancellation on. `mutint_breseq` does not need this -- its `BreseqRun` row stores
    `task_result_id` -- but coverage has no row of its own, only a sample's primary key.
    Django hands a `TaskContext` whose `task_result.id` is exactly the handle
    `jobs.is_cancelled` wants.

    **Takes the primary key, not the model.** Task arguments are serialized to JSON, so a
    model instance cannot travel and would fail at enqueue time; the pk is also the honest
    thing to send, because the row may have changed by the time a worker picks the task up.

    **Calls `build_for`, not `build_quietly`**, and that is the upgrade this move buys.
    `build_quietly` swallows `CoverageError`, `ToolMissing` and subprocess failures because
    inside an import it must not cost the sample its mutations. Out here the sample's rows
    are already committed, so there is nothing left to protect and a failure should be
    recorded rather than hidden -- which matters most for the failure a worker is *most*
    likely to hit: a `db_worker` started outside `./mutint` has no `MUTINT_TOOLS_DIR`, so
    `bedGraphToBigWig` is not found and every sample would silently get no coverage.
    """
    # Polled once, at entry, and that is the only place it would mean anything: the
    # derivation below is a single `build_for` call with no loop to check inside. It matters
    # because `request_cancel` deliberately never touches the queue row, so a job cancelled
    # while it was still queued is handed to a worker anyway -- the same reason
    # `mutint_breseq.tasks.run_breseq` checks before it does anything.
    if jobs.is_cancelled(context.task_result.id):
        logger.info("coverage for sample %s was cancelled before it ran", sample_id)
        return None

    reseq = Sample.objects.filter(pk=sample_id).first()
    if reseq is None:
        # Deleted between enqueue and execution. Not an error: there is nothing to derive.
        logger.info("sample %s is gone; no coverage to build", sample_id)
        return None
    tally = coverage.build_for(reseq)
    logger.info("coverage built for sample %s", sample_id)
    return str(tally) if tally is not None else None
