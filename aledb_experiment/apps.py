from django.apps import AppConfig


class ExperimentConfig(AppConfig):
    name = "aledb_experiment"

    def ready(self):
        from aledb_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        register_nav_item('Projects', url='/project/', section=MAIN_SECTION)
        register_nav_item('Experiments', url='/experiment/', section=MAIN_SECTION)
        register_nav_item('Groups', url='/group/', section=MAIN_SECTION)

        # Deleting the designated ancestor un-subtracts its mutations everywhere. A signal
        # rather than a call in each delete path, because there are several -- `remove_flask`,
        # `delete_isolate`, the experiment purge -- and the one added next is exactly the one
        # that would forget. See `aledb_experiment.ancestor.note_sample_deleted`.
        from django.db.models.signals import pre_delete
        from aledb_experiment.ancestor import note_sample_deleted
        pre_delete.connect(note_sample_deleted, sender="aledb_seq.Sample",
                           dispatch_uid="aledb_experiment.ancestor")
