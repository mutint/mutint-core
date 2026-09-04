"""Filesystem layout of the managed store that aledb-core owns and operates.

Every path here is derived from a database primary key. That is the whole point: no
client-supplied path component ever reaches the filesystem, so the traversal exposure that
a client-supplied path would carry cannot exist here.

Layout::

    <store>/experiments/<ale_experiment_pk>/reference/reference.gff3
                                            reference/reference.fasta
                                            reference/reference.fasta.fai
    <store>/samples/<sample_pk>/sample.gd
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


SAMPLE_GD = "sample.gd"
SAMPLE_BAM = "aligned.bam"
SAMPLE_BAI = "aligned.bam.bai"
# Read depth across the whole reference, derived from the BAM at import. BigWig carries its
# own zoom levels, so this one file answers a whole-genome view and a base-pair one alike --
# which is the point, because igv's own coverage row is part of the alignment track and is
# gated away with the reads past the track's visibility window.
SAMPLE_BIGWIG = "coverage.bw"


def store_root():
    return settings.ALEDB_STORE_DIR


def experiment_reference_dir(experiment_id):
    return os.path.join(
        store_root(), "experiments", str(int(experiment_id)), "reference")


def experiment_reference_path(experiment_id, filename):
    """Path to one reference artifact. ``filename`` must be one of the module constants."""
    if filename not in (REFERENCE_GFF3, REFERENCE_FASTA, REFERENCE_FAI):
        raise ValueError("unknown reference artifact: %r" % (filename,))
    return os.path.join(experiment_reference_dir(experiment_id), filename)


def sample_dir(sample_id):
    return os.path.join(store_root(), "samples", str(int(sample_id)))


def sample_path(sample_id, filename):
    """Path to one sample artifact. ``filename`` must be one of the module constants."""
    if filename not in (SAMPLE_GD, SAMPLE_BAM, SAMPLE_BAI, SAMPLE_BIGWIG):
        raise ValueError("unknown sample artifact: %r" % (filename,))
    return os.path.join(sample_dir(sample_id), filename)


def staging_dir(upload_session_id):
    """Staging area for one upload session.

    ``upload_session_id`` is a UUID generated server-side, never supplied by the client.
    """
    return os.path.join(store_root(), "staging", str(upload_session_id))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
