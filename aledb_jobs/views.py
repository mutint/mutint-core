"""The jobs page: what you asked the machine to do, and how to stop it."""

import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from aledb_common.util import get_user_context
from aledb_jobs import jobs as jobs_api
from aledb_jobs import queue
from aledb_jobs.models import Job

logger = logging.getLogger("aledb_jobs.views")

# Queue statuses that mean the work is over. `TaskResult.is_finished` says the same thing; it
# is spelled out here because the page also has to reason about STATUS_UNKNOWN, which the
# queue's own vocabulary has no word for.
FINISHED = ("SUCCESSFUL", "FAILED")


def _row(job):
    status = queue.status_of(job.task_result_id, job.task_path)
    finished = status in FINISHED or status == queue.STATUS_UNKNOWN
    return {
        "id": job.pk,
        "label": job.label or job.task_path,
        "component": job.component,
        "user": job.user.username if job.user else "",
        "experiment": job.experiment.name if job.experiment else "",
        "experiment_id": job.experiment_id,
        "created_at": job.created_at.isoformat(),
        "status": status,
        "finished": finished,
        "cancel_requested": job.cancel_requested,
        "cancel_requested_by": (job.cancel_requested_by.username
                                if job.cancel_requested_by else ""),
        # A button only where pressing it would do something: the job must still be running,
        # and its task must have promised to poll. Anything else is a control that lies.
        "cancellable": bool(job.cancellable) and not finished and not job.cancel_requested,
    }


def _rows(user):
    return [_row(job) for job in jobs_api.for_user(user)[:200]]


def _unattributed(user):
    if not getattr(user, "is_superuser", False):
        return []
    known = list(Job.objects.values_list("task_result_id", flat=True))
    return [{
        "label": row["label"],
        "status": row["status"],
        "queue_name": row["queue_name"],
        "enqueued_at": row["enqueued_at"].isoformat() if row["enqueued_at"] else None,
    } for row in queue.unattributed(known)]


@ensure_csrf_cookie
def jobs(request):
    """`/jobs/` -- your jobs, or everyone's if you are a superuser."""
    if not request.user.is_authenticated:
        return render(request, "403.html", get_user_context(request.user), status=403)

    context = get_user_context(request.user)
    context.update({
        "jobs": _rows(request.user),
        "unattributed": _unattributed(request.user),
        "is_superuser": request.user.is_superuser,
    })
    return render(request, "jobs/list.html", context)


def jobs_json(request):
    """The same rows, for the page's poll."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    return JsonResponse({
        "jobs": _rows(request.user),
        "unattributed": _unattributed(request.user),
    })


@require_POST
def job_cancel(request, pk):
    """Ask one job to stop.

    Answers 404 rather than 403 for a job the caller may not touch, so the endpoint cannot be
    used to find out whether a given job id exists -- the same reason `resolve_group_name`
    gives one message for a group you cannot see and one that does not exist.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    job = Job.objects.filter(pk=pk).first()
    if job is None or not jobs_api.may_cancel(request.user, job):
        return JsonResponse({"error": "Unknown job."}, status=404)

    if not job.cancellable:
        # Said plainly rather than accepted and quietly ignored. This job's task never promised
        # to look at the flag, so setting it would leave a row marked cancelled that runs to
        # completion anyway.
        return JsonResponse(
            {"error": "This kind of job cannot be stopped once it is queued."}, status=409)

    jobs_api.request_cancel(job, by=request.user)
    return JsonResponse({"cancelled": True, "jobs": _rows(request.user)})
