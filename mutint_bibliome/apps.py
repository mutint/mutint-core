from django.apps import AppConfig


class BibliomeConfig(AppConfig):
    name = "mutint_bibliome"

    def ready(self):
        from mutint_common.context_registry import register_experiment_context_provider
        from mutint_bibliome.models import Publication

        @register_experiment_context_provider
        def provide_publications(experiment):
            return {'pub_qryset': Publication.objects.filter(experiment=experiment)}
