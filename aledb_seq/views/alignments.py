"""Serve BAM/BAI and reference files from the managed store.

Every path here is derived from a primary key, so no client-supplied path component reaches
the filesystem, and authorization uses the model that actually exists -- ``can_view_project``.

The streaming and byte-range machinery lives in ``aledb_common.fileserve``.
"""

from django.http import Http404, HttpResponseForbidden

from aledb_common import store
from aledb_common.fileserve import serve_file
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_view_project
from aledb_seq.models import ResequencingExperiment


def sample_bam(request, reseq_id):
    return _serve_sample(request, reseq_id, store.SAMPLE_BAM)


def sample_bai(request, reseq_id):
    return _serve_sample(request, reseq_id, store.SAMPLE_BAI)


def reference_fasta(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_FASTA)


def reference_fai(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_FAI)


def reference_gff3(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_GFF3)


def _serve_sample(request, reseq_id, filename):
    try:
        reseq = ResequencingExperiment.objects.get(pk=reseq_id)
    except ResequencingExperiment.DoesNotExist:
        raise Http404("No such resequencing experiment.")

    experiment = reseq.ale_experiment
    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    return serve_file(request, store.sample_path(reseq.id, filename), filename)


def _serve_reference(request, experiment_id, filename):
    try:
        experiment = AleExperiment.objects.get(pk=experiment_id)
    except AleExperiment.DoesNotExist:
        raise Http404("No such experiment.")

    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    return serve_file(
        request, store.experiment_reference_path(experiment.ale_id, filename), filename)


def _may_view(user, experiment):
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)
