from django.apps import AppConfig


class BibliomeConfig(AppConfig):
    name = "aledb_bibliome"

    def ready(self):
        from aledb_common.context_registry import register_experiment_context_provider
        from aledb_bibliome.models import Publication

        @register_experiment_context_provider
        def provide_publications(experiment):
            return {'pub_qryset': Publication.objects.filter(ale_experiment=experiment)}
