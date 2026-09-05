"""Staging a drop for a component that is not an import type.

`upload_session` stages files and hands them to the import registry. Some components want the
first half and not the second: `mutint-breseq` takes FASTQ reads that are not a mutation file
at all -- they are the input to a job whose *output* is imported hours later, and which needs
a sample name and a command line the Add page has nowhere to put.

Registering an import handler for them would have been the smaller change and is the wrong
one: it puts an entry in the Add page's dropdown that cannot carry what the entry needs, and
makes `run_import` responsible for something that does not return a summary. So the split is
made one level down. Core keeps the parts that are about *bytes arriving safely* -- the
permission check, the manifest, path containment, the chunk endpoint and the TTL reaper -- and
the component keeps the part that is about what the bytes are for.

**What a component gets, and the one rule.** ``open_session`` and then core's own
``/import/uploads/<id>/chunk``, unchanged, which does not care which kind of session it is
appending to. Then ``claim``, which is the handover: after it the directory is the
component's, and *nothing here will delete it*. Before it, an abandoned session is reaped
after ``MUTINT_UPLOAD_SESSION_TTL_HOURS`` like any other. So a component that claims and then
crashes leaks a directory, which is the trade for a component being able to take as long as
it likes -- and is why ``claim`` should be followed by moving the files somewhere the
component reaps itself, rather than by working in place.

Nothing is registered and there is no ninth registry, because there is nothing for an app to
*contribute*. This is a facility to call, and a registry whose entries nobody enumerates is a
dictionary with ceremony.
"""

import json
import logging
import shutil

from django.apps import apps
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from mutint_common import store
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment
from mutint_import.models import (
    STATE_CLAIMED,
    STATE_FAILED,
    STATE_FINALIZED,
    STATE_OPEN,
    UploadSession,
)
from mutint_import.upload_session import UploadError, build_manifest

logger = logging.getLogger("mutint_import.staging")


def open_session(user, experiment, consumer, files):
    """Open a staging session owned by `consumer`, an installed app label.

    `files` is the client's declared manifest, `[{path, size}, ...]`; it is sanitized here and
    is a record of what was promised rather than a trusted map. Raises `UploadError` for
    anything the client got wrong, and `ValueError` for a `consumer` that is not an installed
    app -- the second is a programming error in the component, not a bad request.

    The permission check is the *caller's*: this is reached from a component's own view, which
    has already decided who may ask. `create_staging_session` below is that view for the
    common case and does check.
    """
    if not apps.is_installed(consumer):
        raise ValueError("consumer must be an installed app label: %r" % (consumer,))

    manifest, declared = build_manifest(files)
    session = UploadSession.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        experiment=experiment,
        consumer=consumer,
        manifest=manifest,
        declared_bytes=declared)
    store.ensure_dir(store.staging_dir(session.id))
    return session


def claim(session):
    """Take ownership of a staged directory and return its path.

    After this the reaper leaves it alone -- `reap_expired_sessions` only ever looks at open
    sessions -- so the component is the only thing that will delete it. Idempotent, so a
    retried launch does not fail on its own earlier attempt.
    """
    if session.state not in (STATE_OPEN, STATE_CLAIMED):
        raise UploadError("Upload session is %s." % session.state)
    if session.state != STATE_CLAIMED:
        session.state = STATE_CLAIMED
        session.save(update_fields=["state", "updated"])
    return store.staging_dir(session.id)


def close(session):
    """The component is done with the staged files: remove them and mark the session spent."""
    shutil.rmtree(store.staging_dir(session.id), ignore_errors=True)
    session.state = STATE_FINALIZED
    session.save(update_fields=["state", "updated"])


def abandon(session):
    """The component is not going to use the files after all: remove them and say so."""
    shutil.rmtree(store.staging_dir(session.id), ignore_errors=True)
    session.state = STATE_FAILED
    session.save(update_fields=["state", "updated"])


def session_for(request, upload_id, consumer):
    """Resolve one of `consumer`'s sessions for this request, or (None, JsonResponse).

    The checks a component would otherwise write for itself, and would eventually write
    differently: that the session exists, that it belongs to this component rather than to
    another's or to an ordinary import, and that it belongs to whoever is asking.
    """
    try:
        session = UploadSession.objects.get(pk=upload_id)
    except (UploadSession.DoesNotExist, ValueError, TypeError):
        return None, JsonResponse({"error": "Unknown upload session."}, status=404)

    if session.consumer != consumer:
        return None, JsonResponse({"error": "That upload is not %s's." % consumer}, status=409)

    if session.user_id and session.user_id != getattr(request.user, "id", None):
        return None, JsonResponse({"error": "Not your upload session."}, status=403)

    return session, None


@require_POST
def create_staging_session(request):
    """Open a component's session. Body: {experiment_id, consumer, files:[{path,size}]}.

    The sibling of `create_upload_session`, differing in that `consumer` names an installed app
    instead of `import_type` naming a registered handler. Same gate: `can_edit_experiment`, not
    `can_edit_project` -- what a component does with a drop is a write to the experiment, and a
    predicate handed the project cannot see the lock.

    **Signed in as well as permitted**, and the check is not redundant even though
    `can_edit_experiment` already refuses anonymous (`effective_role` caps them at `read`). It
    states the rule where somebody editing this file will read it, the way `project_create`
    does -- and the shape that has bitten this codebase before is exactly a new endpoint whose
    author had no object to run a predicate against. `mutint_sample/views/table_actions.py`
    carries the note; its two tag endpoints were anonymously writable until it was added.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"error": "Body must be JSON."}, status=400)

    try:
        experiment = Experiment.objects.get(pk=payload.get("experiment_id"))
    except (Experiment.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Unknown experiment."}, status=404)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot add to this experiment."}, status=403)

    consumer = (payload.get("consumer") or "").strip()
    if not consumer or not apps.is_installed(consumer):
        return JsonResponse({"error": "Unknown consumer: %s" % consumer}, status=400)

    try:
        session = open_session(request.user, experiment, consumer, payload.get("files") or [])
    except (UploadError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({
        "upload_id": str(session.id),
        "files": len(session.manifest),
        "declared_bytes": session.declared_bytes,
    })
