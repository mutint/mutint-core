"""Registry of import types contributed by apps.

Mirrors ``plugin_registry.register_export_handler`` -- which already drives a UI dropdown from
a registry -- so an app that can ingest a new kind of file registers it here and appears in the
Add page's type dropdown with no edit to core.

Apps register in ``AppConfig.ready()``::

    register_import_handler(
        name='breseq_folder',
        label='breseq result folder',
        patterns=['output/annotated.gd', 'data/reference.bam'],
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


def register_import_handler(name, label, patterns, handle,
                            priority=PRIORITY_DATA, detect=None, description=""):
    """Register an import type.

    name        stable slug; the value the Add page's dropdown submits
    label       human-readable, shown in the dropdown
    patterns    lowercase path suffixes this handler claims (e.g. '.gbk',
                'data/reference.bam'). Serialised to the client so it can bucket a
                drop before uploading, and used as the default `detect`.
    handle      callable(experiment, staged_root, paths, user) -> summary dict with
                keys 'files' (list of {file, mutations, error}) and 'total_mutations'
    priority    lower runs first; use PRIORITY_REFERENCE for anything that must be
                in place before data is imported
    detect      optional callable(staged_root, paths) -> claimed paths, for handlers
                whose shape is not expressible as suffixes (breseq folders need to
                see a whole directory). Defaults to suffix matching on `patterns`.
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
    })


def get_import_handlers():
    """All registered handlers, in the order they should run."""
    return sorted(_import_handlers, key=lambda h: (h["priority"], h["name"]))


def get_import_handler(name):
    for handler in _import_handlers:
        if handler["name"] == name:
            return handler
    return None


def get_import_types():
    """The dropdown's contents: JSON-safe, no callables."""
    return [{
        "name": h["name"],
        "label": h["label"],
        "patterns": h["patterns"],
        "description": h["description"],
    } for h in get_import_handlers()]


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


def run_import(experiment, staged_root, user, import_type=None):
    """Route a staged drop through the registered handlers.

    ``import_type`` None means auto-detect: every handler claims what it recognises and the
    matched ones run in priority order. Naming a type forces that single handler, and anything
    it does not claim is reported as an error rather than quietly routed elsewhere -- that is
    the point of choosing explicitly.

    Returns the standard summary dict, so callers render one shape either way.
    """
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
        summary = handler["handle"](experiment, staged_root, claimed, user)
        file_results.extend(summary.get("files") or [])
        total_mutations += summary.get("total_mutations") or 0

    for path in sorted(unclaimed):
        file_results.append({
            "file": path,
            "mutations": 0,
            "error": ("not recognised as %s" % get_import_handler(import_type)["label"]
                      if import_type else "not recognised by any import type"),
        })

    return {
        "experiment_id": experiment.ale_id,
        "experiment": experiment.name,
        "total_mutations": total_mutations,
        "files": file_results,
    }
