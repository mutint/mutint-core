from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "aledb_import"

    def ready(self):
        from aledb_common.nav_registry import MAIN_SECTION, register_nav_item
        register_nav_item('Import', url_name='gd_import', section=MAIN_SECTION)
