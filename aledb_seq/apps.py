from django.apps import AppConfig


class SeqConfig(AppConfig):
    name = "aledb_seq"

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        # 'Compare' used to be registered here beside this. It is the aledb-compare
        # plugin's now, and registers itself -- which is what lets a deployment leave it
        # out. Because plugins load after every core app, its entry lands after this one
        # rather than immediately beside it.
        register_nav_item('Mutations', url='/mutations/breseq',
                          section=EXPERIMENT_SECTION)
