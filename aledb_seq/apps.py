from django.apps import AppConfig


class SeqConfig(AppConfig):
    name = "aledb_seq"

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        register_nav_item('Mutations', url='/mutations', section=EXPERIMENT_SECTION)
        register_nav_item('Samples', url='/mutations/breseq',
                          section=EXPERIMENT_SECTION)
