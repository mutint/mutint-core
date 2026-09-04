import inspect
import logging

logger = logging.getLogger(__name__)

_sequence_rename_hooks = []
_plugin_urlpatterns = []
_export_handlers = {}
_export_labels = {}
#: Whether each handler's signature can accept the reader's filter. See below.
_export_takes_filter = {}


def register_post_experiment_hook(fn):
    """Register fn(experiment_id) to be called after each experiment upload.

    Kept as the name plugins already call. It is a thin wrapper over
    `aledb_common.rebuild_registry.register_rebuilder`, which is the general form: a
    post-experiment hook is exactly a rebuild of derived data belonging to one experiment,
    and the registry adds the two things this never had -- a name, so a caller can ask for
    one rebuild and not the rest, and a record of whether the data is currently stale.

    The rebuilder is named after the app the function came from -- `aledb_fixation`,
    `aledb_converge` -- which is what `./aledb rebuild --only` and the `only=` argument take.
    A second hook from the same app has its function name appended, and a third a counter,
    so registering twice is not an error the way `register_rebuilder` alone would make it --
    the old API allowed it, including two anonymous functions, and still does.

    Returns the name it registered under, which is what `unregister_rebuilder` takes.

    New code should call `register_rebuilder` directly and choose its own name.
    """
    from aledb_common.rebuild_registry import get_rebuilder, register_rebuilder

    app = (getattr(fn, "__module__", "") or "").split(".")[0]
    fn_name = getattr(fn, "__name__", "hook")
    for candidate in _candidate_names(app, fn_name):
        if get_rebuilder(candidate) is None:
            register_rebuilder(candidate, fn)
            return candidate


def _candidate_names(app, fn_name):
    """Names to try, least qualified first, ending in an unbounded numbered sequence."""
    stem = "%s.%s" % (app, fn_name) if app else fn_name
    if app:
        yield app
    yield stem
    counter = 2
    while True:
        yield "%s.%d" % (stem, counter)
        counter += 1


def run_post_experiment_hooks(experiment_id):
    """Run every registered rebuild for this experiment, stale or not.

    Retained because it is the name the import path and the sample editor call, and because
    tests patch it. It runs *everything* registered, not only what was registered through
    `register_post_experiment_hook`, which is the point of folding the two together: core's
    own derived data used to be rebuilt by hardcoded statements either side of this call, and
    a plugin had no way to ask for it.

    `force=True` -- the callers of this reach it having just changed an experiment's
    mutations, so asking whether the data is stale would only re-derive what they already
    know. Call `request_rebuild` then `run_rebuilds` to have staleness respected.
    """
    from aledb_common.rebuild_registry import run_rebuilds

    run_rebuilds(experiment_id, force=True)


def register_sequence_rename_hook(fn):
    """Register fn(experiment_id, renames) for when an experiment's contigs are renamed.

    `renames` is `{old_name: new_name}`, covering only the names that changed.

    A rename replaces the contig names an experiment's mutations carry -- the same genome,
    relabelled. Core rewrites everything it owns (`Mutation.seq_id`, the verbatim
    the stored GenomeDiff record, the missing-coverage evidence, the reference row), but
    anything an app
    *derived* from those names is core's blind spot, and a plugin's derived data keyed on
    the old name is silently wrong rather than visibly broken.

    aledb-phylogeny is the live example: its character-matrix columns are identified by
    `(seq_id, position, ...)` and ordered by the same field, so a rename changes
    both which column a mutation belongs to and where that column sits.

    Distinct from `register_post_experiment_hook`, which says "this experiment's data
    changed" and carries no detail. A rename hook needs the mapping: a rebuild-from-scratch
    is one valid response, but so is rewriting a stored blob in place, and only the caller
    can decide which.
    """
    _sequence_rename_hooks.append(fn)


def run_sequence_rename_hooks(experiment_id, renames):
    """Fire every rename hook, isolating failures.

    One plugin raising must not take down a rename that has already been committed -- the
    names are the truth now, and a hook that failed leaves that plugin's derived data stale,
    which is recoverable, where propagating the exception is not.
    """
    if not renames:
        return
    for fn in _sequence_rename_hooks:
        try:
            fn(experiment_id, dict(renames))
        except Exception:  # noqa: BLE001
            logger.exception("sequence rename hook %r failed for experiment %s",
                             getattr(fn, "__name__", fn), experiment_id)


def register_plugin_urlpatterns(patterns):
    """Register additional URL patterns (called from AppConfig.ready())."""
    _plugin_urlpatterns.extend(patterns)


def get_plugin_urlpatterns():
    return list(_plugin_urlpatterns)


def register_export_handler(type_str, fn, label=None):
    """Register `fn(experiment_id, view_filter=None)` -> MutationCall queryset.

    `label` is the human-readable name shown in export menus; it defaults to `type_str`.

    **The second argument is optional and a handler may simply not have it.** The contract was
    `fn(experiment_id)` before filtering became a per-reader affair, and a download should carry
    the filter the page it was launched from was showing -- but no plugin should have to be
    edited to keep working. The signature is inspected once, here, and `get_export_handler`
    returns a callable that always takes both and forwards or drops the filter to suit.

    Inspecting rather than `try: fn(a, b) except TypeError: fn(a)`, because that swallows a
    `TypeError` raised *inside* the handler and turns a plugin's bug into a silently unfiltered
    export -- which looks like data, not like a failure.
    """
    _export_handlers[type_str] = fn
    _export_takes_filter[type_str] = _accepts_view_filter(fn)
    _export_labels[type_str] = label or type_str


def _accepts_view_filter(fn):
    """Whether `fn` can be handed a view filter: by name, by **kwargs, or by arity."""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False        # a builtin or C callable; assume the old contract
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return True
    positional = [p for p in parameters.values()
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    return "view_filter" in parameters or len(positional) >= 2


def get_export_handler(type_str):
    """The handler for a type, normalised to `(experiment_id, view_filter=None)`.

    Always safe to call with both arguments; one that predates the filter never sees it.
    """
    handler = _export_handlers.get(type_str)
    if handler is None:
        return None
    if _export_takes_filter.get(type_str):
        return handler

    def without_filter(experiment_id, view_filter=None):
        return handler(experiment_id)

    return without_filter


def get_export_types():
    """[(type_str, label), ...] for all registered plugin export types, sorted by label."""
    return sorted(((t, _export_labels[t]) for t in _export_handlers), key=lambda pair: pair[1])
