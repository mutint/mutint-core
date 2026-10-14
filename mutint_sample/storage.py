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

import logging
import os
import shutil

from mutint_common import store
from mutint_common.storage_registry import directory_bytes, file_bytes, request_remeasure
from mutint_experiment import paths

logger = logging.getLogger(__name__)

ALIGNMENTS = 'alignments'
REPORT = 'report'

_ALIGNMENT_FILES = (store.SAMPLE_BAM, store.SAMPLE_BAI, store.SAMPLE_BIGWIG)


def _samples(experiment):
    from mutint_sample.models import Sample

    return Sample.objects.filter(**{paths.to_experiment(): experiment}).only(
        'id', 'bam_stored', 'coverage_stored', 'report_stored', 'check_report_stored')


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


#: The flag each report kind sets; None is the import's own report.
_REPORT_FLAGS = {None: 'report_stored', 'check': 'check_report_stored'}


def measure_report(experiment):
    """Both reports a sample may carry: the import's, and a curation check's."""
    return sum(directory_bytes(store.sample_report_dir(sample.id, kind))
               for sample in _samples(experiment) for kind in _REPORT_FLAGS)


def clear_report(experiment):
    for sample in _samples(experiment):
        cleared = []
        for kind, flag in _REPORT_FLAGS.items():
            shutil.rmtree(store.sample_report_dir(sample.id, kind), ignore_errors=True)
            if getattr(sample, flag):
                setattr(sample, flag, False)
                cleared.append(flag)
        if cleared:
            sample.save(update_fields=cleared)


def store_report(sample, source, kind=None):
    """Copy a breseq `output/` tree into the sample's store as its report. Whether it was.

    **Best-effort.** A sample keeps its mutations whether or not its report stores -- the
    posture coverage takes, and for the same reason: the mutations are the data and the report
    is a way of looking at them. A folder assembled by hand from a `data/` directory has no
    `output/` at all and imports perfectly without one.

    Copied whole rather than file by file. What breseq writes in there is breseq's business:
    0.50 writes one self-contained `evidence.html` with the whole evidence tree zipped inside
    it, older releases write an `evidence/` directory of pages and PNGs, and a rule here naming
    either would quietly drop the other.

    The one writer for both kinds, so the importer and a plugin storing a check's report
    (`kind="check"`) cannot disagree about where one goes or what it sets. Sets the kind's
    flag on `sample` without saving -- the importer batches that write with its own -- and
    marks the experiment's storage stale, since a tree just landed.
    """
    if not os.path.isdir(source):
        return False

    destination = store.sample_report_dir(sample.pk, kind)
    try:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)
    except (OSError, shutil.Error):
        logger.warning("could not store the breseq report (%s) for sample %s",
                       kind or 'import', sample.pk, exc_info=True)
        return False
    setattr(sample, _REPORT_FLAGS[kind], True)
    request_remeasure(sample.experiment.id, 'report stored')
    return True
