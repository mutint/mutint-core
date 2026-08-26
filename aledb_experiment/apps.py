from django.apps import AppConfig


class ExperimentConfig(AppConfig):
    name = "aledb_experiment"

    def ready(self):
        from aledb_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        register_nav_item('Projects', url='/ale/projects/', section=MAIN_SECTION)
        register_nav_item('Experiments', url='/ale/experiments/', section=MAIN_SECTION)
        register_nav_item('Groups', url='/ale/groups/', section=MAIN_SECTION)
