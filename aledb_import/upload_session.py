"""Chunked upload endpoints for breseq folder import.

A folder drop can be tens of GB, which cannot go through one POST. The client declares a
manifest, uploads each file in bounded chunks, then asks the server to finalize. Every
request stays short, so this needs no task queue -- there is neither Celery nor Channels in
this codebase.

The client supplies relative paths (``webkitRelativePath`` / ``entry.fullPath``), which are
untrusted input and the main new attack surface here. ``sanitize_relative_path`` rejects
absolute paths, drive letters, and any ``..`` component, and every write is additionally
confirmed to resolve inside the session's own staging directory.
"""

import json
import logging
import os
import shutil

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from aledb_common import store
from aledb_common.import_registry import (
    ConfirmationRequired,
    get_import_handler,
    run_import,
)
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_edit_experiment, experiment_lock_refusal
from aledb_import import reference_store
from aledb_import.models import (
    STATE_FAILED,
    STATE_FINALIZED,
    STATE_OPEN,
    UploadSession,
)

logger = logging.getLogger("aledb_import.upload_session")

# Bounds the temp file Django spools per part, independent of total upload size.
MAX_CHUNK_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_ENTRIES = 20000


class UploadError(Exception):
    """Client error; rendered as a 400."""


def sanitize_relative_path(raw):
    """Return a safe relative path, or raise UploadError.

    Rejects absolute paths, Windows drive letters, and any ``..`` component. Note this is
    only half the defence -- the caller still confirms the resolved path stays inside the
    session's staging directory, which is what catches symlinks.
    """
    if not raw or not isinstance(raw, str):
        raise UploadError("missing path")
    normalized = raw.replace("\\", "/").strip()
    if normalized.startswith("/"):
        raise UploadError("absolute paths are not accepted: %r" % (raw,))
    if len(normalized) > 1 and normalized[1] == ":":
        raise UploadError("absolute paths are not accepted: %r" % (raw,))

    parts = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UploadError("path may not contain '..': %r" % (raw,))
        parts.append(part)
    if not parts:
        raise UploadError("empty path: %r" % (raw,))
    return os.path.join(*parts)


def staged_path(session, raw_path):
    """Resolve ``raw_path`` inside this session's staging dir, or raise UploadError."""
    relative = sanitize_relative_path(raw_path)
    root = store.staging_dir(session.id)
    candidate = os.path.join(root, relative)

    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(candidate)
    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        raise UploadError("path escapes the upload area: %r" % (raw_path,))
    return candidate


@require_POST
def create_upload_session(request):
    """Open a session. Body: {ale_experiment_id, import_type, files:[{path,size}]}."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"error": "Body must be JSON."}, status=400)

    try:
        experiment = AleExperiment.objects.get(pk=payload.get("ale_experiment_id"))
    except (AleExperiment.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Unknown experiment."}, status=404)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot add to this experiment."}, status=403)

    # Required, not optional. Auto-detect guessed from filename suffixes, which is
    # exactly where it was least reliable: a breseq result folder carries a .gd, a
    # .fasta, a .gff3 and a .bam, each of which several handlers claim, so what a
    # drop became depended on handler priority rather than on what the person meant.
    # Naming the type makes anything the handler does not claim a reported error
    # instead of a file quietly routed somewhere else.
    import_type = (payload.get("import_type") or "").strip()
    if not import_type:
        return JsonResponse(
            {"error": "Choose what you are importing."}, status=400)
    if get_import_handler(import_type) is None:
        return JsonResponse(
            {"error": "Unknown import type: %s" % import_type}, status=400)

    files = payload.get("files") or []
    if not isinstance(files, list) or not files:
        return JsonResponse({"error": "No files were declared."}, status=400)
    if len(files) > MAX_MANIFEST_ENTRIES:
        return JsonResponse(
            {"error": "Too many files (%d)." % len(files)}, status=400)

    manifest = []
    declared = 0
    try:
        for entry in files:
            relative = sanitize_relative_path((entry or {}).get("path"))
            size = int((entry or {}).get("size") or 0)
            if size < 0:
                raise UploadError("negative size for %r" % (relative,))
            manifest.append({"path": relative, "size": size})
            declared += size
    except (UploadError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    session = UploadSession.objects.create(
        user=request.user if request.user.is_authenticated else None,
        ale_experiment=experiment,
        import_type=import_type,
        manifest=manifest,
        declared_bytes=declared)
    store.ensure_dir(store.staging_dir(session.id))

    return JsonResponse({
        "upload_id": str(session.id),
        "files": len(manifest),
        "declared_bytes": declared,
    })


@require_POST
def upload_chunk(request, upload_id):
    """Append one chunk. Multipart: path, offset, and a single `chunk` part."""
    session, error = _open_session(request, upload_id)
    if error:
        return error

    chunk = request.FILES.get("chunk")
    if chunk is None:
        return JsonResponse({"error": "No chunk was sent."}, status=400)
    if chunk.size > MAX_CHUNK_BYTES:
        return JsonResponse(
            {"error": "Chunk exceeds %d bytes." % MAX_CHUNK_BYTES}, status=400)

    try:
        offset = int(request.POST.get("offset") or 0)
        if offset < 0:
            raise UploadError("negative offset")
        destination = staged_path(session, request.POST.get("path"))
    except (UploadError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    store.ensure_dir(os.path.dirname(destination))
    existing = os.path.getsize(destination) if os.path.exists(destination) else 0
    if offset > existing:
        # Refuse to leave a hole; the client should retry from `existing`.
        return JsonResponse(
            {"error": "Out-of-order chunk.", "expected_offset": existing}, status=409)

    with open(destination, "r+b" if existing else "wb") as handle:
        handle.seek(offset)
        for block in chunk.chunks():
            handle.write(block)

    new_size = os.path.getsize(destination)
    # Count only growth, so a retried chunk is not double-counted.
    session.received_bytes = max(0, session.received_bytes + (new_size - existing))
    session.save(update_fields=["received_bytes", "updated"])

    return JsonResponse({"path": os.path.relpath(destination, store.staging_dir(session.id)),
                         "size": new_size,
                         "received_bytes": session.received_bytes})


@require_POST
def cancel_upload(request, upload_id):
    """Abandon a staged upload and clear it.

    Without this a declined rename would hold its staging -- gigabytes, for a breseq drop --
    until the TTL reaper got to it.
    """
    session, error = _open_session(request, upload_id)
    if error:
        return error

    shutil.rmtree(store.staging_dir(session.id), ignore_errors=True)
    session.state = STATE_FAILED
    session.save(update_fields=["state", "updated"])
    return JsonResponse({"cancelled": True})


def _payload(request):
    try:
        return json.loads((request.body or b"{}").decode("utf-8")) or {}
    except (ValueError, UnicodeDecodeError):
        return {}


def finalize_upload(request, upload_id):
    """Ingest the staged tree and clear it. Returns the standard import summary."""
    session, error = _open_session(request, upload_id)
    if error:
        return error

    # Re-checked here, and not only when the session was created. Permission was asked once
    # at `create_upload_session` and the session then carries itself; a session opened before
    # the experiment was locked would otherwise finalize straight through it. `run_import`
    # refuses as well -- this one exists so the answer arrives as a clean JSON refusal rather
    # than an exception surfacing from three layers down.
    if not can_edit_experiment(request.user, session.ale_experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(session.ale_experiment)
                      or "You cannot add data to this experiment."}, status=403)

    root = store.staging_dir(session.id)
    options = {"confirm_rename": _payload(request).get("confirm_rename")}
    try:
        # The registry decides what each file is and which handler takes it, so a plugin's
        # import type is reachable here with no change to this view.
        summary = run_import(session.ale_experiment, root, request.user,
                             import_type=session.import_type, options=options)
    except ConfirmationRequired as ask:
        # Deliberately before the blanket handler, and deliberately without the cleanup: the
        # staged tree is what the confirming request will import, so it has to survive the
        # question, and the session stays open so `_open_session` accepts the second POST.
        # `updated` is touched so the reaper's TTL restarts from when the question was asked
        # rather than from when the upload began.
        session.save(update_fields=["updated"])
        # 200 rather than 409: the client's postJson throws on any non-2xx and renders
        # `error`, so a status code would turn a question into a failure message.
        return JsonResponse({"needs_confirmation": ask.payload})
    except Exception as exc:
        logger.exception("breseq folder finalize failed for session %s", session.id)
        session.state = STATE_FAILED
        session.save(update_fields=["state", "updated"])
        shutil.rmtree(root, ignore_errors=True)
        return JsonResponse({"error": str(exc)}, status=500)

    shutil.rmtree(root, ignore_errors=True)
    session.state = STATE_FINALIZED
    session.save(update_fields=["state", "updated"])
    summary["upload_id"] = str(session.id)
    # The Add page's dropdown and its banner are both scoped by this, and a drop that
    # establishes a reference -- a genome on its own, or a breseq folder bringing its own --
    # changes it underneath a page already rendered. Reporting it is what lets the page know
    # to go and get itself re-rendered rather than keep offering the pre-reference choices.
    summary["has_reference"] = reference_store.has_reference(session.ale_experiment)
    return JsonResponse(summary)


def reap_expired_sessions(now=None):
    """Delete staging dirs for sessions past their TTL. Returns how many were removed."""
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(
        hours=getattr(settings, "ALEDB_UPLOAD_SESSION_TTL_HOURS", 24))
    removed = 0
    for session in UploadSession.objects.filter(state=STATE_OPEN, updated__lt=cutoff):
        shutil.rmtree(store.staging_dir(session.id), ignore_errors=True)
        session.state = STATE_FAILED
        session.save(update_fields=["state", "updated"])
        removed += 1
    return removed


def _open_session(request, upload_id):
    try:
        session = UploadSession.objects.get(pk=upload_id)
    except (UploadSession.DoesNotExist, ValueError, TypeError):
        return None, JsonResponse({"error": "Unknown upload session."}, status=404)

    if session.state != STATE_OPEN:
        return None, JsonResponse(
            {"error": "Upload session is %s." % session.state}, status=409)

    # A session belongs to whoever opened it; nobody else may write into its staging area.
    if session.user_id and session.user_id != getattr(request.user, "id", None):
        return None, JsonResponse({"error": "Not your upload session."}, status=403)

    return session, None
