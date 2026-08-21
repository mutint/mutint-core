from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "aledb_dashboard"

    def ready(self):
        from aledb_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        register_nav_item('Dashboard', url='/dashboard', section=MAIN_SECTION)
