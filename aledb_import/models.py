"""Server-side state for chunked uploads.

A breseq folder drop can be tens of GB, which cannot go through a single POST. The client
declares a manifest, uploads each file in chunks, then asks the server to finalize.

That made every *transfer* request short. It did not make finalize short -- it moved the
entire ingest there, where it is bounded by nothing, which is the case ``WORKERS.md`` records
as having outgrown this design. There is still no task queue, and the repo has neither Celery
nor Channels; what a long finalize has instead is ``progress``, a snapshot the Add page polls
so the wait is legible rather than silent.
"""

import datetime
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

STATE_OPEN = "open"
STATE_FINALIZED = "finalized"
STATE_FAILED = "failed"
# Handed to a component, which now owns the staged directory. Distinct from `finalized`
# because the two say opposite things about the files: finalized means the ingest is done and
# the directory is gone, claimed means somebody else is still using it and the reaper must
# keep its hands off. See aledb_import/staging.py.
STATE_CLAIMED = "claimed"

STATE_CHOICES = [
    (STATE_OPEN, "Open"),
    (STATE_FINALIZED, "Finalized"),
    (STATE_FAILED, "Failed"),
    (STATE_CLAIMED, "Claimed"),
]


class ImportLock(models.Model):
    """One row, held for the duration of an import, so two never run at once.

    SQLite permits exactly one writer, so concurrent imports were already queueing -- badly.
    Each sample is its own transaction, and a transaction that cannot get the write lock
    within ``busy_timeout`` fails and takes its sample with it. Measured with three
    importers and transactions longer than the timeout: **19 of 30 samples landed**. Holding
    this row instead makes the queue orderly, and the same measurement gives 30 of 30.

    **A database row rather than a Python lock**, because the dev server is threaded and a
    deployment may run several processes -- an in-process lock would not be seen by either.
    Rather than ``select_for_update`` (a no-op on SQLite, which has no row locking), the
    guarantee comes from the primary key: ``name`` is unique, so *creating* the row is the
    acquire and only one caller can win it. That works on every backend.

    Held rows are reclaimed after ``STALE_AFTER``. A process killed mid-import would
    otherwise block every later one for ever, and there is no cleanup path that runs on a
    process that has already died.
    """

    # Long enough that a real import is never mistaken for a dead one -- the largest LTEE
    # drop is minutes, not hours -- and short enough that a crash does not need a person.
    STALE_AFTER = datetime.timedelta(hours=2)

    name = models.CharField(max_length=64, primary_key=True)
    acquired_at = models.DateTimeField(auto_now_add=True)
    # Free text naming who holds it, for the message the loser is shown and for `./aledb`.
    holder = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return "ImportLock %s held by %s since %s" % (
            self.name, self.holder or "?", self.acquired_at)

    def is_stale(self, now=None):
        return (now or timezone.now()) - self.acquired_at > self.STALE_AFTER


class UploadSession(models.Model):
    """One folder drop, staged on disk until finalized.

    The id is a server-generated UUID and is the only thing that names the staging
    directory, so a client can neither guess another session's area nor influence where
    its own bytes land.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_OPEN)

    # The target is the experiment itself, by primary key. Earlier versions carried the
    # project and experiment as free text and resolved by name, which cannot reach the second
    # of two experiments sharing a name -- see gd_import.prepare_experiment_by_id.
    experiment = models.ForeignKey("aledb_experiment.Experiment", null=True, blank=True,
                                       on_delete=models.CASCADE)
    # Empty means auto-detect; otherwise the name of a registered import handler.
    import_type = models.CharField(max_length=64, blank=True)
    # Set when the session belongs to a component rather than to the import registry -- the
    # app label that opened it. Such a session carries no `import_type`, is never finalized by
    # `finalize_upload` (there is no handler for it), and is claimed by the component through
    # `aledb_import.staging`. Blank is the ordinary import case, which is every session that
    # existed before this field.
    consumer = models.CharField(max_length=64, blank=True)

    # [{"path": "<sample>/data/reference.bam", "size": 123}, ...] as declared by the client.
    # Paths are sanitized before use; this is a record of what was promised, not a trusted map.
    manifest = models.JSONField(default=list)
    declared_bytes = models.BigIntegerField(default=0)
    received_bytes = models.BigIntegerField(default=0)

    # How far `finalize_upload` has got, as {"state", "stage", "units": [...]}, written by
    # `upload_session._SessionProgress` and served by `upload_session.upload_progress`. It
    # lives here rather than in its own table so it is reaped with the session it describes,
    # and it is a snapshot rather than a log because the page it feeds re-renders whole.
    progress = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return "UploadSession %s (%s)" % (self.id, self.state)
