from django.apps import AppConfig


class MutationEditorConfig(AppConfig):
    name = "aledb_mutation_editor"

    def ready(self):
        from aledb_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )

        # One entry, not three. Copy and History are reached from the editor page: they are
        # the same task at different moments, and three sidebar rows for one feature would
        # crowd out the apps that genuinely are separate.
        register_nav_item('Edit Mutations', url='/mutation-editor/',
                          section=EXPERIMENT_SECTION)

        # Deliberately no register_about_section() here. About entries are keyed by the
        # *component directory* an app belongs to, so a second registration from aledb-core
        # would replace aledb_common's entry for the whole checkout rather than adding one.
