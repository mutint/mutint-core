"""Renaming an experiment's contigs, when the same genome arrives under different names.

A reference's identity is its bases (`reference.sequence_set_digest`). Names are a label
whoever prepared the file chose, and breseq's `REL606` and a RefSeq `NC_012967.1` can be the
same genome. So an upload that matches on sequence but differs on names is not a rejection --
it is a rename, and this module plans and applies it.

Two things make it more than a column update.

**The name is persisted in more places than the obvious one.** `Mutation.reseq_reference` is
the visible one, but `to_gd_line()` reads the name out of the verbatim `gd_data` instead, and
`gd_data` embeds names in more keys than `seq_id`: `region` on CON/INT and `mob_region` on an
annotated MOB are both `seq:start-end`. `Mutation.sequence_change` duplicates the region
string, and `UnassignedMissingCoverageEvidence` has its own `seq_id`. Rewriting by *value*
rather than by a list of keys is what covers all of them, including the ones breseq has not
invented yet.

**The stored alignments keep their old names.** A BAM's `@SQ` header and a BigWig's
chromosome B-tree are baked in, and rewriting them means samtools and, for a real experiment,
gigabytes of I/O. Instead the old names are recorded as `aliases` on the sequence and served
as an igv chromosome alias table -- verified against a real BAM, where a renamed reference
drew pixel-for-pixel what the original did.
"""

import logging

from django.db import transaction

from aledb_common.plugin_registry import run_sequence_rename_hooks
from aledb_import import reference as reference_io
from aledb_experiment import paths

logger = logging.getLogger(__name__)

# `Mutation.reseq_reference` is CharField(max_length=200).
MAX_NAME_LENGTH = 200

# A contig name is written into tab-separated files (.gd, the SAM header, the alias table)
# and into `seq:start-end` region values, so these characters cannot appear in one.
FORBIDDEN_CHARACTERS = ("\t", "\n", ":", " ")

BATCH = 1000


class RenameError(Exception):
    """Base for every reason a rename cannot be planned."""


class SequenceSetMismatch(RenameError):
    """Not the same genome -- the sequences themselves differ."""


class RenameAmbiguous(RenameError):
    """Byte-identical duplicate contigs; which maps to which cannot be known."""


class RenameUnavailable(RenameError):
    """The stored reference has no per-sequence hashes to match names on."""


class InvalidSequenceName(RenameError):
    """A proposed name cannot be stored or round-tripped."""


class RenamePlan:
    """What a rename would do. Inert until `apply_rename` is called with it."""

    def __init__(self, pairs, unchanged, sequences):
        self.pairs = pairs              # [(old, new)], only names that actually change
        self.unchanged = unchanged      # names that stay as they are
        self.sequences = sequences      # the uploaded [(seq_id, sequence)] -- new names

    def __bool__(self):
        return bool(self.pairs)

    @property
    def mapping(self):
        return dict(self.pairs)

    def as_payload(self, counts=None):
        """JSON-safe description, for the confirmation prompt."""
        lengths = {seq_id: len(seq) for seq_id, seq in self.sequences}
        return {
            "renames": [{"from": old, "to": new, "length": lengths.get(new)}
                        for old, new in self.pairs],
            "unchanged": list(self.unchanged),
            "counts": counts or {},
        }


def plan_rename(reference, sequences):
    """`RenamePlan` for re-establishing `reference` as `sequences`.

    Assumes the two are already known to be the same genome; raises if they are not, so it
    is safe to call without checking first.
    """
    stored = _stored_hashes(reference)
    incoming = [(seq_id, reference_io.sequence_digest(seq)) for seq_id, seq in sequences]

    _check_sets(reference, sequences, stored, incoming)
    pairs, unchanged = _pair_up(stored, incoming)
    for _old, new in pairs:
        _validate_name(new)
    _check_bijection(pairs)
    return RenamePlan(pairs, unchanged, list(sequences))


def apply_rename(experiment, reference, plan, actor=""):
    """Rewrite every name this experiment persists, then tell the plugins.

    The database work is one transaction. The reference *files* are written by the caller
    (`reference_store`) afterwards, deliberately: a rollback cannot unwrite a file, so files
    must never lead the rows.
    """
    from aledb_seq.models import Mutation, UnassignedMissingCoverageEvidence

    mapping = plan.mapping
    if not mapping:
        return {}

    counts = {"mutations": 0, "evidence": 0}
    with transaction.atomic():
        counts["mutations"] = _rename_mutations(Mutation, experiment, mapping)
        counts["evidence"] = _rename_evidence(
            UnassignedMissingCoverageEvidence, experiment, mapping)
        _record_aliases(reference, plan)

    # Everything below is after the commit, and in this order on purpose.
    #
    # The annotation cache is keyed by reference directory and now holds a genome under names
    # nothing carries any more, so it goes first. Re-annotation then matters because
    # `annotate_records_with` filters on `record['seq_id']` -- with a stale name it matches
    # nothing and silently annotates none of them.
    _reannotate(experiment)

    # Hooks after core has finished, so a plugin rebuilding from the mutations sees the
    # finished state. A hook must never see names a rollback could take away, and one that
    # fails must not undo a rename that is already true -- `run_sequence_rename_hooks`
    # isolates each.
    run_sequence_rename_hooks(experiment.ale_id, mapping)
    logger.info("renamed %d sequence(s) on experiment %s: %s",
                len(mapping), experiment.ale_id,
                ", ".join("%s -> %s" % pair for pair in plan.pairs))
    return counts


def _reannotate(experiment):
    """Re-derive annotation against the renamed reference, best-effort.

    Best-effort because the rename is already committed and correct: annotation is derived
    data that `./aledb reannotate` can rebuild, so failing here must not present as the
    rename having failed.
    """
    from aledb_import import annotation

    annotation.clear_cache()
    try:
        annotation.reannotate_experiment(experiment)
    except Exception:  # noqa: BLE001
        logger.exception(
            "re-annotation after renaming experiment %s failed; run `./aledb reannotate %s`",
            experiment.ale_id, experiment.ale_id)


def describe_difference(reference, sequences):
    """Why `sequences` is not this experiment's genome, in terms of sequences not hashes.

    Named by whichever side has a name for it -- under sequence identity the names carry no
    weight, but they are what the person uploading recognises.
    """
    stored = _stored_hashes(reference)
    incoming = [(seq_id, reference_io.sequence_digest(seq)) for seq_id, seq in sequences]
    lengths = dict(_lengths(reference), **{seq_id: len(seq) for seq_id, seq in sequences})

    stored_counts = _counts([h for _name, h in stored])
    incoming_counts = _counts([h for _name, h in incoming])
    stored_names = _names_by_hash(stored)
    incoming_names = _names_by_hash(incoming)

    lines = ["The uploaded reference is not the same set of sequences as this experiment's."]
    only_here, only_there, differing = [], [], []
    for digest in sorted(set(stored_counts) | set(incoming_counts)):
        here, there = stored_counts.get(digest, 0), incoming_counts.get(digest, 0)
        if here and not there:
            only_here.append(_describe_one(stored_names[digest], digest, lengths))
        elif there and not here:
            only_there.append(_describe_one(incoming_names[digest], digest, lengths))
        elif here != there:
            differing.append("      %s: this experiment has %d, the upload has %d"
                             % (stored_names[digest], here, there))

    if only_here:
        lines.append("\n  in this experiment but not in the upload:")
        lines.extend(only_here)
    if only_there:
        lines.append("\n  in the upload but not in this experiment:")
        lines.extend(only_there)
    if differing:
        lines.append("\n  present on both sides a different number of times:")
        lines.extend(differing)

    lines.append(
        "\nSequences are compared base by base, ignoring names, order and annotation, so a"
        "\nsingle differing base is a different genome and belongs in its own experiment.")
    return "\n".join(lines)


# --- planning internals -----------------------------------------------------------------


def _stored_hashes(reference):
    entries = reference.seq_ids or []
    missing = [entry for entry in entries if not entry.get("sha256")]
    if missing or not entries:
        raise RenameUnavailable(
            "This experiment's reference was recorded before per-sequence hashes existed, "
            "so its contigs cannot be matched to the uploaded ones by sequence. Run "
            "`./aledb rename_contigs %s --repair` to recompute them from the stored FASTA "
            "first." % (reference.ale_experiment_id,))
    return [(entry["id"], entry["sha256"]) for entry in entries]


def _lengths(reference):
    return {entry["id"]: entry.get("length") for entry in (reference.seq_ids or [])}


def _counts(digests):
    counts = {}
    for digest in digests:
        counts[digest] = counts.get(digest, 0) + 1
    return counts


def _names_by_hash(pairs):
    names = {}
    for name, digest in pairs:
        names.setdefault(digest, name)
    return names


def _describe_one(name, digest, lengths):
    length = lengths.get(name)
    return "      %-24s %s   (%s…)" % (
        name, ("%s bp" % format(length, ",")) if length else "unknown length", digest[:8])


def _check_sets(reference, sequences, stored, incoming):
    if _counts([h for _n, h in stored]) != _counts([h for _n, h in incoming]):
        raise SequenceSetMismatch(describe_difference(reference, sequences))


def _pair_up(stored, incoming):
    """`([(old, new)], [unchanged])`, matching sequences by hash.

    A hash occurring once on each side pairs unambiguously. A hash occurring more than once
    -- byte-identical duplicate contigs -- pairs only by identical name; anything left over
    is refused rather than guessed, because the pairings are indistinguishable to the hash
    and entirely distinguishable to the mutations sitting on them.
    """
    by_hash = {}
    for name, digest in incoming:
        by_hash.setdefault(digest, []).append(name)

    pairs, unchanged, leftovers = [], [], []
    for name, digest in stored:
        candidates = by_hash.get(digest, [])
        if name in candidates:
            candidates.remove(name)
            unchanged.append(name)
        elif len(candidates) == 1:
            pairs.append((name, candidates.pop()))
        else:
            leftovers.append((name, digest))

    if leftovers:
        names = ", ".join(name for name, _digest in leftovers)
        raise RenameAmbiguous(
            "This genome carries byte-identical sequences under more than one name (%s), so "
            "which uploaded name replaces which cannot be determined from the sequences "
            "alone. Rename them to match the upload one at a time, or upload a reference "
            "that keeps their current names." % names)
    return pairs, unchanged


def _validate_name(name):
    if not name:
        raise InvalidSequenceName("A sequence name cannot be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidSequenceName(
            "The sequence name %r is %d characters; the limit is %d."
            % (name[:40] + "…", len(name), MAX_NAME_LENGTH))
    for character in FORBIDDEN_CHARACTERS:
        if character in name:
            raise InvalidSequenceName(
                "The sequence name %r contains %r. Contig names are written into "
                "tab-separated files and into `sequence:start-end` region values, so they "
                "cannot contain tabs, newlines, spaces or colons." % (name, character))


def _check_bijection(pairs):
    """No two old names may become one new name.

    It cannot happen -- pairing is one-to-one over distinct sequences -- but it is the
    assumption that makes the `Mutation` dedup key safe under a rename, so it is asserted
    rather than left implicit.
    """
    new_names = [new for _old, new in pairs]
    if len(set(new_names)) != len(new_names):
        raise RenameAmbiguous("Two sequences would end up with the same name.")


# --- applying internals -----------------------------------------------------------------


def rewrite_value(value, mapping):
    """`value` with a leading sequence name replaced, or unchanged.

    Covers a bare name (`gd_data["seq_id"]`) and a `seq:start-end` region (`region`,
    `mob_region`, and the `sequence_change` copy of them) with one rule. Region values are
    split on the *first* colon, which is safe because `_validate_name` refuses a name
    containing one.
    """
    if not isinstance(value, str):
        return value
    if value in mapping:
        return mapping[value]
    head, sep, tail = value.partition(":")
    if sep and head in mapping:
        return "%s:%s" % (mapping[head], tail)
    return value


def rewrite_gd_data(gd_data, mapping):
    """Every string value of a verbatim GenomeDiff record, rewritten by value.

    By value rather than by a list of keys on purpose. `seq_id` is not the only place a name
    appears: CON and INT carry `region`, an annotated MOB carries `mob_region`, and `gd_data`
    is contractually verbatim, so whatever breseq writes next is covered too. Rewriting by
    key would leave those stale and `to_gd_line()` would emit a `.gd` that `gdtools APPLY`
    resolves against a contig the reference no longer has.
    """
    if not gd_data:
        return gd_data, False
    updated = dict(gd_data)
    changed = False
    for key, value in updated.items():
        new_value = rewrite_value(value, mapping)
        if new_value != value:
            updated[key] = new_value
            changed = True
    return updated, changed


def _rename_mutations(Mutation, experiment, mapping):
    """Rewrite in memory, write once.

    Computed entirely from the original values before anything is written, which is what
    makes a *swap* (A→B, B→A) land correctly. Two sequential `UPDATE ... SET
    reseq_reference` statements per pair would collapse one -- and `Mutation` has no unique
    constraint, so the result would be silent duplicates rather than an IntegrityError.
    """
    queryset = Mutation.objects.filter(ale_experiment=experiment).only(
        "id", "reseq_reference", "gd_data", "sequence_change")
    batch, total = [], 0
    for mutation in queryset.iterator(chunk_size=BATCH):
        new_reference = mapping.get(mutation.reseq_reference, mutation.reseq_reference)
        new_gd_data, gd_changed = rewrite_gd_data(mutation.gd_data, mapping)
        new_change = rewrite_value(mutation.sequence_change, mapping)
        if (new_reference == mutation.reseq_reference and not gd_changed
                and new_change == mutation.sequence_change):
            continue
        mutation.reseq_reference = new_reference
        mutation.gd_data = new_gd_data
        mutation.sequence_change = new_change
        batch.append(mutation)
        if len(batch) >= BATCH:
            Mutation.objects.bulk_update(
                batch, ["reseq_reference", "gd_data", "sequence_change"])
            total += len(batch)
            batch = []
    if batch:
        Mutation.objects.bulk_update(batch, ["reseq_reference", "gd_data", "sequence_change"])
        total += len(batch)
    return total


def _rename_evidence(Evidence, experiment, mapping):
    """Missing-coverage evidence, rewritten the same way and for the same swap reason."""
    queryset = Evidence.objects.filter(
        **{paths.to_experiment(paths.FROM_OBSERVATION): experiment},
        seq_id__in=list(mapping)).only("id", "seq_id")
    batch, total = [], 0
    for row in queryset.iterator(chunk_size=BATCH):
        row.seq_id = mapping[row.seq_id]
        batch.append(row)
        if len(batch) >= BATCH:
            Evidence.objects.bulk_update(batch, ["seq_id"])
            total += len(batch)
            batch = []
    if batch:
        Evidence.objects.bulk_update(batch, ["seq_id"])
        total += len(batch)
    return total


def _record_aliases(reference, plan):
    """Carry each sequence's former names forward onto its new entry.

    Accumulated rather than replaced: a sequence renamed twice must keep the first name too,
    or a BAM stored before either rename stops resolving.
    """
    previous = {entry["id"]: list(entry.get("aliases") or [])
                for entry in (reference.seq_ids or [])}
    mapping = plan.mapping

    entries = reference_io.sequence_entries(plan.sequences)
    for entry in entries:
        old_name = next((old for old, new in plan.pairs if new == entry["id"]), entry["id"])
        aliases = list(previous.get(old_name, []))
        if old_name != entry["id"] and old_name not in aliases:
            aliases.append(old_name)
        if aliases:
            entry["aliases"] = aliases

    reference.seq_ids = entries
    reference.total_length = sum(len(seq) for _seq_id, seq in plan.sequences)
    reference.sequence_sha256 = reference_io.sequence_set_digest(plan.sequences)
    reference.save(update_fields=["seq_ids", "total_length", "sequence_sha256"])
    return mapping


def has_annotation(gff3_text):
    """Whether a normalized GFF3 carries features, or is only sequence.

    A bare FASTA normalizes to `##sequence-region` lines plus `##FASTA` and nothing else. It
    is a legitimate thing to upload -- it is how contigs get renamed without touching
    annotation -- but it must never be installed *as* annotation, or a rename by FASTA would
    silently replace a gene table with nothing.
    """
    for line in gff3_text.splitlines():
        if line.startswith("##FASTA"):
            break
        if line and not line.startswith("#"):
            return True
    return False


def rename_gff3_text(gff3_text, mapping):
    """The same annotation, with its sequence names replaced.

    Used when a rename arrives as a bare FASTA: the experiment's existing annotation has to
    be carried across to the new names rather than replaced by the sequence-only GFF3 the
    upload normalizes to.

    Safe as a line-wise transform because the text is machine-generated by
    `annotate.gff3.render_breseq_gff3`: tab-separated, seqid in column 1, `##sequence-region`
    in column 2, and `>seq_id` headers after `##FASTA`. Only those three positions are
    touched -- never a free-text attribute, which may legitimately mention the old name.
    """
    out, in_fasta = [], False
    for line in gff3_text.splitlines():
        if line.startswith("##FASTA"):
            in_fasta = True
            out.append(line)
        elif in_fasta and line.startswith(">"):
            name = line[1:].split()[0] if line[1:].split() else ""
            out.append(">%s" % mapping.get(name, name))
        elif line.startswith("##sequence-region"):
            parts = line.split("\t")
            if len(parts) > 1:
                parts[1] = mapping.get(parts[1], parts[1])
            out.append("\t".join(parts))
        elif line and not line.startswith("#") and not in_fasta:
            parts = line.split("\t")
            parts[0] = mapping.get(parts[0], parts[0])
            out.append("\t".join(parts))
        else:
            out.append(line)
    return "\n".join(out) + "\n"
