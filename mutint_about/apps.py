from django.apps import AppConfig


class AboutConfig(AppConfig):
    name = 'mutint_about'

    def ready(self):
        from mutint_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        register_nav_item('About', url='/about', section=MAIN_SECTION)
