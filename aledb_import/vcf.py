"""Reading VCF, and turning each call into the GenomeDiff record ALEdb stores.

**GenomeDiff is the anchor format.** A VCF import is not a second kind of mutation: it
produces exactly the records the `.gd` path produces, hands them to the same
`gd_import._database_gd_mutations`, and therefore lands on the same `Mutation` rows through
the same seven-field `get_or_create`. A site called by breseq and the same site called by GATK
must be one row, and the way to guarantee that is to have one place that decides what a
mutation *is*.

This module is pure -- text in, records out, no database and no Django -- in the shape
`reference.py` and `sample_names.py` already have. That is what makes the two rules most
likely to be got wrong testable on their own: how a variant normalizes, and what it is then
called.

`gdtools VCF2GD` is the pattern for the position arithmetic and is deliberately not followed
elsewhere. What it actually does, measured against breseq 0.50 rather than assumed:

- it classifies on string length alone and never reads the reference, so a VCF called against
  the wrong assembly imports silently and wrongly;
- it strips only an *exact full-REF* prefix, so `AC -> AGG` becomes `INS AGG`;
- it has no SUB path at all: equal-length MNPs are dropped with a warning, and unequal
  non-indels are mangled into INS;
- a comma in ALT is a **fatal error that abandons the whole file**;
- FORMAT and every sample column are discarded, and INFO is dumped verbatim into GenomeDiff
  fields -- which for us would be actively wrong, because `Mutation.to_gd_line()` splats every
  key of the stored record onto the line it emits.

Each divergence has a test naming its reason, and where we agree there is a differential test
asserting we still do.
"""

import gzip
import io
import re

# VCF's own spelling of "no value".
MISSING = "."

# The eight columns every VCF has, in order, before FORMAT and the sample columns.
FIXED_COLUMNS = ("CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO")

# Bases we will build a mutation out of. Anything else in REF or ALT -- IUPAC ambiguity, a
# symbolic <DEL>, a breakend's square brackets -- is reported rather than guessed at.
_PLAIN_BASES = re.compile(r"^[ACGTNacgtn]*$")

# A genotype that is not a call. `.` is no-call and `0` is the reference allele; neither is a
# mutation, and importing them would put a row in every sample for every site in the file.
_NO_CALL = frozenset((MISSING, "0"))


class VcfError(Exception):
    """The file is not a VCF, or is one we cannot read at all."""


class LineProblem(Exception):
    """One line cannot become a mutation. The file continues; the reason is reported.

    The same posture `gd_import._is_storable` and `parse_warnings` take with a `.gd` line the
    parser could not fully read: one bad line must not cost the other thousand, and the reason
    reaches the person who uploaded it rather than a log nobody reads.
    """


class Variant:
    """One ALT allele of one VCF line, normalized, with the fields needed to place it."""

    def __init__(self, seq_id, position, ref, alt, allele_index, line):
        self.seq_id = seq_id
        self.position = position          # 1-based, after trimming
        self.ref = ref                    # trimmed; "" for a pure insertion
        self.alt = alt                    # trimmed; "" for a pure deletion
        self.allele_index = allele_index  # 1-based index into the line's ALT list
        self.line = line                  # the VcfLine it came from

    def __repr__(self):
        return "Variant(%s:%s %s->%s)" % (self.seq_id, self.position,
                                          self.ref or "-", self.alt or "-")


def trim(position, ref, alt):
    """Normalize one REF/ALT pair. Returns `(position, ref, alt)`.

    Common suffix first, then common prefix, advancing the position by each prefix base
    removed. `bcftools norm` semantics with one difference: **the anchor base is removed
    entirely** rather than kept. VCF keeps an anchor because it has no way to spell a
    zero-length allele; GenomeDiff needs none, and its INS and DEL positions are defined
    without one.

    Suffix before prefix, and the order matters twice over.

    On `AT -> AGT` trimming the prefix first leaves `T -> GT`, a substitution of one base for
    two; trimming the suffix first leaves `A -> AG` and then `"" -> G`, an insertion -- which
    is what it is. Trimming the *shared* end first is what keeps an insertion from being read
    as a replacement.

    And it is what **left-aligns** an indel in a repeat. `AAA -> AA` at 100 is a deletion of
    one A, and which A is unknowable; the convention is the leftmost. Suffix-first gives
    `DEL 100`, prefix-first gives `DEL 102`. Both produce the same sequence and only one
    agrees with every other tool, which matters because `start_position` is one of the seven
    fields `Mutation.objects.get_or_create` keys on -- the same deletion reported by two
    callers must not become two rows.

    The result may be `("", "")` for a variant that says nothing (`REF == ALT`); the caller
    refuses those rather than storing a mutation that changes nothing, which is the same rule
    `aledb_mutation_editor.validation` applies to a hand-typed one.
    """
    ref = ref or ""
    alt = alt or ""

    # Suffix. Stop while either allele still has a base: trimming past that would lose the
    # position the remaining bases sit at.
    while ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]

    # Prefix, advancing the position as bases come off the front.
    while ref and alt and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
        position += 1

    return position, ref, alt


def classify(variant):
    """The GenomeDiff type and fields for a normalized variant, as a plain dict.

    Four rules, and the last is one rule rather than three::

        both one base          SNP  at P                new_seq = ALT
        REF empty              INS  at P - 1            new_seq = ALT
        ALT empty              DEL  at P                size    = len(REF)
        both non-empty         SUB  at P                size    = len(REF), new_seq = ALT

    **`SUB` replaces `size` reference bases with `new_seq`, and the two lengths need not
    match.** `AC -> AGG` trims to `C -> GG` and is `SUB size=1 new_seq=GG` -- the C is
    *changed*, not merely inserted after, and calling it an insertion loses that. An
    equal-length MNP is `SUB size=n`; `AT -> GCCC` is `SUB size=2 new_seq=GCCC`. Splitting
    this by whether the lengths happen to be equal would be a distinction GenomeDiff does not
    make, and gdtools' two separate wrong answers for the two halves are what having no SUB
    path looks like.

    **INS sits at `P - 1`, the base it follows.** After trimming, `P` is where the inserted
    sequence would begin; GenomeDiff names the reference base the insertion comes after. On a
    canonical left-anchored VCF indel that is the original POS, which is why this agrees with
    gdtools there and diverges only on input that was not canonical.
    """
    ref, alt = variant.ref, variant.alt

    if not ref and not alt:
        raise LineProblem("REF and ALT are the same; this describes no mutation")

    if len(ref) == 1 and len(alt) == 1:
        return {"type": "SNP", "seq_id": variant.seq_id,
                "position": variant.position, "new_seq": alt}

    if not ref:
        return {"type": "INS", "seq_id": variant.seq_id,
                "position": variant.position - 1, "new_seq": alt}

    if not alt:
        return {"type": "DEL", "seq_id": variant.seq_id,
                "position": variant.position, "size": len(ref)}

    return {"type": "SUB", "seq_id": variant.seq_id,
            "position": variant.position, "size": len(ref), "new_seq": alt}


def applied_to(sequence, record):
    """`sequence` with `record` applied, for checking that a conversion says what it meant.

    1-based inclusive positions throughout, matching GenomeDiff. Used by the round-trip test
    rather than by the importer: a conversion that cannot reproduce the ALT it came from is
    wrong however plausible its fields look, and that is the whole class of off-by-one this
    module has to avoid.
    """
    position = record["position"]
    kind = record["type"]

    if kind == "SNP":
        return sequence[:position - 1] + record["new_seq"] + sequence[position:]
    if kind == "SUB":
        return sequence[:position - 1] + record["new_seq"] + sequence[position - 1 + record["size"]:]
    if kind == "DEL":
        return sequence[:position - 1] + sequence[position - 1 + record["size"]:]
    if kind == "INS":
        # After `position`, which is the base it follows.
        return sequence[:position] + record["new_seq"] + sequence[position:]
    raise ValueError("cannot apply %s" % kind)


class VcfLine:
    """One data line, parsed into its columns and kept verbatim.

    The verbatim halves are what makes the export faithful: `fixed` is the eight columns as
    they were written, and `samples` maps each column name to its untouched string. Nothing
    here reformats a number or reorders a key, because a VCF exported from ALEdb should be the
    VCF that came in.
    """

    def __init__(self, fields, columns, sample_names):
        self.fields = fields
        self.chrom = fields[0]
        self.id = fields[2]
        self.ref = fields[3]
        self.alt = fields[4]
        self.qual = fields[5]
        self.filter = fields[6]
        self.info = fields[7] if len(fields) > 7 else ""
        self.format = fields[8] if len(fields) > 8 else ""
        self.samples = {name: fields[9 + index]
                        for index, name in enumerate(sample_names)
                        if 9 + index < len(fields)}

        try:
            self.position = int(fields[1])
        except (TypeError, ValueError):
            raise LineProblem("POS is not a number: %r" % (fields[1],))

        self.alts = [] if self.alt == MISSING else self.alt.split(",")

    @property
    def fixed(self):
        """The eight fixed columns, verbatim, for the export to re-emit."""
        return list(self.fields[:8])

    def genotype(self, sample_name):
        """The GT value for one sample, or None when the line carries no genotypes.

        None means "this file does not say", which is different from "not called" -- a
        sites-only VCF has no FORMAT at all and every line in it is a call for the one sample
        the file describes.
        """
        if not self.format or sample_name not in self.samples:
            return None
        keys = self.format.split(":")
        if "GT" not in keys:
            return None
        values = self.samples[sample_name].split(":")
        index = keys.index("GT")
        return values[index] if index < len(values) else None

    def format_field(self, sample_name, key):
        """One FORMAT value for one sample, or None."""
        if not self.format or sample_name not in self.samples:
            return None
        keys = self.format.split(":")
        if key not in keys:
            return None
        values = self.samples[sample_name].split(":")
        index = keys.index(key)
        value = values[index] if index < len(values) else None
        return None if value in (None, MISSING) else value

    def info_map(self):
        """INFO parsed into a dict. A valueless flag maps to True, as in the spec."""
        parsed = {}
        if not self.info or self.info == MISSING:
            return parsed
        for item in self.info.split(";"):
            if not item:
                continue
            key, sep, value = item.partition("=")
            parsed[key] = value if sep else True
        return parsed


class VcfDocument:
    """A parsed VCF: its header verbatim, its column names, and its data lines."""

    def __init__(self, header, columns, sample_names, lines, problems):
        self.header = header              # the `##` lines, verbatim, in order
        self.columns = columns            # the `#CHROM` line's fields
        self.sample_names = sample_names  # column 10 onwards
        self.lines = lines
        self.problems = problems          # [str], reported the way parse_warnings are

    @property
    def is_sites_only(self):
        return not self.sample_names


def read(handle, filename=""):
    """Parse a VCF (optionally gzipped) into a `VcfDocument`.

    Lenient in the same way `gd_import._parse_document` is: a line that cannot be read lands
    in `problems` and the rest of the file still loads. Only a file that is not a VCF at all
    raises, because there is nothing to salvage from one.
    """
    raw = handle.read()
    if isinstance(raw, bytes):
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError as exc:
                raise VcfError("could not decompress %s: %s" % (filename or "the file", exc))
        raw = raw.decode("utf-8", "replace")

    header = []
    columns = []
    sample_names = []
    lines = []
    problems = []

    for text in io.StringIO(raw):
        text = text.rstrip("\r\n")
        if not text.strip():
            continue
        if text.startswith("##"):
            header.append(text)
            continue
        if text.startswith("#CHROM"):
            columns = text.lstrip("#").split("\t")
            sample_names = columns[9:] if len(columns) > 9 else []
            continue
        if text.startswith("#"):
            header.append(text)
            continue

        if not columns:
            raise VcfError(
                "no #CHROM header line; this does not look like a VCF")

        fields = text.split("\t")
        if len(fields) < 8:
            problems.append("skipped a line with %d columns, expected at least 8: %s"
                            % (len(fields), text[:80]))
            continue
        try:
            lines.append(VcfLine(fields, columns, sample_names))
        except LineProblem as problem:
            problems.append("%s: %s" % (problem, text[:80]))

    if not header and not columns:
        raise VcfError("%s is empty or is not a VCF" % (filename or "the file",))
    if not columns:
        raise VcfError("no #CHROM header line; this does not look like a VCF")

    return VcfDocument(header, columns, sample_names, lines, problems)


def looks_like_vcf(text):
    """Whether the first non-blank line declares VCF, for the sniffer.

    The same rule gdtools applies, and the same rule `aledb_import.sniff` uses for the other
    formats: the first line is where each of them says what it is.
    """
    for line in io.StringIO(text):
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.lower().startswith("##fileformat=vcf")
    return False


def variants_for(line, sample_name=None):
    """The normalized variants this line calls, for one sample or for a sites-only file.

    **Multi-allelic lines are split, not refused.** gdtools aborts the whole file on a comma
    in ALT; here each ALT becomes its own candidate and the sample's genotype chooses between
    them, which is exactly what the haploid assumption makes unambiguous: one sample, one
    allele. Two samples choosing differently at one site produce two mutations, each called in
    its own samples -- which is correct rather than a compromise, because they are different
    mutations.
    """
    if not line.alts:
        return []

    chosen = _chosen_alleles(line, sample_name)
    variants = []
    for index in chosen:
        alt = line.alts[index - 1]
        _check_alleles(line.ref, alt)
        position, ref, trimmed_alt = trim(line.position, line.ref, alt)
        variants.append(Variant(line.chrom, position, ref, trimmed_alt, index, line))
    return variants


def _chosen_alleles(line, sample_name):
    """Which 1-based ALT indices this sample calls.

    A sites-only file, or a line with no GT, is every ALT -- the file is asserting the
    variants it lists and has no genotype to qualify them with. A GT of `0`, `./.` or any
    mixture of no-calls and reference is nothing.
    """
    genotype = line.genotype(sample_name) if sample_name else None
    if genotype is None:
        return list(range(1, len(line.alts) + 1))

    # Haploid is the assumption, and a diploid-looking GT is read for which alleles it names
    # rather than refused: `1/1` and `1` mean the same thing here, and `1/2` names two.
    called = []
    for token in re.split(r"[/|]", genotype):
        if token in _NO_CALL or not token.isdigit():
            continue
        index = int(token)
        if 1 <= index <= len(line.alts) and index not in called:
            called.append(index)
    return called


def _check_alleles(ref, alt):
    """Refuse anything that is not plain bases, naming what it looked like.

    Symbolic alleles and breakends are real VCF and describe mutations GenomeDiff has types
    for -- but inferring `<DUP>` into an AMP means trusting a caller-specific END/SVLEN
    convention, and a wrong structural variant is worse than an honest refusal.
    """
    if alt.startswith("<") or "[" in alt or "]" in alt:
        raise LineProblem(
            "ALT %s is a symbolic or breakend allele, which this importer does not convert"
            % alt)
    if not _PLAIN_BASES.match(ref or "") or not _PLAIN_BASES.match(alt or ""):
        raise LineProblem("REF/ALT contain characters that are not plain bases: %s -> %s"
                          % (ref, alt))


def frequency_for(line, sample_name, allele_index):
    """The call's frequency in [0, 1], or None if the file does not say.

    Preferred in the order the fields actually mean it: the sample's own AF, then its AD (the
    called allele's depth over the total), then the site's AF. A site-level AF is a property of
    the cohort rather than of this sample, so it is the last resort rather than the first.

    None rather than a guess when nothing says -- `gd_import._coerce_frequency` turns that into
    1.0, which is the right reading of a haploid call with no frequency attached.
    """
    sample_af = line.format_field(sample_name, "AF") if sample_name else None
    value = _nth(sample_af, allele_index - 1)
    if value is not None:
        return _as_fraction(value)

    depths = line.format_field(sample_name, "AD") if sample_name else None
    if depths:
        parts = [_as_number(part) for part in depths.split(",")]
        total = sum(part for part in parts if part is not None)
        if total and allele_index < len(parts) and parts[allele_index] is not None:
            return parts[allele_index] / total

    info = line.info_map().get("AF")
    if isinstance(info, str):
        return _as_fraction(_nth(info, allele_index - 1))
    return None


def _nth(value, index):
    if not value:
        return None
    parts = value.split(",")
    return parts[index] if index < len(parts) else None


def _as_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_fraction(value):
    number = _as_number(value)
    if number is None:
        return None
    # A percentage where a fraction was meant is common enough in hand-made files to be worth
    # not storing as 100.0, which would render as 10000% wherever frequency is shown.
    if number > 1.0:
        return None
    return number
