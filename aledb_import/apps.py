from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "aledb_import"

    def ready(self):
        from aledb_common.nav_registry import MAIN_SECTION, register_nav_item
        # Reference first: it is step one of the two-step import, and a bare .gd cannot be
        # imported into an experiment that has no reference.
        register_nav_item('Reference', url_name='reference_upload', section=MAIN_SECTION)
        register_nav_item('Import', url_name='gd_import', section=MAIN_SECTION)
