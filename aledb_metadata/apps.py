from django.apps import AppConfig


class MetadatConfig(AppConfig):
    name = 'aledb_metadata'

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        register_nav_item('Metadata', url='/metadata', section=EXPERIMENT_SECTION)
