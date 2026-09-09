"""Two things that happen to a job's log without any task having to remember them.

Connected from `JobsConfig.ready()`, so a component that runs a tool gets both for free.
"""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.tasks.signals import task_finished

from mutint_jobs import logs
from mutint_jobs.models import Job

logger = logging.getLogger("mutint_jobs.receivers")


@receiver(task_finished)
def _compress_log(sender, task_result=None, **kwargs):
    """Gzip a job's log once its task is over.

    `task_finished` is sent by `db_worker` on **both** the success and the failure path, and by
    `ImmediateBackend` too, so this covers every job without a line in any task. Uncompressed
    while running is what makes appending and tailing cheap; compressed afterwards is what
    makes keeping them cheap.

    A worker killed outright never gets here, which is why every reader in `logs` accepts an
    uncompressed file as well.
    """
    if task_result is None:
        return
    logs.compress(getattr(task_result, "id", None))


@receiver(post_delete, sender=Job)
def _remove_log(sender, instance, **kwargs):
    """A job's log goes when its row does.

    `store.component_dir` is deliberately not reaped by anything -- core cannot know what a
    component keeps there -- so a row-keyed directory needs its own receiver. This is also what
    makes `./mutint reap_jobs` clear the logs of the rows it strands, since that deletes `Job`
    rows through the ORM. A log with no row at all -- work enqueued without going through
    `jobs.enqueue` -- is out of this receiver's reach; `logs.orphans` is what finds those.
    """
    logs.discard(instance.task_result_id)
