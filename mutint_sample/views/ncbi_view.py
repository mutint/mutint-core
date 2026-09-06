"""One mutation drawn in NCBI's annotation, by NCBI's own Sequence Viewer.

The companion to `browse.py`. That page shows a mutation as *reads* -- the sample's own
pileup against the reference the experiment was called on. This one shows it as a *locus*:
the genes, operons and features NCBI curates around it, which the stored GFF3 gene track
cannot supply because it only carries what breseq's reference happened to annotate.

A **Mutation** is the handle here rather than a MutationCall, which is the one structural
difference from browse. That page needs a sample because it draws that sample's reads; this
one draws no sample data at all, and the Reference Seq cell it is reached from is a property
of the mutation's row rather than of any column. `sample_id` is accepted and used only to keep
the way back pointing at the sample somebody came from.

Nothing is drawn until the contig has been verified against NCBI -- see `mutint_sample.ncbi`. The
whole risk this page carries is that a near-miss accession renders a completely convincing
picture of the wrong gene, so an unverified contig gets an explanation and no viewer.
"""

import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.template import loader
from django.views.decorators.http import require_POST

from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment, can_view_project
from mutint_sample import ncbi
from mutint_sample.breseq_report import build_rows, is_mixed
from mutint_sample.locus import buffered_extent, mutation_extent
from mutint_sample.views.common import get_experiment, no_experiment_selected
from mutint_sample.models import (DatabaseSequenceLink, ReferenceSequences, Mutation,
                                 MutationCall)

logger = logging.getLogger(__name__)

#: The marker drawn over the mutation's own extent, as RGB without a leading '#'. Red, to
#: read as "this is the thing you came to look at" against NCBI's own blue-gray feature
#: colors rather than blending into them.
MARKER_COLOR = "cc0000"


def ncbi_view(request):
    """The NCBI Sequence Viewer at one mutation's locus."""
    try:
        mutation = (Mutation.objects
                    .select_related("experiment")
                    .get(pk=request.GET.get("mutation_id")))
    except (Mutation.DoesNotExist, ValueError, TypeError):
        raise Http404("No such mutation.")

    experiment = mutation.experiment
    if not _may_view(request.user, experiment):
        return HttpResponse(
            loader.get_template("403.html").render(get_user_context(request.user), request),
            status=403)

    contig = ncbi.contig_entry(experiment, mutation.seq_id)
    record = ncbi.record_for(contig["sha256"]) if contig else None

    context = get_user_context(request.user)
    if experiment is not None:
        context.update(experiment.experiment_context())
    context.update({
        "ale_project_name": (experiment.project.name
                             if experiment is not None and experiment.project else ""),
        "ale_project_id": experiment.project_id if experiment is not None else None,
        "title": "%s %s:%s" % (experiment.name if experiment is not None else "Mutation",
                               mutation.seq_id, mutation.start_position),
        "template_header": "Reference annotation",
        "mutation": mutation,
        "contig_name": mutation.seq_id or "",
        # For the way back to the sample somebody arrived from, when they arrived from one.
        "sample_id": _sample_id(request),
        # breseq's own row, as browse does, so the page states which mutation it is showing
        # in the same words every other table uses.
        "rows": build_rows([_any_call(mutation)]) if _any_call(mutation) else [],
        "is_mixed": False,
        # Every state the template renders is decided here rather than in the template, so
        # the reasons stay beside the data that determines them.
        "has_reference": contig is not None,
        "record": record,
        "status": record.status if record else DatabaseSequenceLink.UNCHECKED,
        "detail": record.detail if record else "",
        "accession": record.accession if record else "",
        "is_verified": bool(record and record.is_verified),
        "may_check": _may_check(request.user, experiment),
        "sviewer": _sviewer_params(mutation, contig, record),
    })

    template = loader.get_template("ncbi/ncbi_view.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


@require_POST
def ncbi_check(request):
    """Record an accession for one contig and verify it against NCBI.

    Keyed on `(experiment_id, seq_id)` rather than on a mutation, because an accession
    is a property of the reference: the Reference page has no mutation to name, and the
    mutation page knows both anyway. One endpoint, one contract.

    Gated on `can_edit_experiment`, not `can_edit_project`: this writes, and a predicate
    handed the project cannot see the lock that lives on the experiment.
    """
    try:
        experiment = Experiment.objects.get(pk=request.POST.get("experiment_id"))
    except (Experiment.DoesNotExist, ValueError, TypeError):
        raise Http404("No such experiment.")

    if not _may_view(request.user, experiment):
        return JsonResponse({"error": "You do not have access to this experiment."}, status=403)
    if not _may_check(request.user, experiment):
        return JsonResponse(
            {"error": "You need write access to this experiment, and it must not be locked."},
            status=403)

    seq_id = (request.POST.get("seq_id") or "").strip()
    contig = ncbi.contig_entry(experiment, seq_id)
    if contig is None:
        return JsonResponse(
            {"error": "This experiment has no stored reference sequence named %s."
                      % (seq_id or "(none given)")}, status=400)

    accession = (request.POST.get("accession") or "").strip()
    if not accession:
        return JsonResponse({"error": "Enter an NCBI accession to check."}, status=400)

    record = ncbi.check_and_store(contig["sha256"], contig["length"], accession,
                                  user=request.user)
    return JsonResponse({
        "seq_id": seq_id,
        "status": record.status,
        "accession": record.accession,
        "detail": record.detail,
        "verified": record.is_verified,
    })


def _may_view(user, experiment):
    """Same rule as browse and the alignment routes -- deliberately not a second one."""
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)


def _may_check(user, experiment):
    """Who may state an accession. Lock-aware, because this writes."""
    if experiment is None:
        return False
    return can_edit_experiment(user, experiment)


def _sample_id(request):
    try:
        return int(request.GET.get("sample_id"))
    except (TypeError, ValueError):
        return None


def _any_call(mutation):
    """One call of this mutation, purely so the row can be rendered.

    The page is about the mutation rather than about a sample, but `build_rows` describes
    calls -- so any of them will render the same mutation columns. Deliberately not
    the sample from `sample_id`: the row must read identically however the page was reached.
    """
    return (MutationCall.objects
            .select_related("mutation", "sample")
            .filter(mutation=mutation).order_by("pk").first())


def _sviewer_params(mutation, contig, record):
    """The embedding parameters, or None when there is nothing verified to draw.

    Built here rather than in the template so the coordinate arithmetic sits beside the
    interval rule it uses. Both `v=` and `mk=` are 1-based inclusive, as GenomeDiff positions
    are, so no conversion happens anywhere -- an off-by-one here is invisible and would point
    at the neighbouring base.
    """
    if not (record and record.is_verified and contig):
        return None

    start, end = mutation_extent(mutation)
    low, high = buffered_extent(mutation, contig_length=contig["length"])
    label = "%s %s" % (mutation.mutation_type or "mutation", mutation.sequence_change or "")

    return {
        "id": record.accession,
        "view": "%d:%d" % (low, high),
        # position|name|color. The name is sanitised because a sequence change can carry
        # the delimiters this syntax is built from.
        "marker": "%d:%d|%s|%s" % (start, end, _marker_label(label), MARKER_COLOR),
        "start": start,
        "end": end,
    }


def _marker_label(text):
    """A marker name safe to put in NCBI's `mk=` syntax.

    `|` separates the marker's fields and `,` separates markers, so a sequence change
    containing either would silently produce a different marker than intended -- or none.
    The delimiters are written literally into the href rather than percent-encoded, which is
    what makes this sanitising load-bearing rather than belt-and-braces: it is the only thing
    standing between a mutation's own text and NCBI's parameter syntax.
    """
    cleaned = (text or "").strip()
    for character in ("|", ",", "!", ":", "&", "<", ">", '"', "'", "#", "%", "+", "="):
        cleaned = cleaned.replace(character, " ")
    # Underscores rather than spaces: this ends up inside an href written without
    # percent-encoding, and a raw space there is left to the browser to normalize.
    return "_".join(cleaned.split())[:60] or "mutation"


def reference_view(request):
    """What reference genome an experiment is called against, and what NCBI record each
    contig is.

    Nothing in the product showed this before: the Add Data page knew only whether a
    reference existed, as a yes/no, and the contigs, their lengths, the names they used to
    have and whether any of them had been matched to NCBI were visible nowhere at all.

    It is also where an accession is *stated*, and that is the reason this page exists rather
    than the form living only on the mutation page. An accession belongs to the reference,
    not to any one mutation -- and a form reachable only from a link that appears once the
    work is already done is a form nobody can reach the first time.
    """
    context = get_user_context(request.user)
    try:
        experiment = get_experiment(request)
    except Experiment.DoesNotExist:
        return no_experiment_selected(request, context, logger, "reference genome")

    contigs = ncbi.contig_states(experiment)
    context.update(experiment.experiment_context())
    context.update({
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        "title": "%s reference" % experiment.name,
        "template_header": "Reference",
        "contigs": contigs,
        "has_reference": bool(contigs),
        "total_length": format(sum(contig["length"] for contig in contigs), ",d"),
        "verified_count": sum(1 for contig in contigs if contig["is_verified"]),
        "may_check": _may_check(request.user, experiment),
    })

    template = loader.get_template("ncbi/reference.html")
    return HttpResponse(template.render(context, request), content_type="text/html")
