_post_experiment_hooks = []
_plugin_urlpatterns = []
_export_handlers = {}


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


def register_export_handler(type_str, fn):
    """Register fn(ale_experiment_id) -> ObservedMutation queryset for a named export type."""
    _export_handlers[type_str] = fn


def get_export_handler(type_str):
    return _export_handlers.get(type_str)
