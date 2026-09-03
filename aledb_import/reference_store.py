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
from aledb_import import reference_rename
from aledb_seq.models import ExperimentReference


class ReferenceMismatch(Exception):
    """The supplied reference is not the one this experiment already uses."""


class RenameRequired(Exception):
    """Same genome, different contig names -- and nobody has agreed to the rename yet.

    Deliberately not a subclass of ReferenceMismatch: every existing caller catches that one
    and reports "not this experiment's genome", which is the wrong answer here. A caller has
    to decide what to do about a rename on purpose.
    """

    def __init__(self, plan):
        self.plan = plan
        super().__init__(
            "The uploaded reference is this experiment's genome under different contig "
            "names (%s). Renaming rewrites every mutation in the experiment, so it needs "
            "to be confirmed."
            % ", ".join("%s -> %s" % pair for pair in plan.pairs))


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
                       update_annotation=False, allow_rename=False, actor=""):
    """Create the experiment's reference, or verify a later one is the same reference.

    Sameness is decided on the **sequence** alone -- the bases, independent of the names
    they carry and the order they are in; see ``ExperimentReference.matches_sequence``.
    Everything else is negotiable:

    * different annotation -- ``update_annotation`` decides. An explicit reference or
      ``replace_annotation`` upload refreshes the stored GFF3; a breseq folder import leaves
      the experiment's annotation alone rather than letting import order decide it.
    * different **names** -- ``allow_rename`` decides. Without it a rename raises
      ``RenameRequired``, which the caller turns into a confirmation prompt: renaming
      rewrites every mutation in the experiment and is not something to do as a side effect
      of an upload. Nothing that imports data passes it.

    Returns ``(ExperimentReference, created)``. Raises ReferenceMismatch when the sequence
    differs and ``replace`` is not set.
    """
    fasta_text = normalized_fasta_text(sequences)
    gff3_sha = digest(gff3_text)
    fasta_sha = digest(fasta_text)
    sequence_sha = reference_io.sequence_set_digest(sequences)

    existing = ExperimentReference.objects.filter(ale_experiment=experiment).first()
    if existing is not None and not replace:
        _ensure_sequence_identity(existing)
        if not existing.matches_sequence(sequence_sha, fasta_sha):
            raise ReferenceMismatch(
                reference_rename.describe_difference(existing, sequences))

        plan = reference_rename.plan_rename(existing, sequences)
        if plan:
            if not allow_rename:
                raise RenameRequired(plan)
            # A sequence-only upload -- a bare FASTA -- renames without bringing annotation
            # with it. Installing its feature-less GFF3 would replace the experiment's gene
            # table with nothing, so the stored annotation is carried across to the new names
            # instead. A GenBank or GFF3 already arrives annotated under the new names.
            new_gff3_text = gff3_text
            if not reference_rename.has_annotation(gff3_text):
                stored = _stored_gff3_text(experiment.id)
                if stored is not None:
                    new_gff3_text = reference_rename.rename_gff3_text(stored, plan.mapping)

            reference_rename.apply_rename(experiment, existing, plan, actor=actor)
            # Files after rows: a rollback cannot unwrite a file, so the store must never
            # lead the database.
            _write_store(experiment.id, new_gff3_text, fasta_text)
            existing.gff3_sha256 = digest(new_gff3_text)
            existing.fasta_sha256 = fasta_sha
            existing.save(update_fields=["gff3_sha256", "fasta_sha256"])
            return existing, False

        if existing.gff3_sha256 == gff3_sha:
            return existing, False
        # Sequence-only uploads never install annotation, whatever the caller asked for:
        # there is none in one to install.
        if not update_annotation or not reference_rename.has_annotation(gff3_text):
            # Still refresh identity: `seq_ids` and `sequence_sha256` can be stale on a row
            # written before they existed, and being stale is what this branch used to do.
            _apply_sequence_fields(existing, sequences, fasta_sha, sequence_sha)
            return existing, False
        # Same genome, same names, different annotation, and the caller asked for it.
        _write_store(experiment.id, gff3_text, fasta_text)
        existing.gff3_sha256 = gff3_sha
        _apply_sequence_fields(existing, sequences, fasta_sha, sequence_sha,
                               extra=["gff3_sha256"])
        return existing, False

    _write_store(experiment.id, gff3_text, fasta_text)

    defaults = dict(_sequence_fields(sequences, fasta_sha, sequence_sha),
                    gff3_sha256=gff3_sha)
    reference, created = ExperimentReference.objects.update_or_create(
        ale_experiment=experiment, defaults=defaults)
    return reference, created


def _sequence_fields(sequences, fasta_sha, sequence_sha):
    """Everything about a reference that the sequences alone determine.

    One definition, used by every path that writes it. It used to be inline in the
    create/replace branch only, so the same-sequence branch left `seq_ids` stale. That was
    invisible while identity included names -- a matching hash implied matching ids, lengths
    and order -- and stops being invisible the moment names can move.
    """
    return {
        "fasta_sha256": fasta_sha,
        "sequence_sha256": sequence_sha,
        "seq_ids": reference_io.sequence_entries(sequences),
        "total_length": sum(len(seq) for _seq_id, seq in sequences),
    }


def _apply_sequence_fields(reference, sequences, fasta_sha, sequence_sha, extra=()):
    """Write `_sequence_fields` onto `reference`, preserving any recorded aliases."""
    aliases = {entry["id"]: entry["aliases"]
               for entry in (reference.seq_ids or []) if entry.get("aliases")}
    fields = _sequence_fields(sequences, fasta_sha, sequence_sha)
    for entry in fields["seq_ids"]:
        if entry["id"] in aliases:
            entry["aliases"] = aliases[entry["id"]]
    for name, value in fields.items():
        setattr(reference, name, value)
    reference.save(update_fields=list(fields) + list(extra))


def _stored_gff3_text(ale_experiment_id):
    """The experiment's stored annotation, or None if it cannot be read."""
    path = store.experiment_reference_path(ale_experiment_id, store.REFERENCE_GFF3)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _ensure_sequence_identity(reference):
    """Fill in a reference's sequence identity from the store, if it is missing.

    A row backfilled while its files were absent has no `sequence_sha256` and no
    per-sequence hashes. If the files have since arrived, this heals it in place; if they
    have not, it stays blank and `matches_sequence` falls back to the old comparison.
    """
    if reference.sequence_sha256 and all(
            entry.get("sha256") for entry in (reference.seq_ids or [])):
        return
    path = store.experiment_reference_path(reference.ale_experiment_id, store.REFERENCE_FASTA)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            sequences = list(reference_io.parse_fasta(handle))
    except (OSError, UnicodeDecodeError):
        return
    if not sequences:
        return
    _apply_sequence_fields(reference, sequences,
                           digest(reference_io.render_fasta(sequences)),
                           reference_io.sequence_set_digest(sequences))


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
