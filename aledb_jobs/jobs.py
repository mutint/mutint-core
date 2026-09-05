"""Enqueueing work that a person can see, and stop.

`task.enqueue(...)` is still the mechanism. What this adds is a `Job` row beside it, so the
work has an owner and a name, and a flag a long task can poll.

A component enqueues through here instead of directly::

    from aledb_jobs import jobs

    jobs.enqueue(tasks.run_breseq, run.pk,
                 user=request.user, label="breseq -- %s" % run.sample_name,
                 component="mutint_breseq", experiment=experiment, cancellable=True)

and its task polls::

    if jobs.is_cancelled(task_result_id):
        ...   # stop, tidy up, and say so

Nothing is registered and there is no registry: a component calls a function, and nobody
enumerates components. Tasks enqueued the plain way still run -- they simply have no owner, and
the page shows them to superusers as unattributed.

Cancelling a running task **can only be cooperative**, and that is a property of the queue
rather than a choice made here: `django_tasks_db` has no cancel API and no cancelled status,
and its worker calls `task.call(...)` and looks at nothing again until it returns. There is no
preemption point to hook.
"""

import logging

from django.utils import timezone

from aledb_jobs import queue
from aledb_jobs.models import Job

logger = logging.getLogger("aledb_jobs")


class JobCancelled(Exception):
    """Raised by a task that found its cancellation flag set and stopped."""


def enqueue(task, *args, user=None, label="", component="", experiment=None,
            cancellable=False, **kwargs):
    """Enqueue `task` and record who asked. Returns the `Job`.

    The queue first, the row second, and **deliberately not in one transaction**. The two
    failure modes are not symmetrical: a `Job` naming a task that was never enqueued is a
    ghost that sits on the page for ever saying "queued", while a task with no `Job` is merely
    unattributed, which the page already renders and a superuser can still see. So the order
    is chosen to make the survivable failure the possible one.

    `user` may be None -- some work is asked for by an import rather than by a person -- and
    such a job is then visible only to superusers, for want of anyone to show it to.
    """
    result = task.enqueue(*args, **kwargs)

    return Job.objects.create(
        task_result_id=str(result.id),
        task_path=getattr(task, "module_path", "") or "",
        user=user if getattr(user, "is_authenticated", False) else None,
        label=label or (getattr(task, "name", "") or ""),
        component=component,
        experiment=experiment,
        cancellable=cancellable)


def is_cancelled(task_result_id):
    """Whether somebody has asked this job to stop. One indexed read; safe to poll.

    `task_result_id` rather than a `Job` pk, because that is what a task has: it was given a
    primary key of its own domain object, and the queue id is what links the two. A task with
    no `Job` row -- enqueued the plain way -- is never cancelled, which is the right answer.

    **It cannot raise**, which is the same posture `queue.status_of` takes beside it and
    `rebuild_registry.ensure_fresh` takes on a read path: a question *about* a job must not be
    able to take down the job it is about. Found the hard way -- a dev database predating this
    app has no `aledb_jobs_job` table, and the coverage task, which now checks this before it
    does anything, failed with `UndefinedTable`. That is a broken installation and it should be
    reported as one, but not as a coverage failure whose message says nothing about coverage.

    "We cannot tell" therefore means **not cancelled**, and that direction is deliberate: the
    cost is that a cancellation somebody asked for is ignored, which they can see and ask for
    again. The other direction would silently skip work nobody cancelled.
    """
    if not task_result_id:
        return False
    try:
        return Job.objects.filter(
            task_result_id=str(task_result_id),
            cancel_requested_at__isnull=False).exists()
    except Exception:
        logger.warning("could not read the cancellation flag for %s; assuming not cancelled",
                       task_result_id, exc_info=True)
        return False


def check_cancelled(task_result_id, message="This job was cancelled."):
    """`is_cancelled`, as a raise. For the top of a task and the inside of its loops."""
    if is_cancelled(task_result_id):
        raise JobCancelled(message)


def request_cancel(job, by=None):
    """Ask a job to stop. Idempotent; returns the `Job`.

    **This touches the queue not at all**, and that is the load-bearing decision rather than an
    omission. The tempting optimisation -- a job that has not started yet could have its queue
    row deleted, so it never runs -- is a trap with two independent halves:

    - **Deleting a row the worker has already claimed kills the worker.** Under Django 6.1
      `save(update_fields=...)` raises `Model.NotUpdated` when it matches no rows.
      `DBTaskResult.set_successful` retries three times and re-raises; `set_failed` in the
      except block raises again; that escapes `run_task`, escapes the worker's run loop, and
      takes the process down along with every other job it would have run. And the race is
      real, not theoretical: the worker claims inside `SELECT ... FOR UPDATE SKIP LOCKED`, so a
      delete can read `READY`, block on the lock, and then delete a row that has since become
      `RUNNING`.
    - **Writing a status instead is silently undone**, because `set_successful` / `set_failed`
      write `status` back blindly with `update_fields` when the task finishes.

    So the flag is the whole mechanism, and it behaves identically whether the job had been
    claimed or not. The cost is worth stating: a job cancelled before a worker reaches it is
    still picked up eventually and returns having done nothing, and with no worker running it
    stays on the queue indefinitely. The `Job` says cancelled either way, which is the question
    the person actually asked.
    """
    if job.cancel_requested:
        return job

    job.cancel_requested_at = timezone.now()
    job.cancel_requested_by = by if getattr(by, "is_authenticated", False) else None
    job.save(update_fields=["cancel_requested_at", "cancel_requested_by"])
    logger.info("job %s (%s) cancellation requested by %s",
                job.pk, job.label, getattr(by, "username", "?"))
    return job


def for_user(user):
    """The jobs `user` may see: their own, or everything for a superuser."""
    jobs = Job.objects.select_related("user", "experiment", "cancel_requested_by")
    if getattr(user, "is_superuser", False):
        return jobs
    return jobs.filter(user=user)


def may_cancel(user, job):
    """Whether `user` may stop `job`.

    Whoever asked for it, or a superuser. Deliberately **not** tied to the experiment's roles:
    a job is somebody's request for the machine to do something, and an experiment admin who
    did not ask for it has no more standing to stop it than to stop a colleague's export. A
    superuser can, because somebody has to be able to clear a wedged queue.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return job.user_id == user.id


#: How long a queue row may sit before `reap_stranded` calls it abandoned rather than pending.
#: Fourteen days, matching `prune_db_task_results`'s own default for finished rows and
#: `purge_deleted`'s retention window -- the two nearest things in the suite.
DEFAULT_REAP_DAYS = 14


def reap_stranded(now=None, older_than_days=DEFAULT_REAP_DAYS, dry_run=False):
    """Clear queue rows nothing will ever finish, and the `Job` rows that named them.

    Returns `(queue_rows, job_rows)`. `now` is injectable so a test can age a row without
    waiting a fortnight, which is the shape `upload_session.reap_expired_sessions` already
    established and `./aledb reap_uploads` is the thin command over.

    **Only the `Job` rows whose queue row we just removed.** A `Job` whose result the library's
    own pruner has already discarded is not litter -- it is the ordinary end of a finished job,
    and `queue.STATUS_UNKNOWN` exists precisely to render it. Tidying those away would delete
    the installation's record of work it did, which is the thing every `SET_NULL` on this model
    is there to preserve.
    """
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(days=older_than_days)

    ids = queue.reap_stranded_rows(cutoff, dry_run=dry_run)
    if not ids:
        return 0, 0

    orphans = Job.objects.filter(task_result_id__in=[str(i) for i in ids])
    if dry_run:
        return len(ids), orphans.count()

    removed = orphans.delete()[0]
    logger.info("reaped %d stranded queue row(s) and %d job row(s)", len(ids), removed)
    return len(ids), removed
