from django.apps import AppConfig


class SampleConfig(AppConfig):
    name = "mutint_sample"

    def ready(self):
        from mutint_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        # First, because entries appear in the order they are registered within one app,
        # and the reference is what everything below it is called against -- an experiment
        # reads top-down from what it was aligned to, then what was found. It is also the
        # only page that says what genome that is, and the only place an NCBI accession
        # can be recorded, which is why it needs a nav entry at all rather than being
        # reached from a link that only appears once an accession has been recorded.
        register_nav_item('Reference Sequence', url='/mutations/reference',
                          section=EXPERIMENT_SECTION)
        # 'Compare' used to be registered here beside this. It is the mutint-compare
        # plugin's now, and registers itself -- which is what lets a deployment leave it
        # out. Because plugins load after every core app, its entry lands after this one
        # rather than immediately beside it.
        register_nav_item('Mutations', url='/mutations/breseq',
                          section=EXPERIMENT_SECTION)
