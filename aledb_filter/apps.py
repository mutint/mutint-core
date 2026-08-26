from django.apps import AppConfig


class FilterConfig(AppConfig):
    name = "aledb_filter"

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        from aledb_common.rebuild_registry import PRIORITY_SETTINGS, register_rebuilder
        from aledb_filter.util import ensure_default_experiment_filter

        register_nav_item('Filter', url='/filter', section=EXPERIMENT_SECTION)
        # First of everything: the rest of the rebuilds count mutations through this row.
        register_rebuilder('experiment_filter', ensure_default_experiment_filter,
                           label='Experiment filter defaults', priority=PRIORITY_SETTINGS)
