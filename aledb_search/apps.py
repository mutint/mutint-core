from django.apps import AppConfig


class SearchConfig(AppConfig):
    name = "aledb_search"

    def ready(self):
        from aledb_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        register_nav_item('Search', url='/search/', section=MAIN_SECTION)
