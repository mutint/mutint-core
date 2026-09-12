"""Chunked upload endpoints for breseq folder import.

A folder drop can be tens of GB, which cannot go through one POST. The client declares a
manifest, uploads each file in bounded chunks, then asks the server to finalize.

Every *chunk* request stays short. ``finalize_upload`` does not, and saying otherwise is what
worth saying plainly: the whole ingest runs there, parse and store and coverage and every
derived-data rebuild. There is a queue now, and this has not moved onto it -- what there is
instead is a progress
snapshot written as the import goes and served by ``upload_progress``, so a long finalize
reports itself rather than looking like a hung page.

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
from django.views.decorators.http import require_GET, require_POST

from mutint_common import import_progress, store
from mutint_common.import_registry import (
    ConfirmationRequired,
    get_import_handler,
    run_import,
    KIND_REFERENCE,
)
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment, experiment_lock_refusal
from mutint_common.annotator_registry import (
    AnnotatorOptionsInvalid,
    clean_selections,
    run_annotators,
)
from mutint_import import accessions, import_lock, ncbi_fetch, reference_store
from mutint_import.import_lock import ImportInProgress
from mutint_import.models import (
    STATE_FAILED,
    STATE_FINALIZED,
    STATE_OPEN,
    UploadSession,
)

logger = logging.getLogger("mutint_import.upload_session")

# Bounds the temp file Django spools per part, independent of total upload size.
MAX_CHUNK_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_ENTRIES = 20000

# Where one unit of an import has got to. Named here rather than on the model because they
# describe a row inside a JSON blob, not a column anything queries.
UNIT_WAITING = "waiting"
UNIT_WORKING = "working"
UNIT_DONE = "done"


class UploadError(Exception):
    """Client error; rendered as a 400."""


def sanitize_relative_path(raw):
    """Return a safe relative path, or raise UploadError.

    Rejects absolute paths, Windows drive letters, and any ``..`` component. Note this is
    only half the defense -- the caller still confirms the resolved path stays inside the
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


def build_manifest(files, allow_empty=False):
    """``[{path, size}, ...]`` from what the client declared, sanitized. Raises UploadError.

    Shared with ``mutint_import.staging``, which opens sessions for components rather than for
    the import registry: the two differ in what they route to and in nothing else, and a
    second copy of this is a second opinion about which paths are acceptable.

    ``allow_empty`` is for the one drop that legitimately carries no files: an import that
    names NCBI accessions and nothing else. It is opt-in rather than the default because a
    component staging zero files is still a caller that has got something wrong.
    """
    if not isinstance(files, list) or (not files and not allow_empty):
        raise UploadError("No files were declared.")
    if len(files) > MAX_MANIFEST_ENTRIES:
        raise UploadError("Too many files (%d)." % len(files))

    manifest = []
    declared = 0
    for entry in files:
        relative = sanitize_relative_path((entry or {}).get("path"))
        size = int((entry or {}).get("size") or 0)
        if size < 0:
            raise UploadError("negative size for %r" % (relative,))
        manifest.append({"path": relative, "size": size})
        declared += size
    return manifest, declared


@require_POST
def create_upload_session(request):
    """Open a session.

    Body: `{experiment_id, import_type, files: [{path, size}], accessions: "<text>"}`.

    `accessions` is optional and only for a type that declared `accepts_accessions`. It is
    **resolved here**, against NCBI, which is the one thing in this endpoint that reaches the
    network -- so that a typo is refused while there is still nothing to lose, and so that a
    drop naming accessions alone can open a session with no files in it.
    """
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
    handler = get_import_handler(import_type)
    if handler is None:
        return JsonResponse(
            {"error": "Unknown import type: %s" % import_type}, status=400)

    # NCBI accessions, resolved here and not at finalize. **Deliberately after the permission
    # check**: resolving talks to NCBI, and a visitor who may not write to this experiment must
    # not be able to make the installation do that on their behalf.
    raw_accessions = (payload.get("accessions") or "").strip()
    if raw_accessions and not handler.get("accepts_accessions"):
        return JsonResponse(
            {"error": "%s does not take NCBI accessions." % handler["label"]}, status=400)

    plans = []
    if raw_accessions:
        try:
            plans = ncbi_fetch.resolve(accessions.parse(raw_accessions))
        except (accessions.AccessionError, ncbi_fetch.FetchError) as exc:
            # No session row, no staging directory, nothing uploaded. That is the whole point
            # of resolving here: a typo costs the round trip that caught it and no more.
            return JsonResponse({"error": str(exc)}, status=400)

    files = payload.get("files") or []
    if not files and not plans:
        # Asked before `build_manifest`, which would otherwise answer "No files were
        # declared." -- true, and no longer the whole of what this page accepts.
        return JsonResponse(
            {"error": ("Choose files, or type an NCBI accession."
                       if handler.get("accepts_accessions") else "No files were declared.")},
            status=400)

    try:
        manifest, declared = build_manifest(files, allow_empty=bool(plans))
    except (UploadError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    # A dropped file may not sit where a download is about to go. Named at create, while the
    # answer is still "rename it or drop it on its own" rather than "your import failed".
    #
    # Only when something is actually being downloaded: with no accessions nothing writes into
    # that directory, and a drop that happens to contain a folder called `ncbi/` is then a
    # perfectly ordinary drop.
    if plans and any(
            entry["path"].startswith(ncbi_fetch.STAGED_SUBDIR + os.sep)
            for entry in manifest):
        return JsonResponse(
            {"error": "A file in this drop would land where the accessions are downloaded. "
                      "Rename it, or drop it on its own."}, status=409)

    session = UploadSession.objects.create(
        user=request.user if request.user.is_authenticated else None,
        experiment=experiment,
        import_type=import_type,
        manifest=manifest,
        declared_bytes=declared,
        accessions=[plan.as_dict() for plan in plans])
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
    """Ingest the staged tree and clear it. Returns the standard import summary.

    Long -- a sample's coverage alone runs `bedtools` and `bedGraphToBigWig` under a
    900-second timeout, and the derived-data rebuild after the last sample counts every
    call in the installation. What makes that bearable to watch is
    `_SessionProgress`, which writes a snapshot onto the session as each unit starts and
    finishes; `upload_progress` serves it and the Import data page polls.

    **The progress is written from inside this request**, which is why it works with no
    worker and no thread: every write here lands outside the per-sample `transaction.atomic()`
    block in autocommit, so the polling request's own connection sees it immediately.

    This deliberately did *not* become a streaming response. Progress arrives at
    `import_progress.report` five frames below `run_import`, in a callback -- and a callback
    cannot yield. Any generator wrapping `run_import` would queue every event and emit the
    lot after the import had already finished, which is this function's behavior with extra
    machinery in front of it. Streaming would need the import on a worker, which is a different
    change entirely -- see **Background work** in the suite `CLAUDE.md` -- not a progress bar.
    """
    session, error = _open_session(request, upload_id)
    if error:
        return error

    # A component's session is not ours to ingest. It carries no `import_type`, so `run_import`
    # would fall through to auto-detect and hand the files to whichever registered handler
    # claimed the suffix -- FASTQ reads staged for breseq becoming an attempted reference
    # import, say. Refusing by name is the difference between a clear error and a wrong import.
    if session.consumer:
        return JsonResponse(
            {"error": "This upload belongs to %s and is finalized by it, not here."
                      % session.consumer}, status=409)

    # Re-checked here, and not only when the session was created. Permission was asked once
    # at `create_upload_session` and the session then carries itself; a session opened before
    # the experiment was locked would otherwise finalize straight through it. `run_import`
    # refuses as well -- this one exists so the answer arrives as a clean JSON refusal rather
    # than an exception surfacing from three layers down.
    if not can_edit_experiment(request.user, session.experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(session.experiment)
                      or "You cannot add data to this experiment."}, status=403)

    root = store.staging_dir(session.id)
    payload = _payload(request)
    options = {"confirm_rename": payload.get("confirm_rename")}
    progress = _SessionProgress(session.id)

    # The reference annotators the page ticked, checked now -- before the download and the
    # lock -- so a bad selection costs nothing and the session stays open, as a
    # `needs_confirmation` leaves it. They run after the import, below, and only if the
    # reference actually landed.
    try:
        selections = clean_selections(payload.get("annotators") or {})
    except AnnotatorOptionsInvalid as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)

    # **Before the lock, deliberately.** The download writes only into this session's own
    # staging directory, so it needs no lock at all -- and `import_lock` is one-at-a-time for
    # the whole installation, so holding it across a genome download would stop everybody
    # else's drop for the length of somebody's network. It has its own `reporting` block
    # because `stage()` is a no-op with no sink installed, and this is the stretch with
    # nothing else to show.
    downloaded = {}
    if session.accessions:
        try:
            with import_progress.reporting(progress):
                downloaded = ncbi_fetch.download(
                    root, session.accessions, report=import_progress.stage)
        except ncbi_fetch.FetchError as exc:
            logger.info("NCBI download failed for session %s: %s", session.id, exc)
            return _fail(session, root, progress, str(exc), status=502)
        except Exception as exc:  # noqa: BLE001 -- same posture as the finalize failure path
            logger.exception("NCBI download failed for session %s", session.id)
            return _fail(session, root, progress, str(exc), status=500)

    # One import at a time. Refused rather than queued: this is a request, and holding it
    # open for however long somebody else's drop takes would look like the hang the progress
    # reporting exists to prevent. The staged files are untouched, so trying again costs
    # nothing but the button.
    try:
        import_lock.acquire()
    except ImportInProgress as busy:
        return JsonResponse({"error": str(busy)}, status=409)

    try:
        return _finalize_holding_lock(session, request, root, options, progress, downloaded,
                                      selections=selections)
    finally:
        # One release covering every way out of the function below, including the ones that
        # return a refusal. A lock stranded here would block every later import until it
        # went stale.
        import_lock.release()


def _fail(session, root, progress, message, status):
    """Fail a session the way the finalize path does, from anywhere in it.

    Shared by the download step and by the ingest, so a drop that dies before `run_import` is
    left in exactly the state a drop that dies inside it would be: the sentence on the
    progress snapshot, the session FAILED, the staging cleared. A retry is a new session,
    which `_open_session`'s state check already insists on.
    """
    progress.fail(message)
    session.state = STATE_FAILED
    session.save(update_fields=["state", "updated"])
    shutil.rmtree(root, ignore_errors=True)
    return JsonResponse({"error": message}, status=status)


def _record_downloads(experiment, downloaded, user):
    """Record what each downloaded record turned out to be, for the contigs that landed.

    **The gate is `seq_ids`**, and it is exactly the right one. A digest that is not in the
    established reference did not become part of it -- a `replace_annotation` against the wrong
    genome, an establish that failed, a contig dropped as a duplicate of one already there --
    so there is nothing about it to record. That one intersection subsumes every "did the
    import succeed" question, which is why no import handler had to learn about any of this.

    **Twinned accessions resolve to the first typed.** `NC_000913.3` and `U00096.3` are the
    same bases under two accessions, and `DatabaseSequenceLink` is unique on
    `(database, sha256)`, so only one row can exist for them. Both statements are true; the
    rule is simply that the first one typed is the one kept, which is deterministic and which
    somebody reading the Reference page can act on. Silently letting the last write win would
    be the same outcome with no rule behind it.

    Never allowed to fail the import. The mutations are committed by now, and provenance is
    commentary on them -- the posture `_SessionProgress` takes for the same reason.
    """
    from mutint_sample import ncbi
    from mutint_sample.models import ReferenceSequences

    try:
        reference = ReferenceSequences.objects.filter(experiment=experiment).first()
        if reference is None:
            return
        stored = {entry.get("sha256") for entry in (reference.seq_ids or [])
                  if entry.get("sha256")}
        for digest, entry in downloaded.items():
            if digest not in stored:
                continue
            ncbi.record_downloaded(digest, entry["length"], entry["accession"],
                                   entry["detail"], user=user)
            # First typed wins: `downloaded` is in the order the accessions were resolved.
            stored.discard(digest)
    except Exception:
        logger.exception("could not record NCBI provenance for experiment %s",
                         getattr(experiment, "id", None))


def _reference_landed(summary):
    """Whether this drop established or updated the reference, judged from its rows.

    The reference-kind rows exist and none carries an error. **Not the `has_reference` flip**:
    `replace_annotation` never flips it, and `_ingest_reference` is all-or-nothing, so an
    error on any reference row means nothing landed.
    """
    rows = [entry for entry in summary.get("files") or []
            if entry.get("kind") == KIND_REFERENCE]
    return bool(rows) and not any(entry.get("error") for entry in rows)


def _finalize_holding_lock(session, request, root, options, progress, downloaded=None,
                           selections=None):
    """The body of `finalize_upload`, run with the import lock held."""
    try:
        # The registry decides what each file is and which handler takes it, so a plugin's
        # import type is reachable here with no change to this view.
        with import_progress.reporting(progress):
            summary = run_import(session.experiment, root, request.user,
                                 import_type=session.import_type, options=options)
    except ConfirmationRequired as ask:
        # Deliberately before the blanket handler, and deliberately without the cleanup: the
        # staged tree is what the confirming request will import, so it has to survive the
        # question, and the session stays open so `_open_session` accepts the second POST.
        # `updated` is touched so the reaper's TTL restarts from when the question was asked
        # rather than from when the upload began.
        session.save(update_fields=["updated"])
        # Nothing was imported, so the plan this drop announced describes work that has not
        # happened. Left in place it would be served to the confirming POST's first poll as
        # a table of samples already under way.
        progress.discard()
        # 200 rather than 409: the client's postJson throws on any non-2xx and renders
        # `error`, so a status code would turn a question into a failure message.
        return JsonResponse({"needs_confirmation": ask.payload})
    except Exception as exc:
        logger.exception("breseq folder finalize failed for session %s", session.id)
        # Before the state change, so the last poll shows which unit it died on rather than
        # a table frozen mid-import with nothing saying why.
        return _fail(session, root, progress, str(exc), status=500)

    if downloaded:
        _record_downloads(session.experiment, downloaded, request.user)

    # The ticked reference annotators, after the reference is in and before the session is
    # closed -- still under the lock, since an annotator that runs inline reannotates and
    # rebuilds. Only when a reference actually landed: a refused annotation replace must not
    # then run a tool over the reference it refused to touch. Never after a data import,
    # whatever was posted, because the panels are only drawn on the reference tabs.
    summary["annotators"] = []
    if selections and _reference_landed(summary):
        with import_progress.reporting(progress):
            import_progress.stage("Running reference annotators\u2026")
            summary["annotators"] = run_annotators(
                session.experiment, selections, request.user)

    progress.finish()
    shutil.rmtree(root, ignore_errors=True)
    session.state = STATE_FINALIZED
    session.save(update_fields=["state", "updated"])
    summary["upload_id"] = str(session.id)
    # The Add page's dropdown and its banner are both scoped by this, and a drop that
    # establishes a reference -- a genome on its own, or a breseq folder bringing its own --
    # changes it underneath a page already rendered. Reporting it is what lets the page know
    # to go and get itself re-rendered rather than keep offering the pre-reference choices.
    summary["has_reference"] = reference_store.has_reference(session.experiment)
    return JsonResponse(summary)


@require_GET
def upload_progress(request, upload_id):
    """How far the finalize of this session has got. Polled while it runs.

    **Deliberately not `_open_session`.** That refuses anything not `STATE_OPEN`, and the
    single most useful moment to ask this question is the tick right after finalize returned,
    when the session is `finalized` -- so borrowing it would 409 exactly the poll that
    matters. The ownership check is the same; the state check is not wanted here.

    A session nothing has reported on yet answers with an empty unit list rather than a 404,
    because that is the honest answer between the POST going out and the first unit starting.

    **It must not write anything.** Under WAL a reader is never blocked, so this answers
    immediately however long the import's transaction is running; a single write would make it
    queue for the write lock that `BEGIN IMMEDIATE` holds for the whole of each sample, and the
    poll would then take as long as the sample in flight. `SESSION_SAVE_EVERY_REQUEST` was
    doing exactly that -- one `UPDATE django_session` per poll -- which is why a 20-30 sample
    drop looked like it refreshed every few seconds and showed no table until the first sample
    had committed.
    """
    # Read-only: see the docstring. `mutint_common.session_middleware` honours this.
    request.mutint_skip_session_save = True

    try:
        session = UploadSession.objects.get(pk=upload_id)
    except (UploadSession.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Unknown upload session."}, status=404)

    if session.user_id and session.user_id != getattr(request.user, "id", None):
        return JsonResponse({"error": "Not your upload session."}, status=403)

    snapshot = session.progress or {}
    units = snapshot.get("units") or []
    return JsonResponse({
        "state": snapshot.get("state") or session.state,
        "stage": snapshot.get("stage") or "",
        # Counted here rather than in the page, so "how many remain" has one authority.
        "total": len(units),
        "completed": sum(1 for unit in units if unit.get("state") == UNIT_DONE),
        "units": units,
    })


class _SessionProgress:
    """Turns `import_progress` events into a snapshot on the `UploadSession` row.

    A snapshot rather than an event log because the page wants a table pre-listed and filled
    in, which is a picture of the present rather than a history: each poll re-renders from
    whatever it is handed, so a poll that is slow, lost or doubled costs nothing and there is
    nothing to reconcile.

    Every write is swallowed on failure. Progress is commentary; the mutations are the point,
    and a locked SQLite file must not be able to fail an import that is otherwise fine.
    """

    def __init__(self, session_id):
        self.session_id = session_id
        self.units = []
        self.stage = ""
        self.cursor = 0

    def __call__(self, event):
        # `event["event"]` says which kind of event this is; `event["kind"]` says what kind of
        # thing the unit was. Two different questions, hence the longer name here.
        event_kind = event.get("event")
        if event_kind == "total":
            self.units = [{"file": name, "mutations": None, "kind": "", "error": None,
                           "warnings": [], "replaced": 0, "state": UNIT_WAITING}
                          for name in event.get("units") or []]
        elif event_kind == "begin":
            self._at(event.get("index"), lambda unit: unit.update(state=UNIT_WORKING))
        elif event_kind == "file":
            self._at(event.get("index"), lambda unit: unit.update(
                file=event.get("file", unit["file"]),
                mutations=event.get("mutations"),
                kind=event.get("kind") or "",
                error=event.get("error"),
                warnings=event.get("warnings") or [],
                replaced=event.get("replaced") or 0,
                state=UNIT_DONE))
        elif event_kind == "stage":
            self.stage = event.get("message") or ""
        self._flush()

    def _at(self, index, mutate):
        if isinstance(index, int) and 0 <= index < len(self.units):
            mutate(self.units[index])

    def fail(self, message):
        """The whole drop stopped. Say so on the unit it stopped on, and on the rest."""
        for unit in self.units:
            if unit["state"] == UNIT_WORKING:
                unit.update(state=UNIT_DONE, error=message)
            elif unit["state"] == UNIT_WAITING:
                unit.update(state=UNIT_DONE, error="not imported")
        self.stage = ""
        self._flush(state=STATE_FAILED)

    def finish(self):
        self.stage = ""
        self._flush(state=STATE_FINALIZED)

    def discard(self):
        self.units = []
        self.stage = ""
        self._flush()

    def _flush(self, state=STATE_OPEN):
        payload = {"state": state, "stage": self.stage, "units": self.units}
        try:
            # `.update()` rather than `save()`: it is one statement, it cannot clobber a
            # column another writer changed, and it does not need the row in memory. It also
            # bypasses `auto_now`, so `updated` is set by hand -- without which a long import
            # would age past the reaper's TTL while it was still running.
            UploadSession.objects.filter(pk=self.session_id).update(
                progress=payload, updated=timezone.now())
        except Exception:
            logger.warning("could not record import progress for session %s",
                           self.session_id, exc_info=True)


def reap_expired_sessions(now=None):
    """Delete staging dirs for sessions past their TTL. Returns how many were removed."""
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(
        hours=getattr(settings, "MUTINT_UPLOAD_SESSION_TTL_HOURS", 24))
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
