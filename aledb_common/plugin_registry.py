_post_experiment_hooks = []
_plugin_urlpatterns = []
_export_handlers = {}
_export_labels = {}


def register_post_experiment_hook(fn):
    """Register fn(ale_experiment_id) to be called after each experiment upload."""
    _post_experiment_hooks.append(fn)


def run_post_experiment_hooks(ale_experiment_id):
    for fn in _post_experiment_hooks:
        fn(ale_experiment_id)


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
