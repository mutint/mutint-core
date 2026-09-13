"""Writing a sample out as VCF: the file it came in as, or one generated from its mutations.

Verbatim where it can be: the header the file arrived with, and each call's own data line,
both kept at import. For a single-sample VCF that is byte-for-byte what was uploaded; for one
column of a multi-sample VCF it is that column's faithful projection.

**A mutation added or edited since import has no stored line**, and is regenerated from the
GenomeDiff record instead. Both alternatives are worse: refusing outright would make the
mutation editor and this feature mutually exclusive, and saying nothing would hand somebody a
file that quietly is not what they gave us. So the file says how many rows were regenerated,
in a `##mutint_` header line, and a reader who cares can see which.

**A sample that never came from a VCF gets a generated one**, every row regenerated the same
way, under a header MutInt writes: the contigs from the reference, the sample's placement,
and a `##source` line saying the file is MutInt's. It is a lossy view for tools that speak
VCF, and the file says what it lost: a MOB, AMP, INV or CON has no VCF spelling, and
`##mutint_omitted` counts them. The `.gd` is the complete record.
"""

from mutint_import import metadata as sample_metadata
from mutint_import.gd_import import format_time_point
from mutint_sample.models import MutationCall

VCF_RECORD = "vcf"

FALLBACK_HEADER = ["##fileformat=VCFv4.2"]
DEFAULT_COLUMNS = ["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]

# What a regenerated row carries where the file had QUAL and FILTER, neither of which
# GenomeDiff records. `.` is VCF's own "no value" and is the honest answer.
MISSING = "."


def export_vcf_text(sample):
    """The sample as VCF: the file it came from, or one generated from its mutations."""
    stored = sample.record(VCF_RECORD)
    generated = not stored

    calls = (MutationCall.objects
             .filter(sample=sample)
             .select_related("mutation")
             .order_by("mutation__seq_id", "mutation__start_position", "pk"))

    rows, regenerated, omitted = [], 0, []
    for call in calls:
        line = None if generated else _stored_line(call)
        if line is None:
            line = _regenerate(call)
            if line is None:
                omitted.append(call.mutation)
                continue
            if not generated:
                regenerated += 1
            else:
                # A genotype column named for the sample, so the file is a single-sample
                # VCF and the importer names the sample by that column rather than by
                # whatever the file is called -- which is what lets the `##SAMPLE` line
                # place it and a re-import land on the same sample.
                line += "\tGT\t1"
        rows.append(line)

    if generated:
        column = sample.source_name or "sample_%d" % sample.pk
        header = _generated_header(sample)
    else:
        column = _column_for(sample, stored)
        header = list(stored.get("header") or FALLBACK_HEADER)
    header = _with_placement(header, sample, column)
    if regenerated:
        header.append(
            "##mutint_regenerated=%d  # rows added or edited in MutInt since this VCF was "
            "imported; rebuilt from the mutation, so QUAL, FILTER and INFO are '.'"
            % regenerated)
    if omitted:
        header.append(_omitted_line(omitted))

    columns = _columns_for(stored, rows, column)
    return "\n".join(header + ["#" + "\t".join(columns)] + rows) + "\n"


def _generated_header(sample):
    """The header of a VCF MutInt writes itself, for a sample that came from none.

    What a reader of the file needs and the reference can supply: the contigs with their
    lengths, which `vcf.read` checks positions against; the genome the sample says it was
    called against; and `AF`, the one INFO field the rows carry. `##source` names MutInt so
    the file is not mistaken for a caller's own output.
    """
    from mutint_common.version import __version__
    from mutint_sample.models import ReferenceSequences

    header = ["##fileformat=VCFv4.2", "##source=MutInt %s" % __version__]
    reference_genome = sample.sequencing.get("reference_genome")
    if reference_genome:
        header.append("##reference=%s" % reference_genome)
    experiment = getattr(getattr(sample, "population", None), "experiment", None)
    reference = (ReferenceSequences.objects.filter(experiment=experiment).first()
                 if experiment is not None else None)
    for entry in (reference.seq_ids if reference else None) or []:
        if entry.get("id"):
            header.append("##contig=<ID=%s,length=%s>" % (entry["id"], entry.get("length", "")))
    header.append('##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency of the '
                  'mutation in this sample">')
    header.append('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">')
    return header


def _omitted_line(mutations):
    """The mutations the file could not spell, counted by type so the loss is named."""
    counts = {}
    for mutation in mutations:
        counts[mutation.mutation_type] = counts.get(mutation.mutation_type, 0) + 1
    by_type = ", ".join("%d %s" % (count, kind) for kind, count in sorted(counts.items()))
    return ("##mutint_omitted=%d  # mutations with no VCF spelling, left out of this file: %s; "
            "the sample's .gd carries them" % (len(mutations), by_type))


def _column_for(sample, stored):
    """The name of this sample's column in the file it came from.

    The importer names a sample from its column (`sample_names_for`), and that name is its
    `source_name`; a sites-only file has no column and the sample is named from the file,
    which is also its `source_name`. So the source name is the answer whenever it is still
    one the file knows, and the file's one column otherwise -- a renamed sample of a
    single-sample file is still that file's sample.
    """
    samples = list(stored.get("samples") or [])
    if sample.source_name in samples or len(samples) != 1:
        return sample.source_name
    return samples[0]


def _with_placement(header, sample, column):
    """The header with one `##SAMPLE=<ID=...>` line saying where this sample sits now.

    The structured `##SAMPLE` line is the spec's own per-column metadata and the one the
    importer reads first (`vcf_import.metadata_for`), so an exported file re-imported
    anywhere lands on the sample's current coordinate. **Upserted, not appended**: the line
    for this column is rewritten in place when the file already has one, keeping every
    field of it that is not a placement, so exporting an imported export is byte-identical.
    Placement keys the file's own line carried are dropped rather than kept beside MutInt's,
    for the reason the `.gd` export drops them: the sample may have been moved since.
    """
    fields = {}
    index = None
    for position, line in enumerate(header):
        if not line.startswith("##SAMPLE=<") or not line.endswith(">"):
            continue
        from mutint_import.vcf_import import _structured_fields
        existing = _structured_fields(line[len("##SAMPLE=<"):-1])
        if existing.get("ID") == column:
            index = position
            fields = {key: value for key, value in existing.items()
                      if key != "ID" and not sample_metadata.is_placement_key(key)}
            break

    placement = {"sample": sample.name}
    population = sample.population_name
    from mutint_import.gd_import import UNSPECIFIED_POPULATION
    if sample.time_point is not None and population != UNSPECIFIED_POPULATION:
        placement["population"] = population
        placement["time_point"] = format_time_point(sample.time_point)
    placement["sample_type"] = "clone" if sample.is_clonal else "population"

    parts = ["ID=%s" % column]
    parts += ["%s=%s" % (key, _quoted(value)) for key, value in placement.items()]
    parts += ["%s=%s" % (key, _quoted(value)) for key, value in fields.items()]
    line = "##SAMPLE=<%s>" % ",".join(parts)
    header = list(header)
    if index is None:
        header.append(line)
    else:
        header[index] = line
    return header


def _quoted(value):
    value = str(value)
    if "," in value or '"' in value:
        return '"%s"' % value.replace('"', "")
    return value


def _columns_for(stored, rows, column):
    """The `#CHROM` line, narrowed to this one sample.

    A multi-sample VCF exported per sample keeps its own column and drops the others, so the
    file is a valid single-sample VCF rather than one whose header promises columns that are
    not there. The column carries the name the `##SAMPLE` line refers to, which is what
    makes the projection re-importable as the sample it is.
    """
    columns = list((stored or {}).get("columns") or DEFAULT_COLUMNS)
    fixed = columns[:8] if len(columns) >= 8 else list(DEFAULT_COLUMNS)
    has_genotypes = any(row.count("\t") >= 9 for row in rows)
    if not has_genotypes:
        return fixed
    return fixed + ["FORMAT", column or "SAMPLE"]


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
    from mutint_import.annotation import reference_sequences_for

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
