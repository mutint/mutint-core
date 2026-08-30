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
from aledb_seq.breseq_report import build_rows, is_population
from aledb_seq.locus import LOCUS_BUFFER_BASES, mutation_extent
from aledb_seq.tracks import database_tracks
from aledb_seq.models import ExperimentReference, ObservedMutation
from aledb_seq.util import get_ordered_reseq_queryset

logger = logging.getLogger(__name__)


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
        # For the way back: the per-sample page, on the sample this browser is showing,
        # rather than the cross-experiment comparison it used to land on.
        "reseq_id": reseq.id,
        # The mutation is described by breseq's own table rather than by a sentence of this
        # page's own, so the row reads exactly as it does on the Samples page. One row, and
        # no evidence link -- its destination is the page you are already on.
        "rows": build_rows([observed], refseq_url=_ncbi_url()),
        "is_population": is_population(reseq),
        "locus": _locus(mutation),
        # Each state the template renders is decided here rather than in the template, so the
        # reasons stay next to the data that determines them.
        "has_alignment": bool(reseq.bam_stored),
        "reference": _reference_urls(experiment),
        # The mutations themselves, drawn from the database rather than from a file. Until
        # this existed igv was handed `tracks: []` and the only thing the database
        # contributed was the locus string -- so the page drew the reads and the reference
        # but not the calls the reads were opened to look at.
        "db_tracks": database_tracks(experiment.ale_id, mutation.reseq_reference),
        "samples": _sample_tracks(experiment, mutation, current_id=reseq.id),
    })

    template = loader.get_template("browse/browse.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


def _ncbi_url():
    """Link this row's Reference cell into the NCBI viewer at the same locus.

    The way across from reads to annotation: the pileup answers "what do the data show here"
    and NCBI's viewer answers "what is here", and they are one click apart rather than two
    pages that do not know about each other. No evidence link is passed alongside it, because
    that one's destination is the page you are already on.
    """
    def url_for(observed):
        if not observed.mutation.reseq_reference:
            return None
        return "%s?mutation_id=%s&reseq_id=%s" % (
            reverse("ncbi_view"), observed.mutation_id, observed.sequencing_experiment_id)

    return url_for


def _may_view(user, experiment):
    """Same rule as the alignment routes -- deliberately not a second one."""
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)


def _locus(mutation):
    """`contig:start-end` spanning the whole mutation plus a buffer either side.

    Bounded by the mutation's own extent rather than by its start position, so a 50 kb
    deletion opens showing the deletion rather than 200 bp of its left junction.

    `Mutation.reseq_reference` is the GenomeDiff seq_id, i.e. the contig name -- not to be
    confused with `Isolate.reseq_reference`, which is the reference file's name. It matches
    the FASTA's sequence names because both take the first whitespace-delimited token of the
    header, the same rule samtools uses.
    """
    start, end = mutation_extent(mutation)
    return "%s:%d-%d" % (mutation.reseq_reference,
                         max(1, start - LOCUS_BUFFER_BASES),
                         end + LOCUS_BUFFER_BASES)


# The extent rule and the buffer moved to `aledb_seq.locus` when the NCBI Sequence Viewer
# became a second page drawing the same interval. Kept as a name here because this module's
# tests and readers know it, but there is one implementation.
_extent = mutation_extent


def _reference_urls(experiment):
    """The igv reference config, or None when the experiment has no reference genome."""
    try:
        reference = experiment.reference
    except ExperimentReference.DoesNotExist:
        return None

    config = {
        "id": str(experiment.ale_id),
        "name": experiment.name,
        # `format` is explicit on every URL below: these routes end in /fasta, /fai, /bam and
        # carry no file extension, and igv.js infers format from the extension.
        "fastaURL": reverse("reference_fasta", args=[experiment.ale_id]),
        "indexURL": reverse("reference_fai", args=[experiment.ale_id]),
        "gff3URL": reverse("reference_gff3", args=[experiment.ale_id]),
        # Only the id and length: the per-sequence hashes seq_ids also carries are identity
        # material, and this dict is rendered into the page for anyone who can see it.
        "seq_ids": [{"id": entry["id"], "length": entry["length"]}
                    for entry in reference.seq_ids],
    }

    # Only when a sequence has actually been renamed. An experiment that never has one gains
    # no extra request, and igv treats a missing aliasURL as "names are already right".
    if any(entry.get("aliases") for entry in reference.seq_ids):
        config["aliasURL"] = reverse("reference_chromalias", args=[experiment.ale_id])
    return config


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
        # The coverage BigWig, when one has been derived. Samples imported before coverage
        # existed have none until `./aledb coverage` runs, and the page simply gives them no
        # coverage track.
        "coverageURL": (reverse("sample_bigwig", args=[reseq.id])
                        if reseq.coverage_stored else None),
    }


def _sample_tracks(experiment, mutation, current_id):
    """Every sample in the experiment with an alignment, for the sample menu.

    The sample being viewed is in the list like any other, marked `is_current` only so the
    template can check its box: it is shown and hidden by the same control as the rest.

    Ordered by `get_ordered_reseq_queryset` rather than by a query of this view's own, so the
    menu reads in the same A/F/I/R order as the mutation table's columns and the Samples
    page's picker.
    """
    called = _samples_calling(mutation)
    return [dict(_sample_track(reseq),
                 has_mutation=reseq.id in called,
                 is_current=reseq.id == current_id)
            # `include_ancestor=True`: the browser inspects evidence rather than analysing
            # it, and the ancestor's own evidence link lands here. Hiding its track would
            # leave `is_current` matching nothing on the very sample that was clicked.
            for reseq in get_ordered_reseq_queryset(
                experiment.ale_id, include_ancestor=True).filter(bam_stored=True)]


def _samples_calling(mutation):
    """Ids of the samples this mutation is recorded in.

    `present=True` is the mutation table's own rule for a cell being filled
    (`mutation_table_builder._get_table_mutation_entry`), and it is reused rather than
    restated so the menu's `*` marks exactly the samples whose cells are filled in there. An
    ObservedMutation row on its own is not enough: one with `present=False` records that the
    mutation was looked for in that sample and found absent, and one with `present` null
    records nothing either way.
    """
    return set(ObservedMutation.objects
               .filter(mutation=mutation, present=True)
               .values_list("sequencing_experiment_id", flat=True))
