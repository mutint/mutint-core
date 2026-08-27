"""Registry of import types contributed by apps.

Mirrors ``plugin_registry.register_export_handler`` -- which already drives a UI dropdown from
a registry -- so an app that can ingest a new kind of file registers it here and appears in the
Add page's type dropdown with no edit to core.

Apps register in ``AppConfig.ready()``::

    register_import_handler(
        name='breseq_folder',
        label='breseq result folder',
        patterns=['data/output.gd', 'data/reference.bam'],
        priority=50,
        handle=import_breseq_folders_from_staging,
    )

Unlike ``nav_registry``, this registry **has explicit ordering**. There, appearance order is
cosmetic and deliberately follows INSTALLED_APPS. Here order is correctness: a reference genome
must be established before mutations that will be hash-checked against it, whatever order the
apps happen to load in. Hence ``priority`` -- lower runs first.
"""

import os

_import_handlers = []

PRIORITY_REFERENCE = 10
PRIORITY_DATA = 50


class ConfirmationRequired(Exception):
    """A handler will not proceed until the user agrees to something.

    Carries `payload`, a JSON-safe description the caller renders and echoes back with the
    agreement. Deliberately not a per-file error: it describes the whole drop, and flattening
    it into the `files` list would present a question as a failure.
    """

    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload.get("message") or "confirmation required")


def register_import_handler(name, label, patterns, handle,
                            priority=PRIORITY_DATA, detect=None, description="",
                            requires_reference=False,
                            only_without_reference=False, accepts_options=False):
    """Register an import type.

    name        stable slug; the value the Add page's dropdown submits
    label       human-readable, shown in the dropdown
    patterns    lowercase path suffixes this handler claims (e.g. '.gbk',
                'data/reference.bam'). Serialised to the client so it can bucket a
                drop before uploading, and used as the default `detect`.
    handle      callable(experiment, staged_root, paths, user) -> summary dict with
                keys 'files' (list of {file, mutations, error, warnings}) and
                'total_mutations'. `error` means the file failed; `warnings` lists what
                the parser could not read in a file that otherwise imported.
    priority    lower runs first; use PRIORITY_REFERENCE for anything that must be
                in place before data is imported
    detect      optional callable(staged_root, paths) -> claimed paths, for handlers
                whose shape is not expressible as suffixes (breseq folders need to
                see a whole directory). Defaults to suffix matching on `patterns`.
    requires_reference
                the type cannot run until the experiment has a reference genome, and
                is left out of the Add page's dropdown until it does. The handler still
                enforces it server-side either way.
    accepts_options
                the handler takes a fifth `options` argument -- the caller's per-request
                dict, e.g. an answer to a ConfirmationRequired it raised earlier. Opt-in so
                the documented four-argument `handle` contract keeps working untouched: a
                plugin's handler must not have to change because core grew a seam.
    only_without_reference
                the inverse: the type stops being offered once a reference exists,
                because establishing one is a one-time act. Nothing enforces this
                server-side -- re-establishing the same genome is harmless, it is
                just not a thing worth offering.
    """
    if any(handler["name"] == name for handler in _import_handlers):
        raise ValueError("import handler %r is already registered" % (name,))
    _import_handlers.append({
        "name": name,
        "label": label,
        "patterns": [p.lower() for p in patterns],
        "priority": priority,
        "handle": handle,
        "detect": detect,
        "description": description,
        "requires_reference": requires_reference,
        "only_without_reference": only_without_reference,
        "accepts_options": accepts_options,
    })


def get_import_handlers():
    """All registered handlers, in the order they should run."""
    return sorted(_import_handlers, key=lambda h: (h["priority"], h["name"]))


def get_import_handler(name):
    for handler in _import_handlers:
        if handler["name"] == name:
            return handler
    return None


def get_import_types_for(has_reference):
    """The dropdown's contents for one experiment: only the types that can run now.

    A type that cannot run yet is left out rather than shown greyed. A dropdown entry
    you can see but not choose is a dead end -- the page's own banner is where the
    answer ("get a reference in first") belongs, and it says so whether or not the
    entry is there to point at. Computed here rather than in the template so the
    dropdown and the JSON the page classifies a drop with cannot disagree.
    """
    offered = []
    for entry in get_import_types():
        if entry["only_without_reference"] and has_reference:
            continue
        if entry["requires_reference"] and not has_reference:
            continue
        offered.append(dict(entry))
    return offered


def get_import_types():
    """The dropdown's contents: JSON-safe, no callables."""
    return [{
        "name": h["name"],
        "label": h["label"],
        "patterns": h["patterns"],
        "description": h["description"],
        "requires_reference": h["requires_reference"],
        "only_without_reference": h["only_without_reference"],
    } for h in get_import_handlers()]


def identify(path, exclude=None):
    """The registered type whose patterns claim `path`, or None if none do.

    Turns "not recognised as Reference genome" into a sentence that says what to do about
    it. The answer is in the registry either way, so a plugin's type names itself here with
    no edit to this module -- and a type that has an entry in the dropdown for exactly this
    file is a better thing to point at than a list of extensions.

    Patterns only, never a handler's own `detect`: this runs on a file the chosen handler
    declined, where the point is to name a likely alternative rather than to settle what the
    file is. `run_import` still routes on `claim`.
    """
    for handler in get_import_handlers():
        if exclude is not None and handler["name"] == exclude:
            continue
        if matches_patterns(path, handler["patterns"]):
            return handler
    return None


def matches_patterns(path, patterns):
    lowered = path.replace(os.sep, "/").lower()
    return any(lowered.endswith(pattern) for pattern in patterns)


def default_detect(handler, _staged_root, paths):
    return [p for p in paths if matches_patterns(p, handler["patterns"])]


def claim(handler, staged_root, paths):
    """Which of `paths` this handler will take."""
    if handler["detect"] is not None:
        return list(handler["detect"](staged_root, paths))
    return default_detect(handler, staged_root, paths)


def walk_files(staged_root):
    """Every staged file, as paths relative to `staged_root`, sorted."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(staged_root):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            found.append(os.path.relpath(full, staged_root))
    return sorted(found)


def _call(handler, experiment, staged_root, claimed, user, options):
    if handler.get("accepts_options"):
        return handler["handle"](experiment, staged_root, claimed, user, options or {})
    return handler["handle"](experiment, staged_root, claimed, user)


def _unclaimed_reason(path, import_type):
    """Why a staged file was not imported, and where it should have gone instead."""
    if not import_type:
        # Auto-detect: every handler already had its chance, so there is no other type to
        # point at. Reached only when a handler's own `detect` declined what its patterns
        # would have matched.
        return "not recognised by any import type"

    chosen = get_import_handler(import_type)
    elsewhere = identify(path, exclude=import_type)
    if elsewhere is not None:
        return ("not recognised as %s -- it looks like %s, so import it with that type"
                % (chosen["label"], elsewhere["label"]))
    return "not recognised as %s" % (chosen["label"],)


def run_import(experiment, staged_root, user, import_type=None, options=None):
    """Route a staged drop through the registered handlers.

    ``import_type`` None means auto-detect: every handler claims what it recognises and the
    matched ones run in priority order. Naming a type forces that single handler, and anything
    it does not claim is reported as an error rather than quietly routed elsewhere -- that is
    the point of choosing explicitly.

    ``options`` is passed only to handlers that declared ``accepts_options``; it carries
    per-request answers, such as agreement to a rename the handler asked about.

    Returns the standard summary dict, so callers render one shape either way. A handler may
    instead raise ``ConfirmationRequired``, which propagates: it is a question about the whole
    drop, not a per-file result.

    **A locked experiment is refused here**, not only at the view. This is the single funnel
    every import type passes through -- core's four and any a plugin registers -- and the
    upload flow needs it: `upload_session` checks permission when the session is *created*
    and never again, so a session opened before the lock would otherwise still ingest after
    it. Raising `ExperimentLocked` rather than returning an error summary because this is a
    refusal of the whole drop, the same shape `ConfirmationRequired` already takes.
    """
    # Imported here rather than at module scope: this module is in aledb_common, which the
    # registries keep free of app-level imports so it can be loaded before the app registry
    # is ready. `rebuild_registry` defers its model imports the same way.
    from aledb_experiment.permissions import ExperimentLocked

    if experiment is not None and getattr(experiment, "is_locked", False):
        raise ExperimentLocked(experiment.lock_message())

    paths = walk_files(staged_root)

    if import_type:
        handler = get_import_handler(import_type)
        if handler is None:
            raise ValueError("unknown import type: %r" % (import_type,))
        handlers = [handler]
    else:
        handlers = get_import_handlers()

    file_results = []
    total_mutations = 0
    unclaimed = set(paths)

    for handler in handlers:
        claimed = [p for p in claim(handler, staged_root, paths) if p in unclaimed]
        if not claimed:
            continue
        unclaimed -= set(claimed)
        summary = _call(handler, experiment, staged_root, claimed, user, options)
        file_results.extend(summary.get("files") or [])
        total_mutations += summary.get("total_mutations") or 0

    for path in sorted(unclaimed):
        file_results.append({
            "file": path,
            "mutations": 0,
            "error": _unclaimed_reason(path, import_type),
            "warnings": [],
        })

    return {
        "experiment_id": experiment.ale_id,
        "experiment": experiment.name,
        "total_mutations": total_mutations,
        "files": file_results,
    }
