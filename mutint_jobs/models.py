"""What the task queue cannot say about a job: who asked for it, and what to call it.

`django_tasks_db` records a status against a UUID nobody sees. It has no user column, no
label, and no metadata field to put either in -- one was added in its migration 0017 and
removed again in 0019 -- so the only thing a queue row carries about *intent* is the arguments
of the call itself. That is enough for a worker and not enough for a person, who wants to know
what is running, whether it is theirs, and how to stop it.

`Job` is the side record that answers those. **It is not a second queue**, and the distinction
is the whole design:

- **It holds no status.** Status is asked of the queue every time it is rendered, so the two
  cannot drift apart. A row here saying `running` while the worker had finished hours ago is
  exactly the failure that makes a jobs page worse than none.
- **It holds the one thing the queue has no opinion about**: whether somebody asked for this
  to stop. `django_tasks_db` has no cancel API and no cancelled status, so cancelling has to
  be cooperative, and `cancel_requested_at` is the flag a task polls. See `jobs.py`.
"""

from django.contrib.auth.models import User
from django.db import models


class Job(models.Model):
    """One enqueued unit of work, attributed to whoever asked for it."""

    #: The queue's own id, from `TaskResult.id`. The join to everything the queue knows, and
    #: the handle a task polls its cancellation flag by.
    task_result_id = models.CharField(max_length=64, unique=True, db_index=True)
    #: Dotted path of the task function, e.g. `mutint_breseq.tasks.run_breseq`. Recorded so a
    #: job can be described after its function has been renamed or removed, which is the state
    #: an old row is most likely to be found in.
    task_path = models.CharField(max_length=200)

    #: SET_NULL, not CASCADE: deleting a person must not delete the record of work the
    #: installation did. Null also means "nobody in particular asked" -- see `jobs.enqueue`.
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="jobs")
    #: What a person reads in the list. Written by the caller, because only the component
    #: knows that `run_breseq(4)` means "breseq -- Ara-2_500gen_763A".
    label = models.CharField(max_length=200)
    #: App label of whatever enqueued it, for grouping and for saying where to look.
    component = models.CharField(max_length=64, blank=True)
    #: Optional, and only for showing which experiment a job belongs to. SET_NULL for the same
    #: reason as `user`: the record of the work outlives the thing it was about.
    experiment = models.ForeignKey("mutint_experiment.Experiment", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="jobs")

    created_at = models.DateTimeField(auto_now_add=True)

    #: Set when somebody asks for this to stop. **Nothing acts on it but the task itself** --
    #: see `jobs.request_cancel` for why the queue row is deliberately left alone.
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    cancel_requested_by = models.ForeignKey(User, null=True, blank=True,
                                            on_delete=models.SET_NULL,
                                            related_name="cancelled_jobs")

    #: Whether this job's task actually polls the flag. **False is the default and is the
    #: honest one**: a task that does not poll cannot be stopped, and a Cancel button that
    #: silently does nothing is worse than no button. A task opts in by promising to check.
    cancellable = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The page's two queries: mine, newest first; and everyone's for a superuser.
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return "Job %s (%s)" % (self.pk, self.label or self.task_path)

    @property
    def cancel_requested(self):
        return self.cancel_requested_at is not None
