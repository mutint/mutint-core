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

        # The two kinds of stored data core owns per sample and will clear: the alignment
        # files, and breseq's HTML report. `sample.gd` and the reference are deliberately
        # not kinds -- the mutations are the data. See mutint_sample/storage.py.
        from mutint_common.storage_registry import register_storage_kind
        from mutint_sample import storage

        register_storage_kind(
            self, key=storage.ALIGNMENTS, label='Alignments and coverage',
            measure=storage.measure_alignments, clear=storage.clear_alignments,
            description="Each sample's aligned reads, their index and the coverage track. "
                        "The genome browser needs them; the mutation tables do not.")
        register_storage_kind(
            self, key=storage.REPORT, label='breseq HTML report',
            measure=storage.measure_report, clear=storage.clear_report,
            description="breseq's own report per sample, with the evidence behind each "
                        "call. Nothing else reads it.")
