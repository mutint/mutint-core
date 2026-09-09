from django.apps import AppConfig


class ExperimentConfig(AppConfig):
    name = "mutint_experiment"

    def ready(self):
        from mutint_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        # `key='projects'` is what lets base.html put the selected project's own row
        # directly under this entry. See mutint_common/nav_registry.py.
        register_nav_item('Projects', url='/project/', section=MAIN_SECTION,
                          key='projects')
        register_nav_item('Experiments', url='/experiment/', section=MAIN_SECTION)
        # Groups is deliberately not registered. It is a per-user thing -- the groups you own
        # or belong to -- so it lives in base.html's account block beside Jobs and Change
        # Password, where `{% if user.is_authenticated %}` already governs it. Registered here
        # it would render for anonymous visitors and lead to a 403, which is the dead end
        # nav_registry has no way to express around.

        # Deleting the designated ancestor un-subtracts its mutations everywhere. A signal
        # rather than a call in each delete path, because there are several --
        # `remove_time_point`, `delete_sample`, the experiment purge -- and the one added
        # next is exactly the one that would forget. See
        # `mutint_experiment.ancestor.note_sample_deleted`.
        from django.db.models.signals import pre_delete
        from mutint_experiment.ancestor import note_sample_deleted
        pre_delete.connect(note_sample_deleted, sender="mutint_sample.Sample",
                           dispatch_uid="mutint_experiment.ancestor")
