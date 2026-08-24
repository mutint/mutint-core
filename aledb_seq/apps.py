from django.apps import AppConfig


class SeqConfig(AppConfig):
    name = "aledb_seq"

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        # Order is the order of these calls -- there is no `order` parameter. Per-sample
        # first: it is the view of one sample's own calls, and the comparison across samples
        # reads as the step out from it.
        register_nav_item('Mutations', url='/mutations/breseq',
                          section=EXPERIMENT_SECTION)
        register_nav_item('Compare', url='/mutations',
                          section=EXPERIMENT_SECTION)
