from django.apps import AppConfig


class AboutConfig(AppConfig):
    name = 'mutint_about'

    def ready(self):
        from mutint_common.nav_registry import (
            END_SECTION, register_nav_item,
        )
        # END_SECTION: at the foot of the sidebar, under the selected experiment's pages.
        register_nav_item('About', url='/about', section=END_SECTION)
