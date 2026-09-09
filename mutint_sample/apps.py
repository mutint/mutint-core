from django.apps import AppConfig


class SampleConfig(AppConfig):
    name = "mutint_sample"

    def ready(self):
        from mutint_common.nav_registry import (
            EXPERIMENT_SECTION, register_nav_item,
        )
        # First, because entries appear in the order they are registered within one app, and
        # this is what somebody opening an experiment came to see. The reference below it
        # used to lead, on the reasoning that an experiment reads top-down from what it was
        # aligned to -- true of how the data is built and not of how it is read.
        register_nav_item('Mutations', url='/mutations/breseq',
                          section=EXPERIMENT_SECTION)
        # The only page that says what genome this experiment is called against, and the only
        # place an NCBI accession can be recorded -- which is why it needs a nav entry at all
        # rather than a link that appears once an accession has been.
        #
        # 'Compare' used to be registered here beside these. It is the mutint-compare
        # plugin's now, and registers itself -- which is what lets a deployment leave it
        # out. Because plugins load after every core app, its entry lands after these rather
        # than immediately beside them.
        register_nav_item('Reference', url='/mutations/reference',
                          section=EXPERIMENT_SECTION)
