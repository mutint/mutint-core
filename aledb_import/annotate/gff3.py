"""
breseq's GFF3 -> the annotation feature model.

A breseq run writes its reference as `data/reference.gff3`, and that file carries
everything annotation needs: CDS/tRNA/rRNA types, locus tags, translation
tables, pseudogene flags, repeat regions, spliced genes and indeterminate ends.
It is therefore the natural annotation input for a breseq folder import -- no
GenBank required.

Ported from cReferenceSequences::ReadGFF (reference_sequence.cpp:1490-1660).
Note this is a *different* reader from `aledb_import.reference.read_gff3`, which
exists to derive the normalized genes-only reference the store hashes. That one
deliberately flattens everything annotation depends on.
"""

from aledb_import.annotate.model import (
    DEFAULT_REPEAT_NAME,
    DEFAULT_REPEAT_PRODUCT,
    GENE_TYPES,
    MULTIPLE_SEPARATOR,
    REPEAT_TYPES,
    AnnotatedSequence,
    Feature,
    FeatureLocation,
    LoadedReferenceSequences,
    make_safe,
    trim_repeat_name,
)

FASTA_DIRECTIVE = '##FASTA'

# breseq writes a pseudogene as "fCDS" -- a fragment CDS -- and reads it back as
# a CDS with the pseudo flag set. reference_sequence.cpp:1571-1575
PSEUDO_CDS_TYPE = 'fCDS'

GFF3_ESCAPES = (
    ('%3B', ';'), ('%3D', '='), ('%26', '&'), ('%2C', ','),
    ('%09', '\t'), ('%0A', '\n'), ('%0D', '\r'),
)


def unescape(value):
    """GFF3 percent-decoding. `%25` last, so it cannot double-decode."""
    for encoded, plain in GFF3_ESCAPES:
        value = value.replace(encoded, plain).replace(encoded.lower(), plain)
    return value.replace('%25', '%')


def parse_attributes(field):
    """
    The GFF3 attribute column as {key: [values]}.

    Values are comma-split before unescaping, matching breseq, so an escaped
    comma inside one value survives.
    """
    attributes = {}
    for item in field.split(';'):
        item = item.strip()
        if not item or '=' not in item:
            continue
        key, _, value = item.partition('=')
        attributes[key.strip()] = [unescape(part) for part in value.split(',')]
    return attributes


def _joined(attributes, key):
    return ','.join(attributes.get(key, []))


def _first(attributes, key):
    values = attributes.get(key)
    return values[0] if values else ''


def _accession(attributes):
    """The locus tag: accession > locus_tag > Alias.

    breseq (reference_sequence.cpp:1544-1551) also falls back to ``ID``. We do not,
    deliberately. ``ID`` is GFF3's row-identity field -- it is what groups the rows
    of a spliced gene -- and third-party writers fill it with serials like
    ``gene1`` or ``PROKKA_00001``. Reading those as locus tags puts a synthetic
    identifier into every mutation's annotation. Nothing is lost on breseq's own
    files, which set ``Alias`` and ``ID`` to the same locus tag, nor on NCBI or
    Prokka output, which carry a real ``locus_tag``.
    """
    for key in ('accession', 'locus_tag', 'Alias'):
        if attributes.get(key):
            return _joined(attributes, key)
    return ''


def _name(attributes, accession):
    """reference_sequence.cpp:1554-1560 -- Name > gene > accession."""
    for key in ('Name', 'gene'):
        if attributes.get(key):
            return _joined(attributes, key)
    return accession


def _product(attributes, name):
    """reference_sequence.cpp:1563-1568 -- product > Note > name."""
    for key in ('product', 'Note'):
        if attributes.get(key):
            return _joined(attributes, key)
    return name


def _is_true(value):
    return str(value).strip().lower() in ('true', '1', 'yes')


def _read_rows(path):
    """Yield feature rows, then return the inline ##FASTA block."""
    rows = []
    fasta_lines = []
    in_fasta = False

    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            stripped = line.rstrip('\n').rstrip('\r')
            if not in_fasta and stripped.strip().upper() == FASTA_DIRECTIVE:
                in_fasta = True
                continue
            if in_fasta:
                fasta_lines.append(stripped)
                continue
            if not stripped or stripped.startswith('#'):
                continue
            parts = stripped.split('\t')
            if len(parts) < 9:
                continue
            try:
                start, end = int(parts[3]), int(parts[4])
            except ValueError:
                continue
            rows.append({
                'seq_id': parts[0],
                'type': parts[2],
                'start': start,
                'end': end,
                'strand': -1 if parts[6] == '-' else 1,
                'attributes': parse_attributes(parts[8]),
            })
    return rows, fasta_lines


def _parse_fasta(lines):
    sequences = []
    seq_id = None
    chunks = []
    for line in lines:
        if line.startswith('>'):
            if seq_id is not None:
                sequences.append((seq_id, ''.join(chunks)))
            header = line[1:].strip()
            seq_id = header.split()[0] if header else ''
            chunks = []
        elif line.strip():
            chunks.append(line.strip())
    if seq_id is not None:
        sequences.append((seq_id, ''.join(chunks)))
    return sequences


def _build_feature(row, promote_gene_rows=False):
    attributes = row['attributes']
    feature_type = row['type']
    pseudo = False

    if feature_type == PSEUDO_CDS_TYPE:
        feature_type = 'CDS'
        pseudo = True
    elif promote_gene_rows and feature_type == 'gene':
        # A file annotated only with `gene` rows -- some third-party GFF3s are --
        # has nothing else to offer, so treat them as coding rather than
        # returning no genes at all. breseq's own GFF3 never needs this: it
        # always writes CDS/tRNA/fCDS.
        feature_type = 'CDS' 

    if feature_type not in GENE_TYPES and feature_type not in REPEAT_TYPES:
        return None

    accession = _accession(attributes)
    name = _name(attributes, accession)
    product = _product(attributes, name)

    feature = Feature(feature_type, product=product)

    if feature.is_repeat():
        # breseq has already normalized the family name by the time it writes
        # GFF3, but a third-party file may not have.
        feature.name = trim_repeat_name(name) if name else DEFAULT_REPEAT_NAME
        feature.product = product or DEFAULT_REPEAT_PRODUCT
    else:
        feature.name = name or 'unknown'
        feature.locus_tag = accession
        feature.pseudogene = pseudo or _is_true(_first(attributes, 'Pseudo')) \
            or _is_true(_first(attributes, 'pseudo'))
        transl_table = _first(attributes, 'transl_table')
        if transl_table:
            try:
                feature.translation_table = int(transl_table)
            except ValueError:
                pass

    feature.name = make_safe(feature.name)
    feature.locus_tag = make_safe(feature.locus_tag)
    feature.product = feature.product.replace(MULTIPLE_SEPARATOR, ';')
    return feature


def _location_for(row, feature):
    indeterminate = row['attributes'].get('indeterminate_coordinate', [])
    return FeatureLocation(
        start_1=row['start'],
        end_1=row['end'],
        strand=row['strand'],
        start_is_indeterminate='start' in indeterminate,
        end_is_indeterminate='end' in indeterminate,
        feature=feature,
    )


def load_gff3(*paths):
    """
    Load one or more breseq GFF3 files into a LoadedReferenceSequences.

    A spliced gene is written as several rows sharing one `ID` and `type`; those
    are merged into a single feature whose sublocations follow file order, which
    breseq writes 5'->3' along the gene. reference_sequence.cpp:1625-1652
    """
    references = LoadedReferenceSequences()

    for path in paths:
        rows, fasta_lines = _read_rows(path)
        sequences = _parse_fasta(fasta_lines)
        if not sequences:
            raise ValueError(
                '%s carries no sequence; annotation needs the inline ##FASTA section' % path)

        contigs = {}
        for seq_id, sequence in sequences:
            contigs[seq_id] = AnnotatedSequence(seq_id, sequence.upper())

        promote_gene_rows = not any(
            row['type'] in GENE_TYPES or row['type'] == PSEUDO_CDS_TYPE for row in rows)

        # (seq_id, ID, type) -> feature, so later rows extend the same feature.
        by_id = {}
        for row in rows:
            contig = contigs.get(row['seq_id'])
            if contig is None:
                continue
            feature_id = _first(row['attributes'], 'ID')
            key = (row['seq_id'], feature_id, row['type'])

            if feature_id and key in by_id:
                feature = by_id[key]
                feature.locations.append(_location_for(row, feature))
                continue

            feature = _build_feature(row, promote_gene_rows)
            if feature is None:
                continue
            feature.locations.append(_location_for(row, feature))
            if feature_id:
                by_id[key] = feature
            contig.features.append(feature)

        for contig in contigs.values():
            contig.update_feature_lists()
            references.add(contig)

    if not references.sequences:
        raise ValueError('No GFF3 records found in: %s' % ', '.join(paths))
    return references


def looks_like_gff3(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                return stripped.startswith('##gff-version') or \
                    stripped.startswith('##sequence-region')
    except OSError:
        return False
    return False


# --- writing ------------------------------------------------------------------------
#
# The canonical stored form. Rendering the loaded model back out in breseq's own
# dialect means one artifact serves three jobs at once: it is what the store
# hashes, what igv.js draws as the gene track, and what annotation reads. A
# GenBank and breseq's GFF3 of the same genome load to equivalent models (see
# tests/test_gff3.py), so they render to identical text -- which is the property
# the shared-reference check rests on.

# Fixed order, so two inputs that agree on content agree byte for byte.
ATTRIBUTE_ORDER = ('Alias', 'ID', 'Name', 'Note', 'Pseudo', 'transl_table',
                   'indeterminate_coordinate')

GFF3_ATTRIBUTE_ESCAPES = (
    ('%', '%25'), (';', '%3B'), ('=', '%3D'), ('&', '%26'), (',', '%2C'),
    ('\t', '%09'), ('\n', '%0A'), ('\r', '%0D'),
)


def escape(value):
    """GFF3 percent-encoding. `%` first, so nothing is double-encoded."""
    for plain, encoded in GFF3_ATTRIBUTE_ESCAPES:
        value = value.replace(plain, encoded)
    return value


def _feature_id(feature, index):
    """A stable identifier, shared by every row of a spliced feature."""
    return feature.get_locus_tag() or feature.name or 'feature%d' % index


def _feature_rows(seq_id, feature, index):
    attributes = {}
    if not feature.is_repeat():
        # Rows of one feature are grouped on reload by shared ID, which is how a
        # spliced gene keeps its sublocations. Repeats deliberately carry no ID
        # -- breseq does not give them one, and every copy of an IS family shares
        # a name, so an ID would merge them into one multi-location feature and
        # update_feature_lists() drops those.
        attributes['ID'] = _feature_id(feature, index)
        if feature.get_locus_tag():
            attributes['Alias'] = feature.get_locus_tag()
    if feature.name:
        attributes['Name'] = feature.name
    if feature.product:
        attributes['Note'] = feature.product

    feature_type = feature.type
    if not feature.is_repeat():
        if feature.pseudogene:
            # breseq writes a pseudo CDS as fCDS and reads it back as CDS+Pseudo.
            if feature_type == 'CDS':
                feature_type = PSEUDO_CDS_TYPE
            attributes['Pseudo'] = 'true'
        if feature_type in ('CDS', PSEUDO_CDS_TYPE):
            attributes['transl_table'] = str(feature.translation_table)

    rows = []
    for position, location in enumerate(feature.locations):
        row_attributes = dict(attributes)
        # Only the outermost ends of the feature can be indeterminate.
        indeterminate = []
        if position == 0 and location.stranded_start_is_indeterminate():
            indeterminate.append('start')
        if position == len(feature.locations) - 1 and location.stranded_end_is_indeterminate():
            indeterminate.append('end')
        if indeterminate:
            row_attributes['indeterminate_coordinate'] = ','.join(indeterminate)

        rendered = ';'.join(
            '%s=%s' % (key, escape(row_attributes[key]))
            for key in ATTRIBUTE_ORDER if key in row_attributes)
        rows.append('\t'.join([
            seq_id, 'aledb', feature_type, str(location.start_1), str(location.end_1),
            '.', '+' if location.strand == 1 else '-', '0', rendered]))
    return rows


def _sort_key(feature):
    starts = [location.start_1 for location in feature.locations]
    return (min(starts) if starts else 0, feature.type, feature.name,
            feature.get_locus_tag())


def render_breseq_gff3(references, line_length=70):
    """Render loaded reference sequences as breseq-dialect GFF3 with inline FASTA.

    Deterministic: contigs and features are emitted in a fixed order, so the same
    genome renders to the same bytes whatever format it arrived in.
    """
    contigs = []
    seen = set()
    for seq_id in sorted(references.seq_ids()):
        contig = references[seq_id]
        if id(contig) in seen:
            continue  # a contig is registered under several aliases
        seen.add(id(contig))
        contigs.append(contig)
    contigs.sort(key=lambda contig: contig.seq_id)

    lines = ['##gff-version 3']
    for contig in contigs:
        lines.append('##sequence-region\t%s\t1\t%d' % (contig.seq_id, len(contig)))

    for contig in contigs:
        features = [f for f in contig.features if f.locations]
        features.sort(key=_sort_key)
        for index, feature in enumerate(features, start=1):
            lines.extend(_feature_rows(contig.seq_id, feature, index))

    lines.append('##FASTA')
    for contig in contigs:
        lines.append('>%s' % contig.seq_id)
        for offset in range(0, len(contig.sequence), line_length):
            lines.append(contig.sequence[offset:offset + line_length])
    return '\n'.join(lines) + '\n'
