"""Writing a sample back out as the VCF it came in as.

Verbatim where it can be: the header the file arrived with, and each call's own data line,
both kept at import. For a single-sample VCF that is byte-for-byte what was uploaded; for one
column of a multi-sample VCF it is that column's faithful projection.

**A mutation added or edited since import has no stored line**, and is regenerated from the
GenomeDiff record instead. Both alternatives are worse: refusing outright would make the
mutation editor and this feature mutually exclusive, and saying nothing would hand somebody a
file that quietly is not what they gave us. So the file says how many rows were regenerated,
in a `##aledb_` header line, and a reader who cares can see which.
"""

from aledb_sample.models import MutationCall

VCF_RECORD = "vcf"

FALLBACK_HEADER = ["##fileformat=VCFv4.2"]
DEFAULT_COLUMNS = ["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]

# What a regenerated row carries where the file had QUAL and FILTER, neither of which
# GenomeDiff records. `.` is VCF's own "no value" and is the honest answer.
MISSING = "."


def export_vcf_text(sample):
    """The sample as VCF, or None if it did not come from one."""
    stored = sample.record(VCF_RECORD)
    if not stored:
        return None

    calls = (MutationCall.objects
             .filter(sample=sample)
             .select_related("mutation")
             .order_by("mutation__seq_id", "mutation__start_position", "pk"))

    rows, regenerated = [], 0
    for call in calls:
        line = _stored_line(call)
        if line is None:
            line = _regenerate(call)
            if line is None:
                continue
            regenerated += 1
        rows.append(line)

    header = list(stored.get("header") or FALLBACK_HEADER)
    if regenerated:
        header.append(
            "##aledb_regenerated=%d  # rows added or edited in ALEdb since this VCF was "
            "imported; rebuilt from the mutation, so QUAL, FILTER and INFO are '.'"
            % regenerated)

    columns = _columns_for(stored, rows)
    return "\n".join(header + ["#" + "\t".join(columns)] + rows) + "\n"


def _columns_for(stored, rows):
    """The `#CHROM` line, narrowed to this one sample.

    A multi-sample VCF exported per sample keeps its own column and drops the others, so the
    file is a valid single-sample VCF rather than one whose header promises columns that are
    not there.
    """
    columns = list(stored.get("columns") or DEFAULT_COLUMNS)
    fixed = columns[:8] if len(columns) >= 8 else list(DEFAULT_COLUMNS)
    has_genotypes = any(row.count("\t") >= 9 for row in rows)
    if not has_genotypes:
        return fixed
    name = (stored.get("samples") or [""])[0] if len(stored.get("samples") or []) == 1 else None
    return fixed + ["FORMAT", name or "SAMPLE"]


def _stored_line(call):
    """The verbatim line this call came in on, or None."""
    record = call.record(VCF_RECORD)
    fixed = record.get("fixed") if record else None
    if not fixed:
        return None

    fields = list(fixed)
    if record.get("format"):
        fields.append(record["format"])
        fields.append(record.get("sample", MISSING))
    return "\t".join(fields)


def _regenerate(call):
    """A VCF line built from the mutation, for a row that has no stored one.

    The inverse of `vcf.classify`, and it needs the anchor base back: VCF has no way to spell
    a zero-length allele, so an insertion is written as the reference base it follows plus the
    inserted sequence, and a deletion as that base plus what is removed. The reference is read
    for exactly that one base.

    Returns None for a mutation VCF cannot express -- a MOB, an AMP -- rather than writing a
    line that would not mean what the row means.
    """
    from aledb_import.annotation import reference_sequences_for

    mutation = call.mutation
    experiment = getattr(getattr(call.sample, "population", None), "experiment", None)
    kind = mutation.mutation_type
    position = mutation.start_position
    record = mutation.genome_diff or {}

    loaded = None
    if experiment is not None:
        try:
            loaded = reference_sequences_for(experiment)
        except Exception:
            loaded = None

    def base_at(where):
        if loaded is None:
            return None
        try:
            return loaded.get_sequence_1(mutation.seq_id, where, where) or None
        except Exception:
            return None

    if kind == "SNP":
        ref = base_at(position) or MISSING
        alt = record.get("new_seq") or mutation.sequence_change
    elif kind == "SUB":
        size = mutation.feature_length or record.get("size") or 1
        ref = _span(loaded, mutation.seq_id, position, position + size - 1) or MISSING
        alt = record.get("new_seq") or mutation.sequence_change
    elif kind == "DEL":
        size = mutation.feature_length or record.get("size") or 0
        anchor = base_at(position - 1)
        span = _span(loaded, mutation.seq_id, position, position + size - 1)
        if anchor is None or span is None:
            return None
        ref, alt, position = anchor + span, anchor, position - 1
    elif kind == "INS":
        anchor = base_at(position)
        if anchor is None:
            return None
        ref = anchor
        alt = anchor + (record.get("new_seq") or mutation.sequence_change)
    else:
        # MOB, AMP, INV, CON: real mutations with no faithful VCF spelling.
        return None

    frequency = call.frequency
    info = MISSING if frequency is None else "AF=%.4f" % frequency
    return "\t".join([mutation.seq_id or MISSING, str(position), MISSING,
                      ref, alt, MISSING, MISSING, info])


def _span(loaded, seq_id, start, end):
    if loaded is None or end < start:
        return None
    try:
        return loaded.get_sequence_1(seq_id, start, end) or None
    except Exception:
        return None
