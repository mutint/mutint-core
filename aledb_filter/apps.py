from django.apps import AppConfig


class FilterConfig(AppConfig):
    name = "aledb_filter"

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        register_nav_item('Filter', url='/filter', section=EXPERIMENT_SECTION)
