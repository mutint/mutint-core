"""Genome browser for one observed mutation.

Reached from a frequency cell in the mutation table. An ObservedMutation is the right handle
because it is exactly what a cell represents -- one mutation in one sample -- so it carries
both the locus (via `mutation`) and the alignment (via `sequencing_experiment`).

The files themselves come from `aledb_seq.views.alignments`, which serves BAM/BAI and the
reference by primary key with HTTP range support. This view contributes no file access of its
own; it renders the configuration igv.js needs to fetch them.
"""

import logging

from django.http import Http404, HttpResponse
from django.template import loader
from django.urls import reverse

from aledb_common.util import get_user_context
from aledb_experiment.permissions import can_view_project
from aledb_seq.models import ExperimentReference, ObservedMutation

logger = logging.getLogger(__name__)

# How much context to show either side of the mutation. A bare coordinate would put the
# position at the very edge of the view.
LOCUS_FLANK_BASES = 100


def browse_mutation(request):
    """igv.js at one mutation's position, starting with the sample that was clicked."""
    try:
        observed = (ObservedMutation.objects
                    .select_related("mutation", "sequencing_experiment")
                    .get(pk=request.GET.get("observed_mut_id")))
    except (ObservedMutation.DoesNotExist, ValueError, TypeError):
        raise Http404("No such observed mutation.")

    reseq = observed.sequencing_experiment
    if reseq is None:
        raise Http404("This mutation is not attached to a sample.")

    experiment = reseq.ale_experiment
    if not _may_view(request.user, experiment):
        return HttpResponse(
            loader.get_template("403.html").render(get_user_context(request.user), request),
            status=403)

    mutation = observed.mutation
    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        "title": "%s %s:%s" % (experiment.name, mutation.reseq_reference, mutation.position),
        "template_header": "Alignments",
        "mutation": mutation,
        "observed": observed,
        "sample_name": reseq.ale_flask_isolate_str,
        "locus": _locus(mutation),
        # Each state the template renders is decided here rather than in the template, so the
        # reasons stay next to the data that determines them.
        "has_alignment": bool(reseq.bam_stored),
        "reference": _reference_urls(experiment),
        "track": _sample_track(reseq),
        "other_tracks": _other_sample_tracks(experiment, exclude=reseq.id),
    })

    template = loader.get_template("browse/browse.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


def _may_view(user, experiment):
    """Same rule as the alignment routes -- deliberately not a second one."""
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)


def _locus(mutation):
    """`contig:start-end` centred on the mutation.

    `Mutation.reseq_reference` is the GenomeDiff seq_id, i.e. the contig name -- not to be
    confused with `Isolate.reseq_reference`, which is the reference file's name. It matches
    the FASTA's sequence names because both take the first whitespace-delimited token of the
    header, the same rule samtools uses.
    """
    start = max(1, mutation.position - LOCUS_FLANK_BASES)
    end = mutation.position + LOCUS_FLANK_BASES
    return "%s:%d-%d" % (mutation.reseq_reference, start, end)


def _reference_urls(experiment):
    """The igv reference config, or None when the experiment has no reference genome."""
    try:
        reference = experiment.reference
    except ExperimentReference.DoesNotExist:
        return None

    return {
        "id": str(experiment.ale_id),
        "name": experiment.name,
        # `format` is explicit on every URL below: these routes end in /fasta, /fai, /bam and
        # carry no file extension, and igv.js infers format from the extension.
        "fastaURL": reverse("reference_fasta", args=[experiment.ale_id]),
        "indexURL": reverse("reference_fai", args=[experiment.ale_id]),
        "gff3URL": reverse("reference_gff3", args=[experiment.ale_id]),
        "seq_ids": reference.seq_ids,
    }


def _sample_track(reseq):
    """One alignment track, or None when this sample has no stored alignment.

    Only the breseq-folder importer stores a BAM, so a bare .gd or legacy CLI import has
    none. `bam_stored` implies both the BAM and its index exist: the importer rejects a BAM
    without a .bai rather than storing it half-usable.
    """
    if not reseq.bam_stored:
        return None
    return {
        "id": reseq.id,
        "name": reseq.ale_flask_isolate_str,
        # indexURL must be explicit: the store renames breseq's data/reference.bam.bai to
        # aligned.bam.bai, so igv.js's default "<url>.bai" derivation would be wrong.
        "url": reverse("sample_bam", args=[reseq.id]),
        "indexURL": reverse("sample_bai", args=[reseq.id]),
    }


def _other_sample_tracks(experiment, exclude):
    """The experiment's other samples that have alignments, for the "add track" control."""
    from aledb_seq.models import ResequencingExperiment

    others = (ResequencingExperiment.objects
              .filter(tech_rep__isolate__flask__ale_id__ale_experiment__ale_id=experiment.ale_id,
                      bam_stored=True)
              .exclude(id=exclude)
              .select_related("tech_rep__isolate__flask__ale_id__ale_experiment"))
    return [_sample_track(reseq) for reseq in others]
