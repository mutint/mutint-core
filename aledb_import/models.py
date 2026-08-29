"""Server-side state for chunked uploads.

A breseq folder drop can be tens of GB, which cannot go through a single POST. The client
declares a manifest, uploads each file in chunks, then asks the server to finalize.

That made every *transfer* request short. It did not make finalize short -- it moved the
entire ingest there, where it is bounded by nothing, which is the case ``WORKERS.md`` records
as having outgrown this design. There is still no task queue, and the repo has neither Celery
nor Channels; what a long finalize has instead is ``progress``, a snapshot the Add page polls
so the wait is legible rather than silent.
"""

import uuid

from django.contrib.auth.models import User
from django.db import models

STATE_OPEN = "open"
STATE_FINALIZED = "finalized"
STATE_FAILED = "failed"

STATE_CHOICES = [
    (STATE_OPEN, "Open"),
    (STATE_FINALIZED, "Finalized"),
    (STATE_FAILED, "Failed"),
]


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

    # The target is the experiment itself, by primary key. Earlier versions carried
    # project/experiment/person as free text and resolved by name, which forked an experiment
    # whenever the person differed -- see gd_import.prepare_experiment_by_id.
    ale_experiment = models.ForeignKey("aledb_experiment.AleExperiment", null=True, blank=True,
                                       on_delete=models.CASCADE)
    # Empty means auto-detect; otherwise the name of a registered import handler.
    import_type = models.CharField(max_length=64, blank=True)

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
