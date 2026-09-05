"""Serving breseq's own HTML report for one sample.

**This is the only HTML in the product that MutInt did not write.** breseq generates it from
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

  **And that flag is why a raw file asked for as a top-level document is sent back to the
  viewer.** ``_top`` inside our frame is MutInt's own window, so a click on *summary* from an
  evidence page replaced the whole page with the bare `summary.html`, chrome gone, sandboxed
  but stranded. The browser says which it is asking for -- ``Sec-Fetch-Dest: document`` for a
  navigation, ``iframe`` for the frame's own loads -- so `report_file` answers a top-level
  request for an ``.html`` with a redirect to ``report/<id>/?page=<that file>``, and the browser
  carries the ``#RA_123.html`` fragment across the redirect for the viewer to hand back to the
  frame. Downloads are navigations too, which is why only ``.html`` is redirected.
- ``allow-downloads`` -- ``output.gd`` and ``log.txt`` are linked from the report and are worth
  being able to save.

``allow-same-origin`` is the one that must never be added. With it the sandbox stops isolating
anything, because the frame is then same-origin with us and its scripts can reach our DOM.

**And the sandbox is why the files are authorised by a signed URL rather than by the session.**
An opaque origin makes every request the report issues for itself cross-site, so `SameSite=Lax`
withholds the cookie. See `sign_sample`.
"""

import logging
import os
from urllib.parse import quote

from django.core import signing
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render

from mutint_common import store
from mutint_common.fileserve import serve_file
from mutint_common.util import get_user_context
from mutint_experiment.permissions import can_view_project
from mutint_sample.models import Sample

logger = logging.getLogger("mutint_sample.report")

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

#: Namespaces the signature so a token minted here cannot be replayed against any other
#: signed value in the project.
REPORT_SALT = "mutint_sample.report"

#: How long a report link stays good. Long enough to read a report at leisure, short enough
#: that a URL copied out of a browser's history is not a lasting key to somebody's data.
REPORT_TOKEN_MAX_AGE = 12 * 60 * 60


def sign_sample(sample_id):
    """A capability for one sample's report, carried in the URL.

    **The report's files cannot be authenticated by cookie, and that follows from the
    sandbox.** A sandboxed frame with no `allow-same-origin` has an *opaque* origin, so every
    request the report makes for itself -- its icon, its stylesheet, a click through to
    `summary.html` -- is cross-site, and Django's `SameSite=Lax` session cookie is
    deliberately not sent on those. The frame's *first* load works, because the parent
    document initiates it and the parent is same-site; everything the report then asks for on
    its own does not. The symptom is a report that renders once, with no images and links
    that 404 -- which is exactly how this was found, by opening one.

    So the URL carries the authority instead. Permission is checked **when the token is
    minted**, by the viewer, which is an ordinary cookie-authenticated page; the file route
    then trusts the signature and asks nothing about who is calling.

    The trade is worth stating plainly: this is a **bearer** capability. Anyone holding the
    URL can read that sample's report until it expires, so it is bound to one sample and to
    `REPORT_TOKEN_MAX_AGE`. It is deliberately *not* bound to the user -- checking that would
    need the cookie that cannot arrive, which is the whole problem.
    """
    return signing.dumps(int(sample_id), salt=REPORT_SALT)


def unsign_sample(token):
    """The sample id a token authorises, or None if it is not valid for one."""
    try:
        return int(signing.loads(token, salt=REPORT_SALT, max_age=REPORT_TOKEN_MAX_AGE))
    except (signing.BadSignature, signing.SignatureExpired, ValueError, TypeError):
        return None


#: What the link bar offers, in the order breseq's own header does. `evidence.html` is not
#: listed: it is reached from a row, never opened on its own -- with no fragment it says
#: "No evidence file specified in URL hash."
REPORT_PAGES = (
    ("index.html", "breseq report"),
    ("summary.html", "summary"),
    ("marginal.html", "marginal predictions"),
)


def _contained_html(sample, page):
    """`page` if it names an `.html` inside this sample's report, else None.

    The viewer takes any page of the report, not only the three the bar offers, because the
    redirect below sends it whatever breseq's own links pointed at -- `evidence.html`, or an
    `evidence/RA_12.html` from an older report. The same containment `report_file` applies:
    the real path has to land inside the report directory.
    """
    if not page or not page.lower().endswith(".html"):
        return None
    root = os.path.realpath(store.sample_report_dir(sample.pk))
    candidate = os.path.realpath(os.path.join(root, page))
    if not candidate.startswith(root + os.sep) or not os.path.isfile(candidate):
        return None
    return os.path.relpath(candidate, root)


def _is_top_level_navigation(request):
    """Whether the browser is loading this as the page itself rather than into a frame.

    `Sec-Fetch-Dest` is `document` for a top-level navigation and `iframe` for a frame's load;
    a browser too old to send it is served the file as before, sandboxed by the header.
    """
    return request.headers.get("Sec-Fetch-Dest", "").lower() == "document"


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
    """The viewer: MutInt's chrome, a link bar, and the report in a sandboxed frame."""
    sample = _sample_or_404(request, sample_id)
    experiment = sample.population.experiment

    # Any .html the report holds, contained to its directory; anything else is the index. The
    # bar marks only its own three, so an evidence page shows with nothing bold.
    page = _contained_html(sample, request.GET.get("page")) or "index.html"

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
        # The token sits in a path *segment*, so the report's own relative links keep it:
        # `breseq_icon.png` beside `index.html` resolves to the same prefix. Putting it in a
        # query string would be lost the moment the report linked to anything.
        "frame_url": "/mutations/report/%d/files/%s/%s" % (
            sample.pk, sign_sample(sample.pk), page),
        # What "open in new tab" opens: this viewer, on this page, rather than the bare file.
        "viewer_url": "/mutations/report/%d/?page=%s" % (sample.pk, quote(page)),
        "sandbox_flags": SANDBOX_FLAGS,
    })
    return render(request, "report/report.html", context)


def report_file(request, sample_id, token, path):
    """One file out of the report, contained and sandboxed.

    **Authorised by the signed token, not by the session**, for the reason `sign_sample`
    gives: the sandbox denies this request its cookie. The token is minted by the viewer,
    which did check `can_view_project`.

    The only route in core that serves a client-named path, so containment is the rest of the
    job: the resolved `realpath` must land inside this sample's own report directory.
    `realpath` on both sides is what makes a symlink *inside* the report pointing outward fail
    too -- breseq does not write symlinks, but the report is whatever was uploaded.
    """
    authorised = unsign_sample(token)
    if authorised is None or authorised != int(sample_id):
        # Same answer for a forged token, an expired one, and one minted for another sample.
        raise Http404("No breseq report for that sample.")

    sample = (Sample.objects.filter(pk=sample_id, report_stored=True).first())
    if sample is None:
        raise Http404("No breseq report for that sample.")

    root = os.path.realpath(store.sample_report_dir(sample.pk))
    candidate = os.path.realpath(os.path.join(root, path))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise Http404("No such file.")

    # A page of the report navigated to as the page itself -- one of breseq's `target="_top"`
    # links, or a pasted URL -- goes back into the viewer, which frames it. See the module
    # docstring. The fragment, which names the evidence page, survives the redirect in the
    # browser. Only .html: a click on `output.gd` is a navigation too, and it wants the file.
    if candidate.lower().endswith(".html") and _is_top_level_navigation(request):
        return HttpResponseRedirect("/mutations/report/%d/?page=%s" % (
            sample.pk, quote(os.path.relpath(candidate, root))))

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
