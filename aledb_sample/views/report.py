"""Serving breseq's own HTML report for one sample.

**This is the only HTML in the product that ALEdb did not write.** breseq generates it from
things a user supplied -- the sample's name, its read filenames, the reference's gene names and
products -- so it must be treated as untrusted markup even though breseq is trusted software.
Served plainly on our own origin, a `<script>` smuggled through a gene name would run with the
reader's session, read the CSRF token and act as them.

So it is served **sandboxed, twice over**, and the two must agree:

- the viewer frames it with ``sandbox=SANDBOX_FLAGS`` and deliberately **no
  ``allow-same-origin``**, which is what gives the document an opaque origin;
- every file response carries ``Content-Security-Policy: sandbox <the same flags>``, which is
  the only thing that covers a reader who pastes the raw URL, or follows one of breseq's own
  ``target="_top"`` links, and gets a *top-level* document with no frame around it.

**The two lists intersect rather than combine.** A header narrower than the attribute silently
removes what the attribute allowed, and the symptom is "the evidence links stopped working"
with nothing anywhere naming the cause. `SANDBOX_FLAGS` is one string used in both places, and
a test asserts the response carries exactly it.

Why each flag is in that string:

- ``allow-scripts`` -- not optional. breseq 0.50's `evidence.html` is a JavaScript application:
  it bundles JSZip and pako inline, embeds every evidence page, SVG and PNG as one base64 ZIP,
  and unzips whichever the URL *fragment* names. Without scripts the page is blank. (Verified
  in headless Chrome under exactly this CSP: the ZIP decodes, a 72 KB read-alignment page
  renders into a nested `srcdoc` frame, and images inline as `data:` URIs.)
- ``allow-top-navigation-by-user-activation`` -- the evidence pages carry
  ``<base target="_top">``, so their cross-links navigate the top window. Without this every
  link inside an evidence page silently does nothing. *By user activation* is the point: a
  click works, a script redirecting the page on its own does not.
- ``allow-downloads`` -- ``output.gd`` and ``log.txt`` are linked from the report and are worth
  being able to save.

``allow-same-origin`` is the one that must never be added. With it the sandbox stops isolating
anything, because the frame is then same-origin with us and its scripts can reach our DOM.
"""

import logging
import os

from django.http import Http404
from django.shortcuts import render

from aledb_common import store
from aledb_common.fileserve import serve_file
from aledb_common.util import get_user_context
from aledb_experiment.permissions import can_view_project
from aledb_sample.models import Sample

logger = logging.getLogger("aledb_sample.report")

#: The sandbox, in one place. Used verbatim as the iframe's `sandbox` attribute and inside the
#: `Content-Security-Policy: sandbox ...` header, because the browser intersects the two.
SANDBOX_FLAGS = "allow-scripts allow-top-navigation-by-user-activation allow-downloads"

#: What every file of the report is served with.
REPORT_HEADERS = {
    "Content-Security-Policy": "sandbox %s" % SANDBOX_FLAGS,
    # A `.png` whose bytes are HTML must not be sniffed into a document. The report's
    # filenames come from breseq, but its *contents* include names a user chose.
    "X-Content-Type-Options": "nosniff",
}

#: What the link bar offers, in the order breseq's own header does. `evidence.html` is not
#: listed: it is reached from a row, never opened on its own -- with no fragment it says
#: "No evidence file specified in URL hash."
REPORT_PAGES = (
    ("index.html", "breseq report"),
    ("summary.html", "summary"),
    ("marginal.html", "marginal predictions"),
)


def _sample_or_404(request, sample_id):
    """The sample, if this reader may see it and it has a report.

    404 rather than 403 for a sample somebody may not read, so the route cannot be used to
    find out which sample ids exist -- the posture `job_cancel` and the VCF export take.
    """
    sample = (Sample.objects
              .filter(pk=sample_id)
              .select_related("population__experiment__project")
              .first())
    if sample is None or not sample.report_stored:
        raise Http404("No breseq report for that sample.")

    experiment = getattr(getattr(sample, "population", None), "experiment", None)
    if not can_view_project(request.user, getattr(experiment, "project", None)):
        raise Http404("No breseq report for that sample.")
    return sample


def report(request, sample_id):
    """The viewer: ALEdb's chrome, a link bar, and the report in a sandboxed frame."""
    sample = _sample_or_404(request, sample_id)
    experiment = sample.population.experiment

    page = request.GET.get("page") or "index.html"
    if page not in dict(REPORT_PAGES):
        # Only the pages the bar offers. A free-form `?page=` would be a second, looser way
        # into the same files than the contained route below, which is the one that is tested.
        page = "index.html"

    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "experiment_id": experiment.id,
        "sample": sample,
        "sample_label": sample.label,
        "pages": [{"file": name, "label": label, "current": name == page}
                  for name, label in REPORT_PAGES if _has(sample, name)],
        "page": page,
        "frame_url": "/mutations/report/%d/files/%s" % (sample.pk, page),
        "sandbox_flags": SANDBOX_FLAGS,
    })
    return render(request, "report/report.html", context)


def report_file(request, sample_id, path):
    """One file out of the report, contained and sandboxed.

    The only route in core that serves a client-named path, so containment is the whole job:
    the resolved `realpath` must land inside this sample's own report directory. `realpath`
    on both sides is what makes a symlink *inside* the report pointing outward fail too --
    breseq does not write symlinks, but the report is whatever was uploaded.
    """
    sample = _sample_or_404(request, sample_id)

    root = os.path.realpath(store.sample_report_dir(sample.pk))
    candidate = os.path.realpath(os.path.join(root, path))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise Http404("No such file.")

    return serve_file(request, candidate, os.path.basename(candidate),
                      headers=REPORT_HEADERS)


def _has(sample, name):
    return os.path.isfile(os.path.join(store.sample_report_dir(sample.pk), name))


def has_report(sample):
    """Whether this sample has something for the link bar to point at.

    Reads the flag rather than the disk: the per-sample mutation table calls this while
    rendering, and a stat per page load to answer a question the column already answers is
    the kind of thing that is free until the store is on a network filesystem.
    """
    return bool(sample is not None and sample.report_stored)
