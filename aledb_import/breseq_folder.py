"""Import a staged tree of breseq result folders.

A sample is any directory containing ``data/output.gd``. Everything this importer reads
lives in that ``data/`` folder -- the reference and the alignment beside the calls made
against them -- which is what lets this path store a BAM and validate the reference,
neither of which a bare ``.gd`` upload can do.

Per sample::

    <sample>/data/output.gd             parsed for mutations
    <sample>/data/reference.gff3        the reference, hashed
    <sample>/data/reference.fasta       the reference sequence, hashed
    <sample>/data/reference.bam         copied into the store
    <sample>/data/reference.bam.bai     copied into the store

``output/`` is not consulted. A stock breseq run writes its .gd there, but the trees being
imported are curated ones that keep only ``data/``.

Every sample in an experiment must agree on the reference. The first one establishes it;
a later mismatch rejects that sample and the batch continues, matching how the bare-.gd
importer already isolates per-file failures.

``annotated.gd`` is no longer read. It existed because the importer took ``gene_name`` /
``gene_product`` straight out of the file and only ``gdtools ANNOTATE`` put them there;
annotation is derived from the stored reference now (``aledb_import.annotation``), so
breseq's own ``output.gd`` is enough and the extra gdtools step is not needed.
"""

import logging
import os
import shutil

from django.db import transaction

from aledb_common import store
from aledb_import import coverage
from aledb_import import reference as reference_io
from aledb_import.breseq_summary import read_breseq_summary
from aledb_import import reference_store
from aledb_import.gd_import import (
    _parse_document,
    _prepare_experiment,
    run_post_processing,
    import_document_as_sample,
)

logger = logging.getLogger("aledb_import.breseq_folder")

# One path, not a preference list. Everything this importer reads lives in data/ --
# the .gd beside the reference it was called against, the alignment, and the summary --
# so output/ is not consulted at all. annotated.gd is gone with it: it only ever existed
# because the importer needed gdtools ANNOTATE to have written gene_name/gene_product
# into the file, and annotation is derived from the stored reference now.
GD_RELATIVE_PATH = os.path.join("data", "output.gd")
GFF3_RELATIVE_PATH = os.path.join("data", "reference.gff3")
FASTA_RELATIVE_PATH = os.path.join("data", "reference.fasta")
BAM_RELATIVE_PATH = os.path.join("data", "reference.bam")
BAI_RELATIVE_PATH = os.path.join("data", "reference.bam.bai")

# What the client is asked to upload per sample. Everything else a breseq run writes
# (01_sequence_conversion/ ... 08_mutation_identification/) is unread by this codebase and
# is often the bulk of the run, so it never leaves the user's machine.
SAMPLE_FILES = (
    GD_RELATIVE_PATH,
    GFF3_RELATIVE_PATH,
    FASTA_RELATIVE_PATH,
    BAM_RELATIVE_PATH,
    BAI_RELATIVE_PATH,
)


class SampleError(Exception):
    """A problem with one sample. Rejects that sample; the batch continues."""


def find_gd_file(sample_dir):
    """The sample's data/output.gd, or None if it has none."""
    candidate = os.path.join(sample_dir, GD_RELATIVE_PATH)
    return candidate if os.path.isfile(candidate) else None


def find_sample_dirs(root):
    """Return sample directories under ``root``, sorted, at any nesting depth.

    A dropped folder may be a single sample or a collection of them, so this walks rather
    than doing one os.listdir -- unlike ale_experiment._get_sample_report_list, which
    assumes exactly one level.
    """
    found = []
    for dirpath, dirnames, _filenames in os.walk(root):
        if find_gd_file(dirpath):
            found.append(dirpath)
            # Samples do not nest inside one another.
            dirnames[:] = []
    return sorted(found)


def find_loose_gd_files(root, sample_dirs):
    """Return ``.gd`` files under ``root`` that are not part of any sample folder.

    These cannot be imported here. Every sample in an experiment must hash-match a shared
    reference, and a bare ``.gd`` carries none -- importing one would put a sample into the
    experiment that the reference check can never apply to. They are reported as skipped so
    a stray file is visible rather than silently ignored.
    """
    prefixes = tuple(os.path.join(sample_dir, "") for sample_dir in sample_dirs)
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if dirpath in sample_dirs or dirpath.startswith(prefixes):
            continue
        for filename in filenames:
            if filename.lower().endswith(".gd"):
                found.append(os.path.join(dirpath, filename))
    return sorted(found)


def import_breseq_folders(root, project_name, experiment_name, person, is_public=False):
    """Name-addressed entry point, kept for the CLI and existing callers.

    Web callers use :func:`import_samples_into` instead, which takes the experiment itself --
    see ``gd_import.prepare_experiment_by_id`` for why identity by primary key matters.
    """
    context = _prepare_experiment(project_name, experiment_name, person, is_public)
    return _import_samples(context, root, person, report_loose_gd=True)


def import_samples_into(experiment, root, person):
    """Import every breseq sample under ``root`` into an existing experiment.

    Loose ``.gd`` files are left alone here: the import registry routes those to the
    genomediff handler, so claiming them would import the same file twice.
    """
    from aledb_import.gd_import import prepare_experiment_by_id

    context = prepare_experiment_by_id(experiment.ale_id)
    return _import_samples(context, root, person, report_loose_gd=False)


def _import_samples(context, root, person, report_loose_gd):
    experiment = context["experiment"]

    sample_dirs = find_sample_dirs(root)
    file_results = []
    total_mutations = 0

    for sample_dir in sample_dirs:
        sample_name = os.path.basename(sample_dir.rstrip(os.sep))
        try:
            with transaction.atomic():
                count, seq_experiment = _import_one_sample(
                    sample_dir, sample_name, context, person)
            # Outside the transaction on purpose: this walks the whole alignment, and holding
            # a database transaction open for it would be paid by every other writer. It is
            # also best-effort -- a sample keeps its reads whether or not the coverage
            # derives, and `./aledb coverage` fills in what did not.
            coverage.build_quietly(seq_experiment)
            file_results.append({"file": sample_name, "mutations": count, "error": None})
            total_mutations += count
        except Exception as exc:  # one bad sample must not poison the batch
            logger.exception("breseq folder import failed for %s", sample_name)
            file_results.append({"file": sample_name, "mutations": 0, "error": str(exc)})

    if report_loose_gd:
        for gd_path in find_loose_gd_files(root, sample_dirs):
            file_results.append({
                "file": os.path.basename(gd_path),
                "mutations": 0,
                "error": ("a bare .gd has no reference genome, which every sample in an "
                          "experiment must share; import it into an experiment whose "
                          "reference is already established"),
            })

    if total_mutations:
        run_post_processing(experiment)

    return {
        "experiment_id": experiment.ale_id,
        "experiment": experiment.name,
        "total_mutations": total_mutations,
        "files": file_results,
    }


def _import_one_sample(sample_dir, sample_name, context, person):
    experiment = context["experiment"]

    gd_path = find_gd_file(sample_dir)
    if gd_path is None:
        raise SampleError("%s has no %s" % (sample_name, GD_RELATIVE_PATH))
    gff3_path = _require(sample_dir, GFF3_RELATIVE_PATH, sample_name)
    fasta_path = _require(sample_dir, FASTA_RELATIVE_PATH, sample_name)
    bam_path = _require(sample_dir, BAM_RELATIVE_PATH, sample_name)
    # A .bai cannot be generated without samtools/pysam, and a BAM without one is useless
    # to a genome browser, so its absence is a rejection rather than a silent degradation.
    bai_path = _require(sample_dir, BAI_RELATIVE_PATH, sample_name)

    _establish_or_check_reference(experiment, gff3_path, fasta_path)

    with open(gd_path, "rb") as handle:
        document = _parse_document(handle)

    seq_experiment, count = import_document_as_sample(
        document, sample_name, context, person)

    sample_store = store.ensure_dir(store.sample_dir(seq_experiment.id))
    shutil.copyfile(gd_path, os.path.join(sample_store, store.SAMPLE_GD))
    shutil.copyfile(bam_path, os.path.join(sample_store, store.SAMPLE_BAM))
    shutil.copyfile(bai_path, os.path.join(sample_store, store.SAMPLE_BAI))

    seq_experiment.bam_stored = True
    updated = ["bam_stored"]

    # data/summary.json is optional -- a drop without it still imports, just with zeroed
    # statistics, which is what every web upload had before it was collected at all.
    statistics = read_breseq_summary(sample_dir)
    if statistics:
        for field, value in statistics.items():
            setattr(seq_experiment, field, value)
        updated.extend(statistics)

    seq_experiment.save(update_fields=updated)

    # The sample goes back with the count so the caller can derive from it once this
    # transaction has closed.
    return count, seq_experiment


def _establish_or_check_reference(experiment, gff3_path, fasta_path):
    """First sample defines the experiment's reference; later ones must match it.

    The sample's own two reference files are cross-checked against each other first, then
    normalized -- so this compares on the same canonical form as a reference uploaded
    through an explicit reference upload, rather than on breseq's raw output.
    """
    _validated_sequences(gff3_path, fasta_path)
    gff3_text, sequences = reference_io.normalize_reference(gff3_path, GFF3_RELATIVE_PATH)

    try:
        reference, _created = reference_store.establish_or_check(
            experiment, gff3_text, sequences)
    except reference_store.ReferenceMismatch as exc:
        raise SampleError(str(exc))
    return reference


def _validated_sequences(gff3_path, fasta_path):
    """Cross-check the GFF3's inline ##FASTA against data/reference.fasta.

    Compared sequence by sequence rather than byte by byte, since line wrapping and case
    legitimately differ between the two files.
    """
    with open(fasta_path, "r", encoding="utf-8", errors="replace") as handle:
        fasta_sequences = list(reference_io.parse_fasta(handle))
    if not fasta_sequences:
        raise SampleError("data/reference.fasta contains no sequences")

    gff3_sequences = reference_io.read_gff3(gff3_path)["sequences"]
    if not gff3_sequences:
        # breseq always inlines the sequence; its absence means this is not a breseq
        # reference, so there is nothing to cross-check against.
        raise SampleError("data/reference.gff3 has no ##FASTA section")

    fasta_map = {seq_id: seq.upper() for seq_id, seq in fasta_sequences}
    gff3_map = {seq_id: seq.upper() for seq_id, seq in gff3_sequences}

    if set(fasta_map) != set(gff3_map):
        raise SampleError(
            "reference.gff3 and reference.fasta disagree on sequences: %s vs %s"
            % (sorted(gff3_map), sorted(fasta_map)))
    for seq_id, sequence in fasta_map.items():
        if gff3_map[seq_id] != sequence:
            raise SampleError(
                "reference.gff3 and reference.fasta disagree on the sequence of %s" % seq_id)

    return fasta_sequences


def _require(sample_dir, relative_path, sample_name):
    path = os.path.join(sample_dir, relative_path)
    if not os.path.isfile(path):
        raise SampleError("%s is missing %s" % (sample_name, relative_path))
    return path
