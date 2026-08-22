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
    ReferenceSequences,
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
    """reference_sequence.cpp:1544-1551 -- accession > locus_tag > ID > Alias."""
    for key in ('accession', 'locus_tag', 'ID', 'Alias'):
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


def _build_feature(row):
    attributes = row['attributes']
    feature_type = row['type']
    pseudo = False

    if feature_type == PSEUDO_CDS_TYPE:
        feature_type = 'CDS'
        pseudo = True

    if feature_type not in GENE_TYPES and feature_type not in REPEAT_TYPES:
        return None

    accession = _accession(attributes)
    name = _name(attributes, accession)
    product = _product(attributes, name)

    feature = Feature(feature_type, product=product)

    if feature.is_repeat():
        # breseq has already normalised the family name by the time it writes
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
    Load one or more breseq GFF3 files into a ReferenceSequences.

    A spliced gene is written as several rows sharing one `ID` and `type`; those
    are merged into a single feature whose sublocations follow file order, which
    breseq writes 5'->3' along the gene. reference_sequence.cpp:1625-1652
    """
    references = ReferenceSequences()

    for path in paths:
        rows, fasta_lines = _read_rows(path)
        sequences = _parse_fasta(fasta_lines)
        if not sequences:
            raise ValueError(
                '%s carries no sequence; annotation needs the inline ##FASTA section' % path)

        contigs = {}
        for seq_id, sequence in sequences:
            contigs[seq_id] = AnnotatedSequence(seq_id, sequence.upper())

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

            feature = _build_feature(row)
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
