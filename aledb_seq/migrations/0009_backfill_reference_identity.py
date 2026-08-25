"""Compute `sequence_sha256` and enrich `seq_ids` for references that predate them.

Identity used to be `fasta_sha256`, the hash of the rendered FASTA -- which carries `>seq_id`
headers, so the same genome under different contig names hashed differently. Identity is now
the bases alone, and the per-sequence hashes that make a rename possible live in `seq_ids`.
Both are recomputed here from each reference's stored FASTA.

Three things this deliberately tolerates, because a migration that stops is worse than a row
it leaves alone:

* no store configured -- returns immediately, which is also what happens under the test runner;
* a reference row whose files are not on disk -- at least one real deployment has one. Its
  `sequence_sha256` stays blank, and `matches_sequence` falls back to the old comparison for
  it, which is exactly how that row behaved before this column existed;
* a FASTA that will not parse -- same treatment.

It also repairs `seq_ids` and `total_length` from the file while it is there. Those could be
stale: `establish_or_check` refreshed them only when it rewrote the store, and under the old
identity that was invisible, because a matching `fasta_sha256` implied matching ids, lengths
and order. It stops being invisible the moment names can move.

The schema is reversible; a rename performed later is not.
"""

import os

from django.conf import settings
from django.db import migrations

from aledb_seq.migrations._reference_identity import identity_from_fasta

BATCH = 1000


def backfill(apps, schema_editor):
    ExperimentReference = apps.get_model("aledb_seq", "ExperimentReference")

    store_root = getattr(settings, "ALEDB_STORE_DIR", None)
    if not store_root:
        return

    pending = []
    for reference in ExperimentReference.objects.all().iterator(chunk_size=BATCH):
        # The layout is hardcoded rather than taken from aledb_common.store for the same
        # reason the digests are inlined: a migration must not re-interpret history when
        # the app's idea of where files live changes.
        path = os.path.join(store_root, "experiments", str(reference.ale_experiment_id),
                            "reference", "reference.fasta")
        identity = identity_from_fasta(path)
        if identity is None:
            continue
        reference.sequence_sha256, reference.seq_ids, reference.total_length = identity
        pending.append(reference)
        if len(pending) >= BATCH:
            ExperimentReference.objects.bulk_update(
                pending, ["sequence_sha256", "seq_ids", "total_length"])
            pending = []
    if pending:
        ExperimentReference.objects.bulk_update(
            pending, ["sequence_sha256", "seq_ids", "total_length"])


def unbackfill(apps, schema_editor):
    """Nothing to undo: 0008's reverse drops the column, and the `seq_ids` enrichment is
    additive -- every reader takes `["id"]` or the whole list."""


class Migration(migrations.Migration):

    dependencies = [("aledb_seq", "0008_experimentreference_sequence_sha256")]

    operations = [migrations.RunPython(backfill, unbackfill)]
