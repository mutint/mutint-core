"""Filesystem layout of the managed store that mutint-core owns and operates.

Every path here is derived from a database primary key. That is the whole point: no
client-supplied path component ever reaches the filesystem, so the traversal exposure that
a client-supplied path would carry cannot exist here.

Layout::

    <store>/experiments/<experiment_pk>/reference/reference.gff3
                                            reference/reference.fasta
                                            reference/reference.fasta.fai
    <store>/samples/<sample_pk>/sample.gd
                                                 aligned.bam
                                                 aligned.bam.bai
    <store>/staging/<upload_session_id>/...      (transient; removed on finalize)
    <store>/components/<component>/<key>/...     (a component's own durable work area)

Paths are never stored per row: three such columns used to exist and were removed with the
breseq HTML report support, because a stored path travels with
each breseq result. Nothing here is stored in the database.
"""

import os
import re

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
    return settings.MUTINT_STORE_DIR


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


def sample_report_dir(sample_id):
    """breseq's own HTML report for a sample, as it wrote it.

    A *tree* rather than an artifact, so it has no entry in `sample_path`'s whitelist -- what
    breseq writes there is breseq's business, and the containment is done where the request
    comes in (`mutint_sample/views/report.py`) rather than by naming every file here.

    Inside the sample's own directory deliberately: `purge_deleted` already rmtrees
    `sample_dir`, so this is reaped along with the BAM and the `.gd` and there is no second
    lifecycle to forget about.
    """
    return os.path.join(sample_dir(sample_id), "report")


def staging_dir(upload_session_id):
    """Staging area for one upload session.

    ``upload_session_id`` is a UUID generated server-side, never supplied by the client.
    """
    return os.path.join(store_root(), "staging", str(upload_session_id))


_COMPONENT_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_COMPONENT_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def component_dir(component, key):
    """A component's own durable work area, at ``<store>/components/<component>/<key>/``.

    The one function here that does not name an artifact core owns, because core cannot know
    what a component keeps. What it still owns is the *shape*: ``component`` is an app label
    and ``key`` a primary key, both checked against a pattern with no separator in it, so the
    "no client-supplied path component reaches the filesystem" rule above still holds -- a
    caller cannot reach out of the store by passing a crafted key any more than it can by
    passing one to ``sample_dir``.

    ``key`` is a primary key or another **server-generated** identifier -- ``mutint_jobs``
    keys a job's log by the queue's own result id, which is a UUID the queue made and the only
    handle that exists while the task is running. Both are checked against the pattern below,
    which admits digits, letters, ``_`` and ``-`` and nothing else: no dot, no separator, so
    the "no client-supplied path component reaches the filesystem" rule above still holds. It
    was ``int(key)``, which said the same thing about a primary key and could say nothing at
    all about a UUID.

    Unlike ``staging_dir``, nothing here reaps this. A component that creates one owns
    deleting it, which for a row-keyed directory means a ``post_delete`` receiver on the row.
    """
    if not _COMPONENT_NAME.match(str(component)):
        raise ValueError("component must be an app label: %r" % (component,))
    # The type check is not redundant beside the pattern: `str(None)` is ``"None"``, which
    # matches it, so a caller that lost its key would get a directory called ``None`` shared
    # by every such caller rather than an error.
    if isinstance(key, bool) or not isinstance(key, (int, str)):
        raise TypeError("component key must be a primary key or a generated id: %r" % (key,))
    if not _COMPONENT_KEY.match(str(key)):
        raise ValueError("component key must be a primary key or a generated id: %r" % (key,))
    return os.path.join(store_root(), "components", str(component), str(key))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
