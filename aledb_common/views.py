"""Serving files out of ALE_DATA_ROOT_DIR at ``/aledata/``.

This module is now solely that route. It also held ``show_amplifiction_data``, which listed
``<exp>/amplifications/`` or ``<exp>/breseq/dups/`` as links into ``/aledata/``. Nothing had
routed to it since 2021, when the Amplifications page became a copy of the mutation table
instead; the page itself is gone now too, so the listing and its ``file_list.html`` template
went with it.

This route used to be the suite's soft spot. It concatenated ``DOC_ROOT + page_name`` with no
normalisation, so ``/aledata/../../../../etc/passwd.html`` escaped the root; it had a branch
(``'.html' in page_name or '.ba' in page_name``) that skipped the permission call entirely,
and being substring rather than suffix tests, ``.ba`` already caught every ``.bam``/``.bai``;
its permission call was ``can_view_experiment``, a stub returning True; it read whole files
into memory; and it kept a module-global ``user_allowed_data_dirs`` dict keyed by user id,
which was per-worker, never evicted, and survived logout.

What replaces all of that is one resolver: normalise the path and require it to stay inside
the root, find the ResequencingExperiment that owns it, and check ``can_view_project``.
Serving itself is ``aledb_common.fileserve``, shared with the alignment routes.

Path ownership is by prefix. A sample's stored ``location`` (``.../breseq/<s>/output/``),
``gatk_location`` and ``experiment_location`` are exactly the roots the breseq HTML reports,
GATK output and amplification files sit under, so the longest stored prefix of a requested
path identifies the experiment it belongs to. A path under no known prefix is a 404: this
route serves experiment data, not the filesystem.
"""

import logging
import os

from django.conf import settings
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden

from aledb_common.fileserve import serve_file
from aledb_common.logger import user_extra
from aledb_experiment.permissions import can_view_project
from aledb_seq.models import ResequencingExperiment

DOC_ROOT = settings.ALE_DATA_ROOT_DIR

INDEX_FILE_NAME = "index.html"

logger = logging.getLogger(__name__)


def protected_file_serve(request, page_name: str):
    relative = _normalized_relative_path(page_name)
    if relative is None:
        logger.warning("rejected /aledata/ path outside the data root: %r", page_name,
                       extra=user_extra(request))
        raise Http404("No such file.")

    file_path = os.path.join(DOC_ROOT, relative)
    if os.path.isdir(file_path):
        # A directory means its breseq report; the old view did the same for a trailing slash.
        relative = os.path.join(relative, INDEX_FILE_NAME)
        file_path = os.path.join(DOC_ROOT, relative)

    experiment = _owning_experiment(relative)
    if experiment is None:
        logger.info("no experiment owns /aledata/%s", relative, extra=user_extra(request))
        raise Http404("No such file.")

    if not _may_view(request.user, experiment):
        # Returned, not raised: HttpResponseForbidden is a response, and `raise`-ing it --
        # as this view used to -- is a TypeError, so the user got a 500 instead of a 403.
        return HttpResponseForbidden("You do not have access to this experiment.")

    logger.info("display file " + relative, extra=user_extra(request))
    return serve_file(request, file_path, os.path.basename(relative))


def _normalized_relative_path(page_name):
    """`page_name` as a path relative to DOC_ROOT, or None if it escapes the root.

    ``os.path.normpath`` collapses the ``..`` segments, and the containment check is what
    actually enforces the boundary -- `normpath` alone still happily walks above the root.
    """
    if not page_name:
        return None

    root = os.path.abspath(DOC_ROOT)
    resolved = os.path.abspath(os.path.join(root, page_name))
    try:
        if os.path.commonpath([resolved, root]) != root:
            return None
    except ValueError:
        # Different drives on Windows; not under the root either way.
        return None
    if resolved == root:
        return None
    return os.path.relpath(resolved, root)


def _owning_experiment(relative):
    """The AleExperiment whose stored paths contain `relative`, or None.

    Matches by generating the requested path's own directory prefixes and looking for a
    stored `location` / `gatk_location` / `experiment_location` equal to one of them -- the
    reverse of a `startswith` lookup, which SQL cannot express against a column.
    """
    candidates = set()
    parts = relative.replace(os.sep, "/").split("/")
    for depth in range(1, len(parts)):
        prefix = "/".join(parts[:depth])
        # Stored with and without a trailing separator, depending on which column it is.
        candidates.add(prefix)
        candidates.add(prefix + "/")
    candidates.discard("")

    reseq = ResequencingExperiment.objects.filter(
        Q(location__in=candidates)
        | Q(gatk_location__in=candidates)
        | Q(experiment_location__in=candidates)).first()
    if reseq is None:
        return None
    return reseq.ale_experiment


def _may_view(user, experiment):
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)
