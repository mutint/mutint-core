from django.apps import AppConfig


class ImportConfig(AppConfig):
    name = "mutint_import"

    def ready(self):
        from mutint_import.handlers import register_core_import_handlers
        register_core_import_handlers()

    # No nav entry. Adding data happens inside an experiment -- the button on the
    # experiment's own page (stats.html) is the way in -- so a sidebar link could only ever
    # land on /import/add/ with no experiment to add to.
