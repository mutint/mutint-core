"""Serve BAM/BAI and reference files from the managed store.

Every path here is derived from a primary key, so no client-supplied path component reaches
the filesystem, and authorization uses the model that actually exists -- ``can_view_project``.

The streaming and byte-range machinery lives in ``aledb_common.fileserve``.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse, HttpResponseForbidden

from aledb_common import store
from aledb_common.fileserve import serve_file
from aledb_experiment.models import Experiment
from aledb_experiment.permissions import can_view_project
from aledb_seq.models import Sample


def sample_bam(request, sample_id):
    return _serve_sample(request, sample_id, store.SAMPLE_BAM)


def sample_bai(request, sample_id):
    return _serve_sample(request, sample_id, store.SAMPLE_BAI)


def sample_bigwig(request, sample_id):
    """The sample's coverage track. `.bw` falls through to application/octet-stream, which is
    what igv wants, and BigWig is unreadable without the byte ranges serve_file already does --
    igv fetches its header and R-tree index by range before any data."""
    return _serve_sample(request, sample_id, store.SAMPLE_BIGWIG)


def reference_fasta(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_FASTA)


def reference_fai(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_FAI)


def reference_gff3(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_GFF3)


def reference_chromalias(request, experiment_id):
    """igv's chromosome alias table, so a renamed contig still finds its alignments.

    Renaming an experiment's sequences renames the reference and the mutations, but the
    stored BAMs and coverage BigWigs keep the names they were built with -- their `@SQ`
    headers and chromosome B-trees are baked in, and rewriting 6 GB of them is not what a
    rename should cost. igv resolves the difference itself: both readers ask the genome for
    an alias record and match it against the names the file actually carries
    (`getAliasName` for alignments, `getIdForChr` for BigWig).

    The format is igv's own: an optional `#` header naming the columns, then one
    tab-separated row per sequence. igv takes as canonical whichever field is in the
    genome's chromosomeNames, so the current name must come first.
    """
    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        raise Http404("No such experiment.")

    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    try:
        reference = experiment.reference
    except ObjectDoesNotExist:
        raise Http404("This experiment has no reference genome.")

    return HttpResponse(chromalias_text(reference.seq_ids),
                        content_type="text/plain; charset=utf-8")


def chromalias_text(seq_ids):
    """The alias table for `seq_ids`, or "" when no sequence has ever been renamed.

    Kept separate from the view so the rename path can test the table it produces without
    going through HTTP.
    """
    rows = [entry for entry in seq_ids if entry.get("aliases")]
    if not rows:
        return ""
    lines = ["#name\tprevious"]
    for entry in rows:
        for alias in entry["aliases"]:
            lines.append("%s\t%s" % (entry["id"], alias))
    return "\n".join(lines) + "\n"


def _serve_sample(request, sample_id, filename):
    try:
        reseq = Sample.objects.get(pk=sample_id)
    except Sample.DoesNotExist:
        raise Http404("No such resequencing experiment.")

    experiment = reseq.experiment
    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    return serve_file(request, store.sample_path(reseq.id, filename), filename)


def _serve_reference(request, experiment_id, filename):
    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        raise Http404("No such experiment.")

    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    return serve_file(
        request, store.experiment_reference_path(experiment.id, filename), filename)


def _may_view(user, experiment):
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)
