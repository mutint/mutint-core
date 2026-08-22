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
from aledb_import import breseq_folder
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
    """Open a session. Body: {project, experiment, person, files:[{path,size}]}."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"error": "Body must be JSON."}, status=400)

    project = (payload.get("project") or "").strip()
    experiment = (payload.get("experiment") or "").strip()
    if not project or not experiment:
        return JsonResponse(
            {"error": "Both project and experiment are required."}, status=400)

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

    person = (payload.get("person") or "").strip() or request.user.get_username()
    session = UploadSession.objects.create(
        user=request.user if request.user.is_authenticated else None,
        project_name=project,
        experiment_name=experiment,
        person=person,
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
def finalize_upload(request, upload_id):
    """Ingest the staged tree and clear it. Returns the standard import summary."""
    session, error = _open_session(request, upload_id)
    if error:
        return error

    root = store.staging_dir(session.id)
    try:
        summary = breseq_folder.import_breseq_folders(
            root,
            project_name=session.project_name,
            experiment_name=session.experiment_name,
            person=session.person)
    except Exception as exc:
        logger.exception("breseq folder finalize failed for session %s", session.id)
        session.state = STATE_FAILED
        session.save(update_fields=["state", "updated"])
        return JsonResponse({"error": str(exc)}, status=500)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    session.state = STATE_FINALIZED
    session.save(update_fields=["state", "updated"])
    summary["upload_id"] = str(session.id)
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
