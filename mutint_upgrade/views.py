"""`/upgrade/` -- what this installation is made of, and moving it onto a newer version.

The page is an inventory first: one row per component, with its version and the git revision
it is at, linked to that commit on GitHub. That table is `about_registry.get_about_sections()`,
the same inventory `/about` renders as prose -- this is the same facts as something you can
act on.

**It stages; it does not upgrade.** A running process cannot safely replace its own source and
rebuild its own virtualenv, so Install writes a request into `data/upgrade.json` and the next
`./mutint start` applies it before Django is importable. See `mutint_common/upgrade.py`.

**It never blocks on the network.** The page renders from the stored verdict and only the
Check button reaches the remote, which is the posture `mutint_sample/ncbi.py` established: a
page whose load time depends on a third party is one that is down whenever they are. Every
failure comes back as a sentence rather than a traceback, and "could not ask" stays
distinguishable from "nothing newer" -- a reader told they are up to date when nobody managed
to look is worse off than one shown an error.
"""

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from mutint_common import upgrade
from mutint_upgrade import restart
from mutint_common.about_registry import get_about_sections
from mutint_common.logger import user_extra
from mutint_common.util import get_user_context

logger = logging.getLogger(__name__)


def _enabled():
    """Whether this deployment offers upgrades at all.

    ALEdb sets it False: it is a private repository whose operator upgrades by hand. The
    `.gitmodules` trick that keeps a plugin out of a deployment cannot be used here, because
    the account block lives in core's own `base.html` and a plugin has no seam into it -- so
    this is a deployment setting, and the decision lives in the deployment that owns it.
    """
    return getattr(settings, "MUTINT_UPGRADE_ENABLED", True)


def _forbidden(request):
    return render(request, "403.html", get_user_context(request.user), status=403)


def _body(request):
    """The posted JSON object, or None. `mutintPostJson` is what the page uses, because it
    surfaces real status codes -- a 409 arrives carrying the sentence written below rather
    than as a generic failure -- and it sends a JSON body rather than form fields."""
    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _rows():
    """One row per installed component: name, version, revision, link.

    `get_about_sections` keys by *component checkout*, so mutint-core's fifteen apps are one
    row and a plugin is one row, which is the unit somebody upgrading thinks in.
    """
    return get_about_sections()


def upgrade_page(request):
    """The page. Renders whatever the last check stored; asks nobody anything.

    Named `upgrade_page` rather than `upgrade` because this module imports
    `mutint_common.upgrade`, and a view of that name would rebind it -- every
    `upgrade.read_state` below would then be an attribute lookup on this function. The URL
    is still called `upgrade`.
    """
    if not request.user.is_superuser:
        return _forbidden(request)
    logger.info("upgrade", extra=user_extra(request))

    base_dir = upgrade_root()
    state = upgrade.read_state(base_dir) if base_dir else {"channel": upgrade.STABLE}

    context = get_user_context(request.user)
    context.update({
        "enabled": _enabled() and base_dir is not None,
        "components": _rows(),
        "channel": state.get("channel", upgrade.STABLE),
        "channels": upgrade.CHANNELS,
        "checked_at": state.get("checked_at"),
        "available": state.get("available"),
        # **Not `error`.** `base.html` renders `<h2>{{ error }}</h2>` unguarded, so a page
        # putting that name in its context gets it as a page heading -- and this one is None
        # whenever no upgrade has failed, which printed the literal word "None" above the
        # component table. The alert this feeds is a different thing from a page-level error
        # anyway: it reports the last *upgrade attempt*, not a problem with the request.
        "upgrade_error": state.get("error"),
        "last_result": state.get("last_result"),
        "requested": state.get("requested"),
        "current": upgrade.current_ref(base_dir) if base_dir else None,
        "blockers": upgrade.blockers(base_dir) if base_dir else [],
        # Only where something said how to start MutInt again -- see `restart`. From a
        # terminal nothing does, and a Restart button there would offer to kill the
        # server and nothing else.
        "can_restart": restart.relaunch_command() is not None,
    })
    return render(request, "upgrade/index.html", context)


def upgrade_root():
    """The installation to act on, or None when no entry script exported one."""
    return upgrade.project_root()


@require_POST
def upgrade_check(request):
    """Ask the remote what is available. The only thing here that touches the network."""
    if not request.user.is_superuser:
        # Re-checked rather than trusted: the gate on a write endpoint is not the gate on the
        # page that offered it.
        return JsonResponse({"error": "Not permitted."}, status=403)
    if not _enabled():
        return JsonResponse({"error": "Updates are not enabled on this deployment."},
                            status=409)
    base_dir = upgrade_root()
    if base_dir is None:
        return JsonResponse({"error": "Cannot tell which installation this is."}, status=409)

    body = _body(request)
    if body is None:
        return JsonResponse({"error": "The body must be JSON."}, status=400)
    channel = body.get("channel") or None
    if channel is not None and channel not in upgrade.CHANNELS:
        return JsonResponse({"error": "Unknown channel."}, status=400)

    state = upgrade.check(base_dir, channel=channel)
    return JsonResponse({
        "channel": state.get("channel"),
        "checked_at": state.get("checked_at"),
        "available": state.get("available"),
        "error": state.get("error"),
    })


@require_POST
def upgrade_install(request):
    """Stage a version to be applied by the next launch."""
    if not request.user.is_superuser:
        return JsonResponse({"error": "Not permitted."}, status=403)
    if not _enabled():
        return JsonResponse({"error": "Updates are not enabled on this deployment."},
                            status=409)
    base_dir = upgrade_root()
    if base_dir is None:
        return JsonResponse({"error": "Cannot tell which installation this is."}, status=409)

    body = _body(request)
    if body is None:
        return JsonResponse({"error": "The body must be JSON."}, status=400)
    ref = (body.get("ref") or "").strip()
    if not ref:
        return JsonResponse({"error": "Nothing to install."}, status=400)

    # Whatever is staged must be something a check actually offered. Otherwise this endpoint
    # takes an arbitrary ref from a form field and hands it to `git checkout` on the next
    # launch, which is a much larger promise than the page is making.
    state = upgrade.read_state(base_dir)
    available = state.get("available") or {}
    if ref != available.get("ref"):
        return JsonResponse(
            {"error": "That version is not the one currently on offer. Check again."},
            status=409)

    problems = upgrade.blockers(base_dir)
    if problems:
        return JsonResponse({"error": " ".join(problems)}, status=409)

    upgrade.request(base_dir, ref, by=request.user.get_username())
    logger.info("upgrade_staged", extra=user_extra(request))
    return JsonResponse({"staged": ref})


@require_POST
def upgrade_restart(request):
    """Stop MutInt and start it again, so a staged upgrade is applied.

    The last step of an upgrade was an instruction -- quit MutInt, start it again -- because
    the page cannot replace the code it is running on. It still cannot: what this does is the
    same quit and the same start, asked for from the page instead of remembered by a person.
    """
    if not request.user.is_superuser:
        # Re-checked rather than trusted: the gate on a write endpoint is not the gate on the
        # page that offered it.
        return JsonResponse({"error": "Not permitted."}, status=403)
    if not _enabled():
        return JsonResponse({"error": "Updates are not enabled on this deployment."},
                            status=409)
    if restart.relaunch_command() is None:
        return JsonResponse(
            {"error": "This copy of MutInt was not started by anything that can start it "
                      "again, so it can only be stopped from where it was started."},
            status=409)

    pid = restart.request_restart()
    logger.info("upgrade_restart", extra=user_extra(request))
    # Answered before it happens, because the process writing this is one of the ones about
    # to be stopped -- `restart` waits for that reason.
    return JsonResponse({"restarting": True, "pid": pid})
