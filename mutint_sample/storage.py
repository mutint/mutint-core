"""The two kinds of stored data core keeps per sample, measured and cleared.

Registered with `mutint_common.storage_registry` from `mutint_sample.apps`. Both walk the
filesystem **indexed by the experiment's `Sample` rows** -- never the flags, which say a file
was stored and not that it still is, and never a listing of the store, which would count
directories nothing points at (those are the dashboard's unattributed line).

**Alignments are one unit: the BAM, its index and the coverage BigWig.** The BigWig is
derived from the BAM and cannot be rebuilt without it, so keeping it past the BAM would mean
a coverage track that can never be corrected, and clearing it alone saves nothing worth a
button. Clearing sets `bam_stored` and `coverage_stored` false *together*: `./mutint
coverage` selects `bam_stored=True` samples with no BigWig, and one flag without the other
would have it try to derive coverage from a file that is gone, once per sample, every run.

**`sample.gd` and the reference are not kinds.** The mutations were read from the one and
annotated against the other; there is nothing to be "cleared" about the data of record.
"""

import os
import shutil

from mutint_common import store
from mutint_common.storage_registry import directory_bytes, file_bytes
from mutint_experiment import paths

ALIGNMENTS = 'alignments'
REPORT = 'report'

_ALIGNMENT_FILES = (store.SAMPLE_BAM, store.SAMPLE_BAI, store.SAMPLE_BIGWIG)


def _samples(experiment):
    from mutint_sample.models import Sample

    return Sample.objects.filter(**{paths.to_experiment(): experiment}).only(
        'id', 'bam_stored', 'coverage_stored', 'report_stored')


def measure_alignments(experiment):
    return sum(file_bytes(store.sample_path(sample.id, name))
               for sample in _samples(experiment) for name in _ALIGNMENT_FILES)


def clear_alignments(experiment):
    for sample in _samples(experiment):
        for name in _ALIGNMENT_FILES:
            try:
                os.remove(store.sample_path(sample.id, name))
            except FileNotFoundError:
                pass
        if sample.bam_stored or sample.coverage_stored:
            sample.bam_stored = False
            sample.coverage_stored = False
            sample.save(update_fields=['bam_stored', 'coverage_stored'])


def measure_report(experiment):
    return sum(directory_bytes(store.sample_report_dir(sample.id))
               for sample in _samples(experiment))


def clear_report(experiment):
    for sample in _samples(experiment):
        shutil.rmtree(store.sample_report_dir(sample.id), ignore_errors=True)
        if sample.report_stored:
            sample.report_stored = False
            sample.save(update_fields=['report_stored'])
