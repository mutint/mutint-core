"""Asking the queue what it knows, and the one place a backend is named.

Two questions, and they need different answers because the standard API answers only one of
them.

**"What became of this job?"** is `Task.get_result(id)`, which every backend implements. That
is what `status_of` uses, so the page keeps working if `TASKS` is ever pointed at Redis, RQ or
Celery -- which is the whole argument for that setting being a setting.

**"What else is on the queue?"** is not expressible in the standard API at all: it can only
answer about a result id you already hold. So `unattributed()` reads `django_tasks_db`'s table
directly, and is the only function in aledb-core that knows which backend is configured. It is
isolated here rather than spread through the view, and it answers `[]` rather than raising when
some other backend is in use -- a page that cannot enumerate the queue should show the jobs it
does know about, not fail.
"""

import logging

from django.conf import settings
from django.db import models, transaction

logger = logging.getLogger("aledb_jobs.queue")

DATABASE_BACKEND = "django_tasks_db.DatabaseBackend"

# Unfinished rows first, then this many of the most recent finished ones. Capped because the
# only index on `status` is partial on READY and there is none on `enqueued_at`, so an
# unbounded listing over a table nobody has pruned is a sequential scan -- and because a
# hundred coverage builds from this morning's import is not information.
UNATTRIBUTED_LIMIT = 50

# The two statuses that mean the work is over. `aledb_jobs.views` has its own copy for a
# different question -- it also has to reason about STATUS_UNKNOWN, which the queue's own
# vocabulary has no word for.
FINISHED_STATUSES = ("SUCCESSFUL", "FAILED")

# What the page says when the queue no longer holds a job's result. `prune_db_task_results`
# removes finished rows after a fortnight, so this is the ordinary fate of an old job rather
# than an error.
STATUS_UNKNOWN = "unknown"

# The backend and queue a bare `@task()` enqueues onto, and the ones `db_worker` reads by
# default. Spelled here rather than imported from the package's `compat` module, which is
# private to it -- and `unattributed()` already answers [] rather than raising when some other
# backend is configured, which is the posture for anything this module assumes.
DEFAULT_BACKEND_ALIAS = "default"
DEFAULT_QUEUE_NAME = "default"

# How long work may sit at the head of the queue before the page says nothing appears to be
# taking it. Not five: a page loaded a second after an import would accuse a worker that is
# about to claim the row, and an indicator that cries wolf is worse than none.
STALLED_SECONDS = 60


def status_of(task_result_id, task_path=""):
    """The queue's status for one job, or STATUS_UNKNOWN.

    Resolved through the task's own `get_result`, so it goes via whatever backend is
    configured. Every failure is the same answer: a pruned result, a task whose function has
    been renamed or deleted, and a backend that cannot answer are all "the queue can no longer
    tell us", and none of them is worth a broken page.
    """
    if not task_result_id:
        return STATUS_UNKNOWN
    try:
        task = _resolve_task(task_path)
        if task is None:
            return STATUS_UNKNOWN
        return task.get_result(str(task_result_id)).status.value
    except Exception:
        logger.debug("no queue status for %s", task_result_id, exc_info=True)
        return STATUS_UNKNOWN


def _resolve_task(task_path):
    """Import the `@task` object named by a dotted path, or None."""
    if not task_path or "." not in task_path:
        return None
    module_path, _, attribute = task_path.rpartition(".")
    try:
        module = __import__(module_path, fromlist=[attribute])
        return getattr(module, attribute, None)
    except ImportError:
        return None


def uses_database_backend():
    """Whether `TASKS` names the backend whose table this module reads.

    Public because `./aledb start` asks it before spawning a worker: under `ImmediateBackend`
    there is nothing to drain and `db_worker` would die on its own `valid_backend_name`. Asked
    here rather than there so this stays the only module in aledb-core that knows the
    backend's dotted path.
    """
    backend = (getattr(settings, "TASKS", {}) or {}).get("default", {}) or {}
    return backend.get("BACKEND") == DATABASE_BACKEND


def oldest_ready():
    """When the row a worker would claim next was enqueued, or None.

    The honest half of "is a worker running", which **cannot be asked directly**:
    `django_tasks_db` has no worker registry and no heartbeat, only `worker_ids` written onto
    rows already claimed. So the page says what can be observed -- work has been waiting since
    a time -- and leaves the conclusion hedged, because a worker busy on a twelve-hour breseq
    run reads identically to no worker at all.

    Three things about the query:

    - **It mirrors the worker's own filter**, `backend_name` and `queue_name` both. A row
      sitting on a queue nobody runs would otherwise accuse a perfectly healthy worker for
      ever.
    - **It rides the model's default ordering**, which is exactly `tasks_db_new_ordering_idx`
      (`priority DESC, run_after, enqueued_at`, partial on `status = 'READY'`). So this is an
      index-ordered LIMIT 1: no sort, and no touching of finished rows. `Min("enqueued_at")`
      or a `count()` would be neither.
    - **The head of the queue, not the literally oldest row.** It is the more meaningful of the
      two: if the row a worker would take *next* has been sitting for a minute, nothing is
      taking it.
    """
    if not uses_database_backend():
        return None
    try:
        from django_tasks_db.models import DBTaskResult
    except ImportError:
        return None

    return (DBTaskResult.objects.ready()
            .filter(backend_name=DEFAULT_BACKEND_ALIAS, queue_name=DEFAULT_QUEUE_NAME)
            .values_list("enqueued_at", flat=True)
            .first())


def unattributed(known_ids):
    """Queue rows with no `Job` beside them, newest first, capped.

    Coverage builds and anything else enqueued the plain way. Shown to superusers only: with
    no owner and no label there is nothing useful to tell anybody else about them.

    Returns `[]` on any other backend rather than raising, and the rows it does return are
    plain dicts, so nothing outside this module handles a `DBTaskResult`.
    """
    if not uses_database_backend():
        return []

    try:
        from django_tasks_db.models import DBTaskResult
    except ImportError:
        return []

    known = [i for i in known_ids if i]
    unclaimed = DBTaskResult.objects.exclude(id__in=known)

    # **Unfinished first, and this used to be a comment describing something the code did not
    # do.** It was one `order_by("-enqueued_at")[:50]`, so on any installation with more than
    # fifty queue rows a stranded READY row from last month was pushed off the list entirely by
    # this morning's successes -- and this panel is the only place such a row is visible at
    # all. Two queries rather than one because the unfinished half is a fully indexed read
    # (`tasks_db_new_ordering_idx` is partial on READY) while a global sort on `enqueued_at`
    # has no index behind it. Oldest first among the unfinished, because an old one is the
    # interesting one; newest first among the finished, because there it is recency that
    # matters.
    unfinished = list(unclaimed.exclude(status__in=FINISHED_STATUSES)
                      .order_by("enqueued_at")[:UNATTRIBUTED_LIMIT])
    budget = UNATTRIBUTED_LIMIT - len(unfinished)
    finished = list(unclaimed.filter(status__in=FINISHED_STATUSES)
                    .order_by("-enqueued_at")[:budget]) if budget > 0 else []
    rows = unfinished + finished

    listed = []
    for row in rows:
        listed.append({
            # `task_name` falls back to the bare path and never raises on a task whose
            # function has been renamed or removed, which is exactly the row most likely to
            # still be sitting here.
            "label": row.task_name,
            "task_path": row.task_path,
            "status": row.status,
            "enqueued_at": row.enqueued_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "queue_name": row.queue_name,
        })
    return listed


def reap_stranded_rows(cutoff, dry_run=False):
    """Delete queue rows that nothing will ever finish. Returns the ids removed.

    **Nothing else clears these.** `django_tasks_db` ships `prune_db_task_results`, and it
    filters `DBTaskResult.objects.finished()` -- SUCCESSFUL or FAILED. A row no worker ever
    claims is outside its remit by construction, so a READY row is immortal: four accumulated
    in this suite's two dev databases with nothing in existence that would remove them.

    Two shapes qualify, and both are "a worker was supposed to deal with this and never will":

    - **READY past the cutoff**, by `enqueued_at`. A row from this morning is *waiting for a
      worker*, which is a different thing entirely; age is the only way to tell them apart.
    - **RUNNING past the cutoff**, by `started_at`. These have a real source now that
      `./aledb start` runs a worker: shutdown kills the worker's group after a few seconds
      rather than waiting out a twelve-hour breseq run, and there is no reaper for a claimed
      row, so a task in flight at Ctrl-C leaves one saying RUNNING for ever. A separate,
      shorter window for these was considered and rejected -- the default already exceeds the
      longest task this suite runs by two orders of magnitude, and a second knob nobody can
      choose a value for is worse than one.

    **`select_for_update(skip_locked=True)` is not optional, and this is the part to get
    right.** The worker claims inside `SELECT ... FOR UPDATE SKIP LOCKED`, so a plain
    `.filter(status=READY).delete()` can read a row as READY, block on the worker's lock, and
    delete a row that has since become RUNNING. `set_successful` then raises `Model.NotUpdated`
    on its zero-row `save(update_fields=...)`, `set_failed` raises again from inside the except
    block, and the exception escapes the run loop and **takes the worker process down** along
    with every other job it would have run. Skipping locked rows passes over anything a live
    worker holds, which is the whole of the fix.
    """
    if not uses_database_backend():
        return []
    try:
        from django_tasks_db.models import DBTaskResult
    except ImportError:
        return []

    stranded = (models.Q(status="READY", enqueued_at__lt=cutoff)
                | models.Q(status="RUNNING", started_at__lt=cutoff))

    with transaction.atomic():
        rows = DBTaskResult.objects.filter(stranded)
        if not dry_run:
            rows = rows.select_for_update(skip_locked=True)
        ids = list(rows.values_list("id", flat=True))
        if ids and not dry_run:
            DBTaskResult.objects.filter(id__in=ids).delete()
    return ids
