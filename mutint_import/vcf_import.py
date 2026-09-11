"""Turning a parsed VCF into samples, through the path a `.gd` already takes.

`vcf.py` decides what a variant *is*; this decides where it goes. The split is the same one
`reference.py` and `reference_store.py` have, and for the same reason: the conversion rules are
worth testing without a database.

**Nothing here writes a Mutation.** The records go to `gd_import._database_gd_mutations`, which
is the single place mutations are stored -- so `synthesize_sequence_change`, the seven-field
`get_or_create`, the `"None"`-gene trap, annotation against the experiment's reference and
`_coerce_frequency` are all shared with the `.gd` path by construction rather than by care.
That is what makes "GenomeDiff is the anchor format" true rather than aspirational: a site
called by breseq and the same site called by GATK land on one row because one function decides.
"""

import logging

from django.conf import settings

from mutint_import import vcf
from mutint_import.annotation import reference_sequences_for
from mutint_sample import inputs

logger = logging.getLogger("mutint_import.vcf_import")

# `MutationCall.source` for a call that came from a VCF, beside "breseq" and "manual".
VCF_SOURCE = "vcf"

# Where the file's own header and each call's own line are kept.
VCF_RECORD = "vcf"


class VcfSample:
    """One sample's worth of a VCF: its records, and the lines they came from."""

    def __init__(self, name, records, lines, problems):
        self.name = name
        self.records = records      # GenomeDiff records, for _database_gd_mutations
        self.lines = lines          # the VcfLine each record came from, positionally
        self.problems = problems    # [str], reported as import warnings


def sample_names_for(document, filename):
    """What the samples in this file are called.

    A file with sample columns names them from its own headers; a sites-only file is one
    sample named from the filename, exactly as a bare `.gd` is. **Both then go through
    `import_document_as_sample`**, so there is still one rule for how a name becomes an ALE,
    a flask and an isolate, and one place that rule lives.
    """
    if document.sample_names:
        return list(document.sample_names)
    stem = filename
    for suffix in (".vcf.gz", ".vcf"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return [stem]


def convert(document, sample_name, experiment=None, filename=""):
    """The GenomeDiff records one sample calls, with the lines they came from.

    Every line that cannot become a mutation is reported rather than dropped: a symbolic
    allele, a REF that disagrees with the reference, a variant that changes nothing. The same
    posture `gd_import.parse_warnings` takes with a `.gd` line the parser could not read --
    the import succeeds and says what it could not use.
    """
    loaded = _reference_for(experiment)
    records, lines, problems = [], [], list(document.problems)
    column = sample_name if document.sample_names else None

    for line in document.lines:
        try:
            variants = vcf.variants_for(line, column)
        except vcf.LineProblem as problem:
            problems.append(_describe(line, problem))
            continue

        for variant in variants:
            try:
                _check_reference(loaded, line, variant)
                record = vcf.classify(variant)
            except vcf.LineProblem as problem:
                problems.append(_describe(line, problem))
                continue

            record = _infer_mob(record, variant, loaded)
            frequency = vcf.frequency_for(line, column, variant.allele_index)
            if frequency is not None:
                record["frequency"] = frequency

            records.append(record)
            lines.append(line)

    return VcfSample(sample_name, records, lines, problems)


def _describe(line, problem):
    return "%s:%s %s>%s -- %s" % (line.chrom, line.position, line.ref, line.alt, problem)


def _reference_for(experiment):
    """The experiment's loaded reference, or None.

    None is a real state rather than an error: it means the checks below cannot run, not that
    the import should stop. The handler already refuses a VCF into an experiment with no
    reference, so in practice this is only None in tests that do not need one.
    """
    if experiment is None:
        return None
    try:
        return reference_sequences_for(experiment)
    except Exception:
        logger.warning("could not load the reference for experiment %s",
                       getattr(experiment, "id", "?"), exc_info=True)
        return None


def _check_reference(loaded, line, variant):
    """Refuse a line whose REF is not what the reference actually holds.

    **The cheapest real safety this importer offers, and gdtools does not do it at all** --
    it reads REF for its length and nothing else, so a VCF called against a different assembly
    or with an off-by-one imports silently and wrongly, which is the failure nobody catches
    by looking at the result.

    Checked on the *untrimmed* line, because that is what the file asserted; a trimmed allele
    is our derivation and checking it would be checking our own arithmetic.
    """
    if loaded is None or not line.ref:
        return
    try:
        actual = loaded.get_sequence_1(line.chrom, line.position,
                                       line.position + len(line.ref) - 1)
    except Exception:
        # Off the end of the contig, or an unknown one. Both are worth reporting as what they
        # are rather than as a mismatch.
        raise vcf.LineProblem(
            "position %s is outside %s" % (line.position, line.chrom))

    if actual and actual.upper() != line.ref.upper():
        raise vcf.LineProblem(
            "REF says %s but the reference holds %s at %s:%s -- this VCF was probably called "
            "against a different genome" % (line.ref, actual, line.chrom, line.position))


def _infer_mob(record, variant, loaded):
    """Promote an INS to a MOB when the inserted sequence *is* an annotated repeat.

    Off by default (`MUTINT_VCF_INFER_MOB`), and the default is the honest one: **a wrong MOB
    is worse than an honest INS.** It asserts a mechanism the data may not support, and it
    lands in the same `get_or_create` key as everything else, so a mistake here does not merely
    mislabel a row -- it forks one mutation into two against every other import of the same
    call.

    So it stays silent unless the answer is unambiguous: exactly one annotated repeat family
    matches the inserted sequence end to end on one strand. Two candidates, a partial match, or
    a reference with no annotated repeats all leave it an INS. What it matched is recorded on
    the record, so a reader can see why -- and `duplication_size` is *measured* from the
    flanking sequence rather than assumed, because assuming it is how a plausible-looking MOB
    ends up describing a duplication that is not there.
    """
    if record["type"] != "INS" or loaded is None:
        return record
    if not getattr(settings, "MUTINT_VCF_INFER_MOB", False):
        return record

    inserted = record["new_seq"]
    matches = _matching_repeats(loaded, variant.seq_id, inserted)
    if len(matches) != 1:
        return record

    name, strand = matches[0]
    duplication = _duplication_size(loaded, variant.seq_id, record["position"], inserted)
    return {
        "type": "MOB", "seq_id": record["seq_id"], "position": record["position"],
        "repeat_name": name, "strand": strand, "duplication_size": duplication,
    }


def _matching_repeats(loaded, seq_id, inserted):
    """[(repeat_name, strand)] for repeat families this insertion matches end to end."""
    found = []
    for name, sequence, strand in _annotated_repeats(loaded, seq_id):
        if len(sequence) != len(inserted):
            continue
        if sequence.upper() == inserted.upper():
            found.append((name, strand))
        elif _revcomp(sequence).upper() == inserted.upper():
            found.append((name, -strand))
    # One family may be annotated many times; that is one answer, not several.
    return sorted(set(found))


def _annotated_repeats(loaded, seq_id):
    """(family, sequence, strand) for each annotated repeat region on this contig.

    Reads `AnnotatedSequence.repeat_locations`, which the annotator already builds from the
    reference's `repeat_region` and `mobile_element` features -- the same list breseq's own
    MOB handling works from, rather than a second opinion about what a repeat is.

    Names go through `trim_repeat_name`, so `IS186B` and `IS186` are one family. That is what
    makes "exactly one family matches" the right test: a genome with ten annotated copies of
    IS186 is one answer, not ten.

    Empty when the reference annotates no repeats, which is the common case and is why
    inference is silent rather than wrong on most data.
    """
    from mutint_import.annotate.model import trim_repeat_name

    try:
        contig = loaded[seq_id]
    except (KeyError, TypeError):
        return []

    repeats = []
    for location in getattr(contig, "repeat_locations", ()) or ():
        feature = getattr(location, "feature", None)
        name = trim_repeat_name(getattr(feature, "name", "") or "")
        if not name:
            continue
        try:
            sequence = contig.get_sequence_1(location.start_1, location.end_1)
        except Exception:
            continue
        if sequence:
            repeats.append((name, sequence, location.strand or 1))
    return repeats


def _duplication_size(loaded, seq_id, position, inserted):
    """The target-site duplication actually present, measured rather than assumed.

    A mobile element usually duplicates a few bases of its target on insertion. The evidence
    is that the bases immediately before the insertion point repeat immediately after it. We
    measure how many, and answer 0 when none do -- which is a real answer: not every insertion
    duplicates, and inventing a plausible number would make the record look like evidence.
    """
    try:
        before = loaded.get_sequence_1(seq_id, max(1, position - 20), position) or ""
        after = loaded.get_sequence_1(seq_id, position + 1, position + 20) or ""
    except Exception:
        return 0

    longest = 0
    for size in range(1, min(len(before), len(after)) + 1):
        if before[-size:].upper() == after[:size].upper():
            longest = size
    return longest


def _revcomp(sequence):
    return sequence.translate(str.maketrans("ACGTacgt", "TGCAtgca"))[::-1]


def import_sample(document, sample_name, context, experiment):
    """Write one VCF sample. Returns `(mutations, replaced, problems)`.

    The records go through `gd_import`'s own machinery: `import_document_as_sample` resolves
    the sample's coordinate from its name -- so a VCF named `Ara-2_500gen_763A.vcf` lands on
    its ALE exactly as the `.gd` of the same name would -- and `_database_gd_mutations` writes
    the rows. Nothing about a mutation is decided here.

    Afterwards the VCF's own material is attached: the header to the sample, each line to the
    call it produced. That is a second pass rather than a parameter to the writer, because the
    writer is shared with the `.gd` path and must not grow a VCF-shaped argument.
    """
    from mutint_import.gd_import import import_document_as_sample

    converted = convert(document, sample_name, experiment, filename=sample_name)
    sample, count, replaced = import_document_as_sample(
        _AsGenomeDiff(converted.records, metadata_for(document)), sample_name, context)

    sample.set_record(sample.COMPONENT, VCF_RECORD, header_record(document))
    # What it was made from, beside the header it was made from. A VCF names no read files --
    # `_AsGenomeDiff` carries a plain dict with no `READSEQ` in it -- so the file itself is
    # the answer.
    inputs.record_inputs(sample, [inputs.Input(inputs.KIND_VCF, sample_name)])
    _attach_lines(sample, converted)
    return count, replaced, converted.problems


#: VCF header keys that mean what a GenomeDiff `#=` header means. The importer reads REFSEQ
#: and CREATED off a document to fill `Sample.sequencing`, and a VCF says both -- so mapping
#: them is carrying real information across rather than filling in a shim.
METADATA_FROM_HEADER = {"reference": "REFSEQ", "filedate": "CREATED", "source": "COMMAND"}


def metadata_for(document):
    """A VCF header as the `#=` metadata `import_document_as_sample` reads.

    `##reference=` is the genome the calls were made against, which is exactly what `#=REFSEQ`
    is; `##fileDate=` is `#=CREATED`. Both end up on `Sample.sequencing`, where the `.gd`
    export already reads the first to write its own header back out.

    `COMMAND` is mapped from `##source=` and is used for one thing: `is_clonal` is false when
    the command contains ` -p`, breseq's polymorphism flag. No other caller spells it that
    way, so in practice a VCF sample is clonal -- which is the right default under the haploid
    assumption this importer is built on, and is a guess worth naming rather than burying.
    """
    metadata = {}
    for line in document.header:
        if not line.startswith("##") or "=" not in line:
            continue
        key, _, value = line[2:].partition("=")
        mapped = METADATA_FROM_HEADER.get(key.strip().lower())
        if mapped and mapped not in metadata:
            metadata[mapped] = value.strip()
    return metadata


class _AsGenomeDiff:
    """The little that `import_document_as_sample` asks of a parsed document.

    It reads `.mutations`, `.metadata` and -- through `_database_uncalled_regions` --
    `.evidence`. A VCF has no uncalled regions, so that list is empty and the sample's
    existing ones are left alone rather than rewritten to nothing.

    A shim rather than a real `GenomeDiff`, because building one would mean serializing our
    records to text and parsing them straight back to prove nothing.
    """

    def __init__(self, records, metadata=None):
        from genomediff.records import Record

        self.mutations = [
            Record(record["type"], None, parent_ids=None,
                   **{k: v for k, v in record.items() if k != "type"})
            for record in records]
        self.metadata = metadata or {}
        self.evidence = []
        self.parse_errors = []


def _attach_lines(sample, converted):
    """Give each call the VCF line it came from.

    Matched by position: `convert` appends to `records` and `lines` together, and
    `_database_gd_mutations` writes one call per record in the same order. Re-read from the
    database rather than trusted, so a mismatch is visible instead of silently mis-attaching
    somebody else's line.
    """
    from mutint_sample.models import MutationCall

    calls = list(MutationCall.objects.filter(sample=sample).order_by("pk"))
    if len(calls) != len(converted.lines):
        logger.warning(
            "sample %s produced %d calls for %d VCF lines; not attaching the originals, so "
            "its VCF export will be regenerated rather than verbatim",
            sample.pk, len(calls), len(converted.lines))
        return

    for call, line in zip(calls, converted.lines):
        call.set_record(call.COMPONENT, VCF_RECORD,
                        call_record(line, converted.name), save=False)
        call.source = VCF_SOURCE
    MutationCall.objects.bulk_update(calls, ["supplemental_data", "source"])


def header_record(document):
    """What to keep on the `Sample` so its VCF can be written back out."""
    return {
        "header": list(document.header),
        "columns": list(document.columns),
        "samples": list(document.sample_names),
    }


def call_record(line, sample_name):
    """What to keep on the `MutationCall` so it can reproduce its own line.

    **Per call rather than per mutation, deliberately.** A `Mutation` is shared -- deduplicated
    across samples and written once on create -- so a site record hung there would be whichever
    VCF happened to write first, and a multi-allelic split means one line maps to several
    mutations anyway. Duplicating a few hundred bytes per call is what buys every call the
    ability to reproduce exactly the line it came from.

    And **nothing VCF-specific goes near the GenomeDiff record**: `Mutation.to_gd_line()` splats
    every key of that record onto the line it emits, which is why it is contractually verbatim.
    This is the one place gdtools' behavior must not be copied -- it writes `AD=`, `DP=` and
    `AF=` into the GenomeDiff, and those are not GenomeDiff fields.
    """
    record = {"fixed": line.fixed}
    if line.format:
        record["format"] = line.format
        if sample_name in line.samples:
            record["sample"] = line.samples[sample_name]
    return record
