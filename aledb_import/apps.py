from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "aledb_import"

    def ready(self):
        from aledb_import.handlers import register_core_import_handlers
        register_core_import_handlers()

        from aledb_common.nav_registry import MAIN_SECTION, register_nav_item
        # Adding is done from inside an experiment now, so the two standalone import
        # entries are gone; this one is the fallback that explains where to go.
        register_nav_item('Add data', url_name='add', section=MAIN_SECTION)
