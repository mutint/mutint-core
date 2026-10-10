from django.apps import AppConfig


class ExperimentConfig(AppConfig):
    name = "mutint_experiment"

    def ready(self):
        from mutint_common.nav_registry import (
            EXPERIMENT_SECTION, MAIN_SECTION, register_nav_item,
        )
        register_nav_item('Projects', url='/project/', section=MAIN_SECTION)
        # `key='experiments'` is what lets base.html put the selection -- the selected
        # project, its experiment and that experiment's pages -- directly under this entry.
        # See mutint_common/nav_registry.py.
        register_nav_item('Experiments', url='/experiment/', section=MAIN_SECTION,
                          key='experiments')
        # First in the experiment section, ahead of mutint_sample's Mutations, because this
        # app is ahead of that one in INSTALLED_APPS. Not `requires_edit`: the page opens as
        # a read-only table for any reader and gates its Edit button itself. The url
        # redirects to `/experiment/<pk>/samples/`, since every entry in this section is its
        # url with `?experiment_id=` appended.
        register_nav_item('Samples', url='/experiment/samples/', section=EXPERIMENT_SECTION)
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

        # The Storage panel on the Overview: how much disk this experiment's stored data
        # takes, per kind, with a Clear button beside each kind that can be. Through the
        # panel registry rather than written into stats.html, which makes core the first
        # user of its own seam -- and keeps the mutint_stats template knowing nothing about
        # storage, as it knows nothing about the needle plot.
        from mutint_common.panel_registry import register_overview_panel
        from mutint_experiment.storage_views import storage_panel_context
        register_overview_panel(self, name='storage', title='Storage',
                                template='experiment/_storage_panel.html',
                                context=storage_panel_context)
