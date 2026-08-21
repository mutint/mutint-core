from django.apps import AppConfig


class AboutConfig(AppConfig):
    name = 'aledb_about'

    def ready(self):
        from aledb_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        register_nav_item('About', url='/about', section=MAIN_SECTION)
