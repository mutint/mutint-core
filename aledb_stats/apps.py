from django.apps import AppConfig


class StatsConfig(AppConfig):
    name = "aledb_stats"

    def ready(self):
        from aledb_common.rebuild_registry import register_rebuilder
        from aledb_stats.util import build_experiment_summary, generate_static_data

        register_rebuilder('static_data', generate_static_data,
                           label='Needle plot data')
        register_rebuilder('overview', build_experiment_summary,
                           label='Overview mutation counts')
