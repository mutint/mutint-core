"""Filesystem layout of the managed store that aledb-core owns and operates.

Every path here is derived from a database primary key. That is the whole point: no
client-supplied path component ever reaches the filesystem, so the traversal exposure that
a client-supplied path would carry cannot exist here.

Layout::

    <store>/experiments/<ale_experiment_pk>/reference/reference.gff3
                                            reference/reference.fasta
                                            reference/reference.fasta.fai
                                            reference/reference.source
    <store>/samples/<resequencing_experiment_pk>/sample.gd
                                                 aligned.bam
                                                 aligned.bam.bai
    <store>/staging/<upload_session_id>/...      (transient; removed on finalize)

Paths are never stored per row: three such columns used to exist and were removed with the
breseq HTML report support, because a stored path travels with
each breseq result. Nothing here is stored in the database.
"""

import os

from django.conf import settings

REFERENCE_GFF3 = "reference.gff3"
REFERENCE_FASTA = "reference.fasta"
REFERENCE_FAI = "reference.fasta.fai"

# The reference exactly as it was supplied, kept verbatim and never hashed.
#
# The normalized gff3/fasta pair above is genes-only and flattened -- that is
# what lets two spellings of one genome hash alike -- so it cannot serve
# annotation, which needs the CDS/tRNA distinction, translation tables,
# pseudogene flags, spliced locations and repeat regions. This is what an
# experiment is re-annotated from. Stored without an extension because the
# format varies; aledb_import.annotate.loader sniffs it.
REFERENCE_SOURCE = "reference.source"

SAMPLE_GD = "sample.gd"
SAMPLE_BAM = "aligned.bam"
SAMPLE_BAI = "aligned.bam.bai"


def store_root():
    return settings.ALEDB_STORE_DIR


def experiment_reference_dir(ale_experiment_id):
    return os.path.join(
        store_root(), "experiments", str(int(ale_experiment_id)), "reference")


def experiment_reference_path(ale_experiment_id, filename):
    """Path to one reference artifact. ``filename`` must be one of the module constants."""
    if filename not in (REFERENCE_GFF3, REFERENCE_FASTA, REFERENCE_FAI, REFERENCE_SOURCE):
        raise ValueError("unknown reference artifact: %r" % (filename,))
    return os.path.join(experiment_reference_dir(ale_experiment_id), filename)


def sample_dir(resequencing_experiment_id):
    return os.path.join(store_root(), "samples", str(int(resequencing_experiment_id)))


def sample_path(resequencing_experiment_id, filename):
    """Path to one sample artifact. ``filename`` must be one of the module constants."""
    if filename not in (SAMPLE_GD, SAMPLE_BAM, SAMPLE_BAI):
        raise ValueError("unknown sample artifact: %r" % (filename,))
    return os.path.join(sample_dir(resequencing_experiment_id), filename)


def staging_dir(upload_session_id):
    """Staging area for one upload session.

    ``upload_session_id`` is a UUID generated server-side, never supplied by the client.
    """
    return os.path.join(store_root(), "staging", str(upload_session_id))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
