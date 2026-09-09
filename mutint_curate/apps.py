from django.apps import AppConfig


class CurateConfig(AppConfig):
    name = "mutint_curate"

    def ready(self):
        from mutint_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )

        # One entry, not three. Copy and History are reached from the editor page: they are
        # the same task at different moments, and three sidebar rows for one feature would
        # crowd out the apps that genuinely are separate.
        # `requires_edit`: every tab here writes, and the page tells a reader with no
        # write role that they cannot -- which is worth saying on a page they navigated to
        # and not worth an entry that only ever leads there.
        register_nav_item('Curate', url='/curate/',
                          section=EXPERIMENT_SECTION, requires_edit=True)

        # Deliberately no register_about_section() here. About entries are keyed by the
        # *component directory* an app belongs to, so a second registration from mutint-core
        # would replace mutint_common's entry for the whole checkout rather than adding one.
