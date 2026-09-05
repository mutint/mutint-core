from django.apps import AppConfig


class StatsConfig(AppConfig):
    name = "mutint_stats"

    # No rebuilders, and no derived data to register them for. `static_data` (the needle plot)
    # and `overview` (the Overview's counts) were registered here while `StaticData` and
    # `ExperimentSummary` existed to keep fresh. Both are computed by the request that needs
    # them -- 0.05s and 0.07s on the largest experiment in the dev database -- which is less
    # than keeping either stored answer correct was worth. See `mutint_stats/models.py`.
