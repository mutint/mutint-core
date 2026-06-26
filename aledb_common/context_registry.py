_experiment_context_providers = []


def register_experiment_context_provider(fn):
    """Register a callable(experiment) → dict to enrich experiment view contexts."""
    _experiment_context_providers.append(fn)
    return fn


def get_experiment_context(experiment):
    """Return merged context dict from all registered providers."""
    context = {}
    for provider in _experiment_context_providers:
        try:
            context.update(provider(experiment))
        except Exception:
            pass
    return context
