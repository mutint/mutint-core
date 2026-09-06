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

# What a file result's count column is about. A file that carried no mutations because it is
# not a mutation file has no count to report: rendering the 0 it technically imported reads as
# a mutation file that imported nothing, which is the one thing a reference genome is not.
# Absent from an entry means the count is a mutation count, which is every other handler.
KIND_REFERENCE = "reference"


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
                            only_without_reference=False, accepts_options=False,
                            list_units=None, menu_order=None, directories=()):
    """Register an import type.

    name        stable slug; the value the Import data page's dropdown submits
    label       human-readable, shown in the dropdown
    patterns    lowercase path suffixes this handler claims (e.g. '.gbk',
                'data/reference.bam'). Serialized to the client so it can bucket a
                drop before uploading, and used as the default `detect`.
    handle      callable(experiment, staged_root, paths, user) -> summary dict with
                keys 'files' (list of {file, mutations, error, warnings}) and
                'total_mutations'. `error` means the file failed; `warnings` lists what
                the parser could not read in a file that otherwise imported. An entry
                whose count is not a mutation count says so with `kind` -- see
                KIND_REFERENCE -- and sets `mutations` to None.
    priority    lower runs first; use PRIORITY_REFERENCE for anything that must be
                in place before data is imported
    detect      optional callable(staged_root, paths) -> claimed paths, for handlers
                whose shape is not expressible as suffixes (breseq folders need to
                see a whole directory). Defaults to suffix matching on `patterns`.
    requires_reference
                the type cannot run until the experiment has a reference genome, and
                is left out of the Import data page's dropdown until it does. The handler still
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
    list_units  optional callable(staged_root, claimed) -> [display names], for
                naming what this handler is about to process before it starts.
                Defaults to `claimed` itself, which is right whenever one claimed
                file is one unit of work -- and wrong for `breseq_folder`, where a
                sample is a directory of five claimed files and counting paths
                would overstate the work fivefold.

                **Each name must equal the `file` key the matching result carries**,
                or a caller pairing an announcement against a result has nothing to
                pair on. The handlers disagree about what that name is -- a
                directory basename, a filename, a path relative to the drop root --
                so this mirrors one handler's own reporting rather than imposing a
                rule. `mutint_common.import_progress` is what consumes it.
    menu_order  where this sits in the Import data page's dropdown, lower first. Defaults to
                `priority`, so a type that says nothing keeps the position it had.

                **This exists because `priority` cannot be moved to do the job.**
                `priority` is the order handlers *run* in, and a reference genome has
                to be established before mutations that are hash-checked against it --
                so it is correctness, not presentation, and reordering the menu by
                editing it would silently change what a mixed drop does. What somebody
                reaches for most often and what has to happen first are different
                questions and now have different answers.

                Only the dropdown is sorted by it. `get_import_types()` stays in
                priority order, because the Import data page walks *that* list to name what an
                unrecognized file looks like and wants the handler that would really
                claim it named first.
    directories directory names whose whole contents this handler claims, wherever they
                appear in the drop.

                **The thing `patterns` cannot express**: patterns are suffix matches, and
                "every file under `output/`" is not a suffix. breseq's HTML report is
                exactly that shape -- breseq chooses the filenames, they differ between
                releases, and older ones nest an `evidence/` directory inside it.

                It matters on the *client* as much as here. The Add page decides what it
                will upload from these declarations before a byte is sent, so a directory
                no handler declares is never transferred and the server's own `detect`
                never gets to see it.

                Broader than a pattern by construction, so declare one only where the
                handler identifies the enclosing folder another way --
                `detect_breseq_folders` claims `output/` only inside a directory that
                already holds `data/output.gd`.
    """
    if any(handler["name"] == name for handler in _import_handlers):
        raise ValueError("import handler %r is already registered" % (name,))
    _import_handlers.append({
        "name": name,
        "label": label,
        "patterns": [p.lower() for p in patterns],
        "directories": [d.strip("/").lower() for d in directories],
        "priority": priority,
        "handle": handle,
        "detect": detect,
        "description": description,
        "requires_reference": requires_reference,
        "only_without_reference": only_without_reference,
        "accepts_options": accepts_options,
        "list_units": list_units,
        "menu_order": priority if menu_order is None else menu_order,
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

    A type that cannot run yet is left out rather than shown grayed. A dropdown entry
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
    # Sorted for the menu, not for the run. See `menu_order` on register_import_handler:
    # the two orders answer different questions and a shared one served neither well --
    # the reference types led because they run first, so the commonest thing anybody
    # comes here to do sat at the bottom.
    offered.sort(key=lambda entry: (entry["menu_order"], entry["name"]))
    return offered


def get_import_types():
    """The dropdown's contents: JSON-safe, no callables."""
    return [{
        "name": h["name"],
        "label": h["label"],
        "patterns": h["patterns"],
        "directories": h["directories"],
        "description": h["description"],
        "requires_reference": h["requires_reference"],
        "only_without_reference": h["only_without_reference"],
        "menu_order": h["menu_order"],
    } for h in get_import_handlers()]


def identify(path, exclude=None):
    """The registered type whose patterns claim `path`, or None if none do.

    Turns "not recognized as Reference genome" into a sentence that says what to do about
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


def units(handler, staged_root, claimed):
    """What this handler will report as it works, named the way it will name it.

    One claimed file is one unit unless the handler says otherwise, which only
    `breseq_folder` does -- see `list_units` on `register_import_handler`.
    """
    if handler.get("list_units") is not None:
        return list(handler["list_units"](staged_root, claimed))
    return list(claimed)


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
        return "not recognized by any import type"

    chosen = get_import_handler(import_type)
    elsewhere = identify(path, exclude=import_type)
    if elsewhere is not None:
        return ("not recognized as %s -- it looks like %s, so import it with that type"
                % (chosen["label"], elsewhere["label"]))
    return "not recognized as %s" % (chosen["label"],)


def run_import(experiment, staged_root, user, import_type=None, options=None):
    """Route a staged drop through the registered handlers.

    ``import_type`` None means auto-detect: every handler claims what it recognizes and the
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

    **Every claim is settled before any handler runs**, so the whole unit list can be
    announced to `mutint_common.import_progress` up front rather than growing a handler at a
    time. That is safe because claiming reads the staged tree and nothing else: all four
    core `detect` functions match on paths and suffixes, and none consults the database. The
    `reference` handler does establish a genome the later ones are hash-checked against, but
    that decides their *results*, never their *claims* -- the `has_reference` tests live in
    each `handle_*`, not in its `detect_*`. Splitting the loop therefore changes which files
    a handler receives not at all.
    """
    # Imported here rather than at module scope: this module is in mutint_common, which the
    # registries keep free of app-level imports so it can be loaded before the app registry
    # is ready. `rebuild_registry` defers its model imports the same way.
    from mutint_experiment.permissions import ExperimentLocked

    from mutint_common import import_progress

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

    # Pass one: who takes what, and what each will call the pieces.
    plan = []
    unclaimed = set(paths)
    announced = []
    for handler in handlers:
        claimed = [p for p in claim(handler, staged_root, paths) if p in unclaimed]
        if not claimed:
            continue
        unclaimed -= set(claimed)
        mine = units(handler, staged_root, claimed)
        plan.append((handler, claimed, len(mine)))
        announced.extend(mine)

    # The leftovers are reported as failed rows below, so they are units too -- a file nobody
    # claimed is a line somebody has to read.
    leftovers = sorted(unclaimed)
    announced.extend(leftovers)
    import_progress.announce(announced)

    # Pass two: run it.
    file_results = []
    total_mutations = 0

    cursor = 0
    for handler, claimed, unit_count in plan:
        # Each handler writes into its own slice of the announcement, so one that reports
        # nothing costs its rows and nobody else's.
        import_progress.advance_to(cursor)
        summary = _call(handler, experiment, staged_root, claimed, user, options)
        cursor += unit_count
        file_results.extend(summary.get("files") or [])
        total_mutations += summary.get("total_mutations") or 0

    import_progress.advance_to(cursor)
    for path in leftovers:
        entry = {
            "file": path,
            "mutations": 0,
            "error": _unclaimed_reason(path, import_type),
            "warnings": [],
        }
        file_results.append(entry)
        import_progress.report(entry)

    return {
        "experiment_id": experiment.id,
        "experiment": experiment.name,
        "total_mutations": total_mutations,
        "files": file_results,
    }
