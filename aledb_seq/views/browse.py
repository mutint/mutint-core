"""Genome browser for one mutation in one sample.

Reached from a frequency cell in the mutation table, which is exactly that pair -- so an
`ObservedMutation` carries both the locus (via `mutation`) and the alignment (via
`sample`), and `?observed_mut_id=` is how every link here is written.

It is not the only way in any more. Clicking a mutation on the Mutations track switches the
page to it, and that track draws every mutation in the *experiment* -- including ones the
sample on screen does not call, which have no ObservedMutation to name. `_resolve` takes
`?mutation_id=&reseq_id=` for those, and `browse_at` is what the click actually calls.

The files themselves come from `aledb_seq.views.alignments`, which serves BAM/BAI and the
reference by primary key with HTTP range support. This view contributes no file access of its
own; it renders the configuration igv.js needs to fetch them.
"""

import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.template import loader
from django.urls import reverse
from django.utils.http import urlencode

from aledb_common.util import get_user_context
from aledb_experiment.permissions import can_view_project
from aledb_seq.breseq_report import build_rows, is_mixed
from aledb_seq.locus import LOCUS_BUFFER_BASES, mutation_extent
from aledb_seq.tracks import MUTATION_TRACK_ID, database_tracks
from aledb_seq.models import (ExperimentReference, Mutation, ObservedMutation,
                              Sample)
from aledb_seq.util import get_observed_mutation_queryset, get_ordered_reseq_queryset
from aledb_experiment import paths

logger = logging.getLogger(__name__)


def _resolve(request):
    """`(reseq, mutation, observed)` from either spelling of this page's address.

    Two spellings, because the page is about a mutation *in a sample* and only one of those
    pairs is always a stored row:

    - `?observed_mut_id=` -- an `ObservedMutation`, and what every link into this page uses.
    - `?mutation_id=&reseq_id=` -- a mutation and a sample named separately, which is the
      only way to address a mutation the sample does **not** call. Clicking a feature on the
      Mutations track reaches exactly that: the track draws every mutation in the experiment,
      and the sample whose reads are on screen carries only some of them.

    `observed` is None in that last case, and callers hand `_row_observation` an unsaved
    instance rather than growing a second rendering path -- see there.

    The pair is checked to be one experiment's. Two ids arrive from the client and nothing
    else stops a mutation from one experiment being asked for beside a sample from another,
    which would render a row about a locus the reads cannot contain.
    """
    observed_id = request.GET.get("observed_mut_id")
    if observed_id:
        try:
            observed = (ObservedMutation.objects
                        .select_related("mutation", "sample")
                        .get(pk=observed_id))
        except (ObservedMutation.DoesNotExist, ValueError, TypeError):
            raise Http404("No such observed mutation.")
        if observed.sample is None:
            raise Http404("This mutation is not attached to a sample.")
        return observed.sample, observed.mutation, observed

    try:
        # `experiment` is a property over time_point -> population, not a
        # column, so the chain is named the way `aledb_seq.util` names it.
        reseq = (Sample.objects
                 .select_related(paths.to_experiment())
                 .get(pk=request.GET.get("reseq_id")))
        mutation = Mutation.objects.get(pk=request.GET.get("mutation_id"))
    except (Sample.DoesNotExist, Mutation.DoesNotExist, ValueError, TypeError):
        raise Http404("No such mutation or sample.")

    # Reached through the observations rather than through `Mutation.experiment`, which
    # may be null -- the unscoped case `permissions.can_curate` exists for -- and would refuse
    # a mutation this experiment plainly observes. `get_observed_mutation_queryset` is the
    # shared spelling of "observed in this experiment".
    #
    # Deliberately the *raw* queryset, so what is accepted is a superset of what the Mutations
    # track draws: the track subtracts the ancestor, while this page shows an ancestral
    # mutation quite happily when a table links to one. Being stricter here would refuse an
    # address the rest of the product hands out.
    if not get_observed_mutation_queryset(
            reseq.experiment.id).filter(mutation=mutation).exists():
        raise Http404("That mutation is not in this sample's experiment.")

    observed = (ObservedMutation.objects
                .filter(mutation=mutation, sample=reseq).first())
    return reseq, mutation, observed


def _row_observation(reseq, mutation, observed):
    """What `build_rows` is handed, which is an ObservedMutation even when there is none.

    A mutation the sample does not call has no row to show, and an **unsaved**
    `ObservedMutation` renders correctly with no change to `breseq_report`: `_frequency`
    already answers `("", False)` for a null frequency, so the Freq cell comes out empty, and
    `_ncbi_url` reads `mutation_id` and `sample_id`, which are both set on it.

    Nothing says "not called here" beside it. The Samples menu already answers that -- the
    `*` flags follow the mutation, so the current sample simply appears without one.
    """
    if observed is not None:
        return observed
    return ObservedMutation(mutation=mutation, sample=reseq)


def browse_url_for(mutation, reseq, observed):
    """This page's address for a mutation in a sample, in whichever spelling fits.

    The stored-observation spelling is preferred where there is one, so a link copied out of
    the address bar is the same one the mutation tables hand out.
    """
    if observed is not None and observed.pk:
        return "%s?%s" % (reverse("browse_mutation"),
                          urlencode({"observed_mut_id": observed.pk}))
    return "%s?%s" % (reverse("browse_mutation"),
                      urlencode({"mutation_id": mutation.pk, "reseq_id": reseq.pk}))


def browse_mutation(request):
    """igv.js at one mutation's position, starting with the sample that was clicked."""
    reseq, mutation, observed = _resolve(request)

    experiment = reseq.experiment
    if not _may_view(request.user, experiment):
        return HttpResponse(
            loader.get_template("403.html").render(get_user_context(request.user), request),
            status=403)

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
        "rows": build_rows([_row_observation(reseq, mutation, observed)],
                           refseq_url=_ncbi_url()),
        # Which samples call it, for the menu's `*` -- and read back by the switch endpoint,
        # so the flags mean the same thing after a click as they did on load.
        "calling": sorted(_samples_calling(mutation)),
        "is_mixed": is_mixed(reseq),
        "locus": _locus(mutation),
        # Each state the template renders is decided here rather than in the template, so the
        # reasons stay next to the data that determines them.
        "has_alignment": bool(reseq.bam_stored),
        "reference": _reference_urls(experiment),
        # The mutations themselves, drawn from the database rather than from a file. Until
        # this existed igv was handed `tracks: []` and the only thing the database
        # contributed was the locus string -- so the page drew the reads and the reference
        # but not the calls the reads were opened to look at.
        "db_tracks": database_tracks(experiment.id, mutation.reseq_reference),
        # Named here rather than written out in the template, so the click handler and the
        # track config cannot come to disagree about which track is the clickable one.
        "mutations_track_id": MUTATION_TRACK_ID,
        "samples": _sample_tracks(experiment, mutation, current_id=reseq.id),
    })

    template = loader.get_template("browse/browse.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


def browse_at(request):
    """The page's state for a different mutation, as JSON, without reloading the browser.

    Clicking a feature on the Mutations track switches which mutation this page is about. A
    navigation would do it too, and would rebuild igv and re-fetch the BAM to show a locus
    already on screen -- so the page swaps the parts that changed and leaves igv alone.

    What comes back is rendered by the *same* `build_rows` call and the same template the
    full page uses, so a switched-to row cannot drift from a loaded one; `calling` is the
    same `_samples_calling` the menu's `*` flags were built from.

    A GET, and it writes nothing. `_resolve` and `_may_view` are shared with the page, so
    there is one answer to "does this pair exist" and one to "may you see it".
    """
    reseq, mutation, observed = _resolve(request)

    if not _may_view(request.user, reseq.experiment):
        return JsonResponse({"error": "You do not have access to this experiment."}, status=403)

    rows = build_rows([_row_observation(reseq, mutation, observed)], refseq_url=_ncbi_url())
    table_html = loader.get_template("breseq_table/_mutation_table.html").render(
        {"rows": rows, "is_mixed": is_mixed(reseq), "empty_message": ""}, request)

    return JsonResponse({
        "mutation_id": mutation.pk,
        "observed_mut_id": observed.pk if observed is not None else None,
        "url": browse_url_for(mutation, reseq, observed),
        "title": "%s %s:%s" % (reseq.experiment.name,
                               mutation.reseq_reference, mutation.position),
        "calling": sorted(_samples_calling(mutation)),
        "table_html": table_html,
    })


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
            reverse("ncbi_view"), observed.mutation_id, observed.sample_id)

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
    confused with the sample's `reseq_reference`, which is the reference file's name. It matches
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
        "id": str(experiment.id),
        "name": experiment.name,
        # `format` is explicit on every URL below: these routes end in /fasta, /fai, /bam and
        # carry no file extension, and igv.js infers format from the extension.
        "fastaURL": reverse("reference_fasta", args=[experiment.id]),
        "indexURL": reverse("reference_fai", args=[experiment.id]),
        "gff3URL": reverse("reference_gff3", args=[experiment.id]),
        # Only the id and length: the per-sequence hashes seq_ids also carries are identity
        # material, and this dict is rendered into the page for anyone who can see it.
        "seq_ids": [{"id": entry["id"], "length": entry["length"]}
                    for entry in reference.seq_ids],
    }

    # Only when a sequence has actually been renamed. An experiment that never has one gains
    # no extra request, and igv treats a missing aliasURL as "names are already right".
    if any(entry.get("aliases") for entry in reference.seq_ids):
        config["aliasURL"] = reverse("reference_chromalias", args=[experiment.id])
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
                experiment.id, include_ancestor=True).filter(bam_stored=True)]


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
               .values_list("sample_id", flat=True))
