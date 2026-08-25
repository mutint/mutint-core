import logging

logger = logging.getLogger(__name__)

_post_experiment_hooks = []
_sequence_rename_hooks = []
_plugin_urlpatterns = []
_export_handlers = {}
_export_labels = {}


def register_post_experiment_hook(fn):
    """Register fn(ale_experiment_id) to be called after each experiment upload."""
    _post_experiment_hooks.append(fn)


def run_post_experiment_hooks(ale_experiment_id):
    for fn in _post_experiment_hooks:
        fn(ale_experiment_id)


def register_sequence_rename_hook(fn):
    """Register fn(ale_experiment_id, renames) for when an experiment's contigs are renamed.

    `renames` is `{old_name: new_name}`, covering only the names that changed.

    A rename replaces the contig names an experiment's mutations carry -- the same genome,
    relabelled. Core rewrites everything it owns (`Mutation.reseq_reference`, the verbatim
    `gd_data`, the missing-coverage evidence, the reference row), but anything an app
    *derived* from those names is core's blind spot, and a plugin's derived data keyed on
    the old name is silently wrong rather than visibly broken.

    aledb-phylogeny is the live example: its character-matrix columns are identified by
    `(reseq_reference, position, ...)` and ordered by the same field, so a rename changes
    both which column a mutation belongs to and where that column sits.

    Distinct from `register_post_experiment_hook`, which says "this experiment's data
    changed" and carries no detail. A rename hook needs the mapping: a rebuild-from-scratch
    is one valid response, but so is rewriting a stored blob in place, and only the caller
    can decide which.
    """
    _sequence_rename_hooks.append(fn)


def run_sequence_rename_hooks(ale_experiment_id, renames):
    """Fire every rename hook, isolating failures.

    One plugin raising must not take down a rename that has already been committed -- the
    names are the truth now, and a hook that failed leaves that plugin's derived data stale,
    which is recoverable, where propagating the exception is not.
    """
    if not renames:
        return
    for fn in _sequence_rename_hooks:
        try:
            fn(ale_experiment_id, dict(renames))
        except Exception:  # noqa: BLE001
            logger.exception("sequence rename hook %r failed for experiment %s",
                             getattr(fn, "__name__", fn), ale_experiment_id)


def register_plugin_urlpatterns(patterns):
    """Register additional URL patterns (called from AppConfig.ready())."""
    _plugin_urlpatterns.extend(patterns)


def get_plugin_urlpatterns():
    return list(_plugin_urlpatterns)


def register_export_handler(type_str, fn, label=None):
    """Register fn(ale_experiment_id) -> ObservedMutation queryset for a named export type.

    label is the human-readable name shown in export menus; it defaults to type_str.
    """
    _export_handlers[type_str] = fn
    _export_labels[type_str] = label or type_str


def get_export_handler(type_str):
    return _export_handlers.get(type_str)


def get_export_types():
    """[(type_str, label), ...] for all registered plugin export types, sorted by label."""
    return sorted(((t, _export_labels[t]) for t in _export_handlers), key=lambda pair: pair[1])
