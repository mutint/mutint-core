"""The jobs page: what you asked the machine to do, and how to stop it."""

import logging

from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from mutint_common.util import get_user_context
from mutint_jobs import jobs as jobs_api
from mutint_jobs import logs, queue
from mutint_jobs.models import Job

logger = logging.getLogger("mutint_jobs.views")


def _rows(user):
    return [jobs_api.row(job, user=user) for job in jobs_api.for_user(user)[:200]]


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
        "stalled_since": jobs_api.stalled_since(),
        "is_superuser": request.user.is_superuser,
    })
    return render(request, "jobs/list.html", context)


def jobs_json(request):
    """The same rows, for the page's poll."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    # `stalled_since` rides the poll as well as the first render. The page refreshes every few
    # seconds, so a banner written only into the initial context would go stale in the wrong
    # direction -- still accusing a worker that started a minute ago.
    return JsonResponse({
        "jobs": _rows(request.user),
        "unattributed": _unattributed(request.user),
        "stalled_since": jobs_api.stalled_since(),
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


def _job_for_reading(request, pk):
    """The job, if this reader may see it. 404 otherwise, never 403.

    Same posture as `job_cancel` and the breseq report viewer: a 403 tells the caller the id
    exists, and job ids are sequential.
    """
    job = Job.objects.select_related("user", "experiment").filter(pk=pk).first()
    if job is None or not jobs_api.may_view(request.user, job):
        raise Http404("Unknown job.")
    return job


@require_GET
def job_log(request, pk):
    """`/jobs/<pk>/log` -- what this job's commands printed, followed while it runs.

    **The page that moves under you while you are reading it is the thing to avoid**, and it
    is avoided by two rules rather than by not polling at all -- which is what this did, and
    which meant watching a three-hour breseq run was a Refresh every minute that reloaded the
    whole page and lost your place. The rules: new output is *appended*, never repainted, so
    a selection and a scroll position survive it; and the box is re-pinned to the bottom only
    when the reader was already there. A checkbox turns it off, and it is offered only while
    the job is still running, because a finished log does not change.

    `job_log_tail` is what the page asks; this renders the first copy, so the page is complete
    before any JavaScript runs.
    """
    job = _job_for_reading(request, pk)
    # The status first, then the log, and that order is load-bearing in both readers here: a
    # job can finish between the two reads, and this way round any final write is already in
    # the text being returned. The other way round stops the page polling on a log missing its
    # last lines, with nothing to say so.
    status = queue.status_of(job.task_result_id, job.task_path)
    text, truncated = logs.read_tail(job.task_result_id)
    finished = jobs_api.finished(status)
    has_log = bool(text) or logs.exists(job.task_result_id)

    context = get_user_context(request.user)
    context.update({
        "job": job,
        "job_label": job.label or job.task_path,
        # Asked of the queue here as everywhere else, so this page cannot say "running" about a
        # job that finished an hour ago -- the reason `Job` stores no status at all. Rendered
        # through the same labels `/jobs/` uses, or this page would be the one surface calling
        # it `unknown` rather than "No longer on the queue".
        "status": queue.label_for(status),
        "log_text": text,
        "log_truncated": truncated,
        "has_log": has_log,
        # **The box and the script are rendered for a job that has printed nothing yet**, not
        # only for one that has. `has_log` alone was the obvious gate and is exactly wrong: a
        # page opened the second a breseq run is launched has no log, so it would get no box,
        # no script and no poll -- the one case live tailing exists for.
        "show_box": has_log or not finished,
        "finished": finished,
        # The script's whole input, as a json_script rather than values interpolated into it:
        # the house idiom (`jobs/list.html`), and what makes the server half of this
        # assertable from a test that cannot run JavaScript.
        "log_state": {
            "finished": finished,
            "truncated": truncated,
            "tail_url": reverse("job_log_tail", args=(job.pk,)),
        },
        "tail_kb": logs.TAIL_BYTES // 1024,
        "download_url": reverse("job_log_download", args=(job.pk,)),
    })
    return render(request, "jobs/log.html", context)


@require_GET
def job_log_tail(request, pk):
    """The same tail as the page, as JSON, for the page's poll.

    Everything the page needs to decide what to do next, in one request: the text, whether it
    is cut, how to name the status, and **whether to ask again**. `finished` is `jobs.row`'s rule
    rather than a second one, so the log page and `/jobs/` cannot disagree about when a job
    stopped.

    The response that first reports `finished` already carries the final text -- the log is
    complete by the time the queue says so -- which is why the page needs no extra fetch to
    close out. `_job_for_reading` keeps the 404-never-403 posture the page and the download
    have.
    """
    job = _job_for_reading(request, pk)
    # Status first, then the log; see `job_log` for why that order is the safe one.
    status = queue.status_of(job.task_result_id, job.task_path)
    text, truncated = logs.read_tail(job.task_result_id)
    response = JsonResponse({
        "text": text,
        "truncated": truncated,
        "status": queue.label_for(status),
        "finished": jobs_api.finished(status),
    })
    # A polled URL that a proxy is free to cache is a log that freezes for one reader and
    # nobody else. Nothing else in this codebase sets a cache header, which is why this one
    # says why it does.
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def job_log_download(request, pk):
    """The whole log, however much of it there is.

    Not `mutint_common.fileserve.serve_file`: the stored file may be gzipped and a reader
    wants text either way, so there is nothing for its `Range` support to be right about. The
    filename is derived from the job's id rather than its label, which is a person's free text.
    """
    job = _job_for_reading(request, pk)
    if not logs.exists(job.task_result_id):
        raise Http404("This job has no log.")

    response = StreamingHttpResponse(logs.stream(job.task_result_id),
                                     content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="job-%d.log"' % job.pk
    # A log is a tool's output plus names a person chose. Nothing should sniff it into a
    # document -- the same rule the breseq report is served under.
    response["X-Content-Type-Options"] = "nosniff"
    return response
