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

        # The strip those pages share, in the order it reads. Registered rather than written
        # into the toolbar template so a plugin can put a page of its own in it -- History is
        # `last` so it stays the tail whatever is registered after this app. Literal paths,
        # as the nav entry's is: `^$` is the Edit tab, and reversing adds nothing.
        from mutint_common.curate_tab_registry import register_curate_tab

        register_curate_tab("edit", "Edit", url="/curate/")
        register_curate_tab("delete", "Delete", url="/curate/delete")
        register_curate_tab("add", "Add", url="/curate/add")
        register_curate_tab("copy", "Copy", url="/curate/copy")
        register_curate_tab("history", "History", url="/curate/history", last=True)

        # Deliberately no register_about_section() here. About entries are keyed by the
        # *component directory* an app belongs to, so a second registration from mutint-core
        # would replace mutint_common's entry for the whole checkout rather than adding one.
