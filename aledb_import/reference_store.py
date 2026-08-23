"""Establish and verify an experiment's reference genome.

One code path for both ways a reference arrives:

* an explicit upload -- a GenBank, GFF3, or FASTA dropped on the Add page; and
* a breseq folder, which carries ``data/reference.gff3`` and ``data/reference.fasta``.

Both are normalized first (see ``reference.normalize_reference``), so the stored artifacts
and the hashes that guard "all samples share a reference" are computed on one canonical
form. Hashing the uploads as-is would make a GenBank and the GFF3 breseq derived from it
look like different references when they are the same genome.
"""

import hashlib
import os

from aledb_common import store
from aledb_import import reference as reference_io
from aledb_seq.models import ExperimentReference


class ReferenceMismatch(Exception):
    """The supplied reference is not the one this experiment already uses."""


def normalized_fasta_text(sequences):
    return reference_io.render_fasta(sequences)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def known_seq_ids(experiment):
    """Every name the experiment's reference answers to, or None if it has none.

    Names are compared exactly. breseq trims the version suffix when matching a GD's
    seq_id to a contig -- REL606 and REL606.6 become the same thing -- and ALEdb
    deliberately does not: it holds many experiments side by side, and two of them
    may legitimately be against different versions of the same accession. Treating
    those as one reference would attribute a mutation to the wrong genome, which is
    a quieter and worse failure than refusing the import.
    """
    reference = ExperimentReference.objects.filter(ale_experiment=experiment).first()
    if reference is None:
        return None
    return {entry["id"] for entry in (reference.seq_ids or []) if entry.get("id")}


def establish_or_check(experiment, gff3_text, sequences, replace=False,
                       update_annotation=False):
    """Create the experiment's reference, or verify a later one is the same reference.

    Sameness is decided on the **sequence** alone -- see
    ``ExperimentReference.matches_sequence``. When the sequence matches but the annotation
    differs, ``update_annotation`` decides what happens: an explicit reference or
    ``replace_annotation`` upload refreshes the stored GFF3, while a breseq folder import
    leaves the experiment's existing annotation alone rather than letting import order
    decide it.

    Returns ``(ExperimentReference, created)``. Raises ReferenceMismatch when the sequence
    differs and ``replace`` is not set.
    """
    fasta_text = normalized_fasta_text(sequences)
    gff3_sha = digest(gff3_text)
    fasta_sha = digest(fasta_text)

    existing = ExperimentReference.objects.filter(ale_experiment=experiment).first()
    if existing is not None and not replace:
        if not existing.matches_sequence(fasta_sha):
            raise ReferenceMismatch(
                "reference sequence does not match the rest of the experiment "
                "(%s... vs %s...)" % (fasta_sha[:12], existing.fasta_sha256[:12]))
        if existing.gff3_sha256 == gff3_sha or not update_annotation:
            return existing, False
        # Same genome, better (or just different) annotation, and the caller asked for it.
        _write_store(experiment.ale_id, gff3_text, fasta_text)
        existing.gff3_sha256 = gff3_sha
        existing.save(update_fields=["gff3_sha256"])
        return existing, False

    _write_store(experiment.ale_id, gff3_text, fasta_text)

    defaults = {
        "gff3_sha256": gff3_sha,
        "fasta_sha256": fasta_sha,
        "seq_ids": [{"id": seq_id, "length": len(seq)} for seq_id, seq in sequences],
        "total_length": sum(len(seq) for _seq_id, seq in sequences),
    }
    reference, created = ExperimentReference.objects.update_or_create(
        ale_experiment=experiment, defaults=defaults)
    return reference, created


def _write_store(ale_experiment_id, gff3_text, fasta_text):
    directory = store.ensure_dir(store.experiment_reference_dir(ale_experiment_id))
    gff3_path = store.experiment_reference_path(ale_experiment_id, store.REFERENCE_GFF3)
    fasta_path = store.experiment_reference_path(ale_experiment_id, store.REFERENCE_FASTA)

    with open(gff3_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(gff3_text)
    with open(fasta_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(fasta_text)
    reference_io.write_fai(
        fasta_path, store.experiment_reference_path(ale_experiment_id, store.REFERENCE_FAI))
    return directory


def annotation_reference_path(ale_experiment_id):
    """The stored reference to annotate against, or None if there is none.

    This is the same canonical GFF3 the store hashes and igv.js draws. It is in
    breseq's own dialect precisely so it can serve all three (see
    ``aledb_import.reference.normalize_reference``).
    """
    path = store.experiment_reference_path(ale_experiment_id, store.REFERENCE_GFF3)
    return path if os.path.isfile(path) else None


def has_reference(experiment):
    return ExperimentReference.objects.filter(ale_experiment=experiment).exists()
