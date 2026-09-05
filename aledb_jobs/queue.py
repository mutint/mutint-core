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

logger = logging.getLogger("aledb_jobs.queue")

DATABASE_BACKEND = "django_tasks_db.DatabaseBackend"

# Unfinished rows first, then this many of the most recent finished ones. Capped because the
# only index on `status` is partial on READY and there is none on `enqueued_at`, so an
# unbounded listing over a table nobody has pruned is a sequential scan -- and because a
# hundred coverage builds from this morning's import is not information.
UNATTRIBUTED_LIMIT = 50

# What the page says when the queue no longer holds a job's result. `prune_db_task_results`
# removes finished rows after a fortnight, so this is the ordinary fate of an old job rather
# than an error.
STATUS_UNKNOWN = "unknown"


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


def _using_database_backend():
    backend = (getattr(settings, "TASKS", {}) or {}).get("default", {}) or {}
    return backend.get("BACKEND") == DATABASE_BACKEND


def unattributed(known_ids):
    """Queue rows with no `Job` beside them, newest first, capped.

    Coverage builds and anything else enqueued the plain way. Shown to superusers only: with
    no owner and no label there is nothing useful to tell anybody else about them.

    Returns `[]` on any other backend rather than raising, and the rows it does return are
    plain dicts, so nothing outside this module handles a `DBTaskResult`.
    """
    if not _using_database_backend():
        return []

    try:
        from django_tasks_db.models import DBTaskResult
    except ImportError:
        return []

    rows = (DBTaskResult.objects
            .exclude(id__in=[i for i in known_ids if i])
            .order_by("-enqueued_at")[:UNATTRIBUTED_LIMIT])

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
