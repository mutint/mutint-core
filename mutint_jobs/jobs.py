"""Enqueueing work that a person can see, and stop.

`task.enqueue(...)` is still the mechanism. What this adds is a `Job` row beside it, so the
work has an owner and a name, and a flag a long task can poll.

A component enqueues through here instead of directly::

    from mutint_jobs import jobs

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

from mutint_jobs import logs, queue
from mutint_jobs.models import Job

logger = logging.getLogger("mutint_jobs")


class JobCancelled(Exception):
    """Raised by a task that found its cancellation flag set and stopped."""


def enqueue(task, *args, user=None, label="", component="", experiment=None,
            cancellable=False, annotates_reference=False, **kwargs):
    """Enqueue `task` and record who asked. Returns the `Job`.

    The queue first, the row second, and **deliberately not in one transaction**. The two
    failure modes are not symmetrical: a `Job` naming a task that was never enqueued is a
    ghost that sits on the page for ever saying "queued", while a task with no `Job` is merely
    unattributed, which the page already renders and a superuser can still see. So the order
    is chosen to make the survivable failure the possible one.

    `user` may be None -- some work is asked for by an import rather than by a person -- and
    such a job is then visible only to superusers, for want of anyone to show it to.

    `annotates_reference` says this job will rewrite the experiment's annotation; see the
    field's own comment for why it is a claim about effect rather than a promise about
    behaviour, and `annotation_jobs` below for what core does with it.
    """
    result = task.enqueue(*args, **kwargs)

    return Job.objects.create(
        task_result_id=str(result.id),
        task_path=getattr(task, "module_path", "") or "",
        user=user if getattr(user, "is_authenticated", False) else None,
        label=label or (getattr(task, "name", "") or ""),
        component=component,
        experiment=experiment,
        cancellable=cancellable,
        annotates_reference=annotates_reference)


def is_cancelled(task_result_id):
    """Whether somebody has asked this job to stop. One indexed read; safe to poll.

    `task_result_id` rather than a `Job` pk, because that is what a task has: it was given a
    primary key of its own domain object, and the queue id is what links the two. A task with
    no `Job` row -- enqueued the plain way -- is never cancelled, which is the right answer.

    **It cannot raise**, which is the same posture `queue.status_of` takes beside it and
    `rebuild_registry.ensure_fresh` takes on a read path: a question *about* a job must not be
    able to take down the job it is about. Found the hard way -- a dev database predating this
    app has no `mutint_jobs_job` table, and the coverage task, which now checks this before it
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


def request_cancel_for(task_result_ids, by=None):
    """Ask every job with one of these queue ids to stop. Returns how many were asked.

    For a component superseding its own earlier work: mutint-breseq launches a run for a
    sample that already has one queued or running, and the old run's output is about to be
    overwritten by the new one, so finishing it is waste.

    **The ground for cancelling here is not that the job is yours.** `may_cancel` asks that,
    and is the right question for a person pressing a button on `/jobs/`; this is a component
    saying that work it started has been superseded by work it is starting now, which it is
    entitled to say about its own jobs whoever asked for them. A caller must therefore be
    sure the ids are its own.

    Already-cancelled jobs are skipped rather than re-flagged, so the count is what this call
    actually changed.
    """
    ids = [str(one) for one in task_result_ids if one]
    if not ids:
        return 0

    asked = 0
    for job in Job.objects.filter(task_result_id__in=ids, cancel_requested_at__isnull=True):
        request_cancel(job, by=by)
        asked += 1
    return asked


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


def may_view(user, job):
    """Whether `user` may read `job` -- its log, and anything else about it.

    The same rule as `may_cancel`, and deliberately the same rule: what a job's log contains is
    a tool's account of work somebody asked for, and standing to read it comes from having
    asked. A caller who may not gets a **404 rather than a 403**, the posture `job_cancel`
    takes, so a route cannot be used to find out which job ids exist.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return job.user_id == user.id


def stalled_since():
    """When the head of the queue started waiting, if nothing appears to be taking it.

    **This is the closest thing to "is a worker running" that can honestly be asked.**
    `django_tasks_db` keeps no worker registry and no heartbeat -- only `worker_ids`, written
    onto rows a worker has already claimed -- so a page reports the observation and leaves the
    reader to draw the conclusion, because a worker busy on a twelve-hour breseq run looks
    from here exactly like no worker at all.

    It lives here rather than on the jobs page because it has a second reader now: the Import
    data page's annotation panel, which switches a button off and therefore owes an
    explanation when nothing is going to finish. Two surfaces inventing two rules for one
    observation is how they come to disagree.
    """
    from django.utils import timezone

    oldest = queue.oldest_ready()
    if oldest is None:
        return None
    if (timezone.now() - oldest).total_seconds() < queue.STALLED_SECONDS:
        return None
    return oldest.isoformat()


def finished(status):
    """Whether a job has stopped moving, as the pages reason about it.

    `STATUS_UNKNOWN` counts: the queue prunes finished results after a fortnight, so a job it
    no longer holds is old rather than running. `TaskResult.is_finished` says the same thing
    about the two real statuses; this exists because everything rendering a job also has to
    reason about the third, which the queue's own vocabulary has no word for.
    """
    return status in queue.FINISHED_STATUSES or status == queue.STATUS_UNKNOWN


def row(job, *, user):
    """One job as the pages render it. `user` is who is looking, and is required.

    **The two permission terms below are load-bearing only away from `/jobs/`.** That page
    lists `for_user(user)`, which already restricts it to jobs the caller owns or is a
    superuser over, so `may_cancel` and `may_view` are no-ops there and look like belt and
    braces. They are not: the Import data page's annotation panel deliberately shows a viewer
    somebody *else's* job -- because the alternative is a disabled Import button with no
    reason given -- and these are what keep that viewer's row to the bare fact that the job
    exists, with no log link and no Cancel button. Removing them would hand a stranger both.
    """
    from django.urls import reverse

    status = queue.status_of(job.task_result_id, job.task_path)
    over = finished(status)
    return {
        "id": job.pk,
        "label": job.label or job.task_path,
        "component": job.component,
        "user": job.user.username if job.user else "",
        "experiment": job.experiment.name if job.experiment else "",
        "experiment_id": job.experiment_id,
        "created_at": job.created_at.isoformat(),
        "status": status,
        "finished": over,
        "cancel_requested": job.cancel_requested,
        "cancel_requested_by": (job.cancel_requested_by.username
                                if job.cancel_requested_by else ""),
        # A button only where pressing it would do something: the job must still be running,
        # its task must have promised to poll, and this reader must be allowed to stop it.
        # Anything else is a control that lies.
        "cancellable": (bool(job.cancellable) and not over and not job.cancel_requested
                        and may_cancel(user, job)),
        # A link only where there is something to read and somebody who may read it. One
        # `os.path.exists` per row, over a list already capped -- and cheaper than the
        # `status_of` above it, which is a query. A job whose task ran no tool never gets one.
        "log": (reverse("job_log", args=(job.pk,))
                if logs.exists(job.task_result_id) and may_view(user, job) else ""),
    }


#: How many of an experiment's annotation jobs `annotation_jobs` will look at. `Job` stores no
#: status, so "unfinished" is not expressible in SQL and every candidate row costs a
#: `status_of` query -- `UNATTRIBUTED_LIMIT`'s reasoning at a much smaller scale. An experiment
#: accumulates one row per annotator run for ever, and only the newest can be in flight.
ANNOTATION_JOB_LIMIT = 10


def annotation_jobs(experiment, *, limit=ANNOTATION_JOB_LIMIT):
    """The jobs still in flight that will rewrite `experiment`'s annotation, newest first.

    **The queue decides, never the row and never a component's own status column.** A worker
    killed outright -- which `./mutint start`'s own shutdown does -- leaves whatever it was
    working on looking eternally busy to anything that trusts a stored status. Everything
    built on this answer would then be stuck for ever, so the stored rows are only the
    candidates and `status_of` is the answer.

    **A job somebody has asked to stop is excluded, and that is the escape hatch.**
    `request_cancel` deliberately never touches the queue row, so with no worker running a
    cancelled job stays READY indefinitely -- and if that still counted as in flight, pressing
    Cancel would change nothing a person could see. What this answers is what is *about* to be
    written, and a cancellation is the person saying it should not be.
    """
    candidates = (Job.objects
                  .filter(experiment=experiment, annotates_reference=True,
                          cancel_requested_at__isnull=True)
                  .select_related("user", "experiment", "cancel_requested_by")
                  .order_by("-created_at")[:limit])
    return [job for job in candidates
            if not finished(queue.status_of(job.task_result_id, job.task_path))]


def log_urls(task_result_ids):
    """`{task_result_id: url}` for those queue results whose job has a log.

    For a component that holds queue ids rather than `Job` rows -- mutint-breseq's run list is
    the first -- so it can link to the log without querying this app's model itself. One query,
    however many ids, and ids with no job or no log are simply absent.
    """
    from django.urls import reverse

    ids = [str(one) for one in task_result_ids if one]
    if not ids:
        return {}

    found = {}
    for task_result_id, pk in Job.objects.filter(
            task_result_id__in=ids).values_list("task_result_id", "pk"):
        if logs.exists(task_result_id):
            found[task_result_id] = reverse("job_log", args=(pk,))
    return found


#: How long a queue row may sit before `reap_stranded` calls it abandoned rather than pending.
#: Fourteen days, matching `prune_db_task_results`'s own default for finished rows and
#: `purge_deleted`'s retention window -- the two nearest things in the suite.
DEFAULT_REAP_DAYS = 14


def reap_stranded(now=None, older_than_days=DEFAULT_REAP_DAYS, dry_run=False):
    """Clear queue rows nothing will ever finish, and the `Job` rows that named them.

    Returns `(queue_rows, job_rows)`. `now` is injectable so a test can age a row without
    waiting a fortnight, which is the shape `upload_session.reap_expired_sessions` already
    established and `./mutint reap_uploads` is the thin command over.

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
