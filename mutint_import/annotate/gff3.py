"""
breseq's GFF3 -> the annotation feature model.

A breseq run writes its reference as `data/reference.gff3`, and that file carries
everything annotation needs: CDS/tRNA/rRNA types, locus tags, translation
tables, pseudogene flags, repeat regions, spliced genes and indeterminate ends.
It is therefore the natural annotation input for a breseq folder import -- no
GenBank required.

Ported from cReferenceSequences::ReadGFF (reference_sequence.cpp:1490-1660).
Note this is a *different* reader from `mutint_import.reference.read_gff3`, which reads
rows and sequences as text for the store's checks and builds no model.

What the model does not interpret is kept rather than dropped -- a row's other attributes
in `Feature.attributes`, a row of any other type as a feature of that type, a `region` row's
other attributes as the contig's `metadata` -- and `render_breseq_gff3` writes it all back,
so the stored form loses nothing a GenBank said. breseq's own reader keeps and rewrites
every attribute too (reference_sequence.cpp:1570-1586, :1758-1771).
"""

from mutint_import.annotate.model import (
    DEFAULT_REPEAT_NAME,
    DEFAULT_REPEAT_PRODUCT,
    GENE_TYPES,
    MULTIPLE_SEPARATOR,
    NEVER_PSEUDO_TYPES,
    REPEAT_TYPES,
    ATTRIBUTE_RENAMES,
    UNSTORABLE_ATTRIBUTES,
    AnnotatedSequence,
    Feature,
    FeatureLocation,
    LoadedReferenceSequences,
    fold_gene_features,
    make_safe,
    trim_repeat_name,
)

FASTA_DIRECTIVE = '##FASTA'

# breseq writes a pseudogene as "fCDS" -- a fragment CDS -- and reads it back as
# a CDS with the pseudo flag set. reference_sequence.cpp:1571-1575
PSEUDO_CDS_TYPE = 'fCDS'

# One `region` row per sequence carries its topology, as breseq's own GFF3 does
# (`Is_circular=true`). Read into `AnnotatedSequence.circular` and written back by
# `render_breseq_gff3` only for a contig whose topology is known; never a feature.
REGION_TYPE = 'region'
CIRCULAR_ATTRIBUTE = 'Is_circular'
REGION_SOURCE = 'mutint'


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


def _extra_attributes(attributes):
    """The row's attributes the model does not interpret, under their stored names."""
    extras = {}
    for key, values in attributes.items():
        key = ATTRIBUTE_RENAMES.get(key, key)
        if key in UNSTORABLE_ATTRIBUTES:
            continue
        extras[key] = list(values)  # `key=` is a bare GenBank qualifier, kept as ['']
    return extras


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

    if feature_type == REGION_TYPE:
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
        # A partial IS element is a pseudo mobile_element -- breseq's ReadISEScan flags it,
        # and repeat_family_sequence skips it when picking a family's consensus. Never for a
        # repeat_region, which flag_pseudo() leaves alone. reference_sequence.h:528-537
        if feature_type not in NEVER_PSEUDO_TYPES:
            feature.pseudogene = _is_true(_first(attributes, 'Pseudo')) \
                or _is_true(_first(attributes, 'pseudo'))
    elif feature.is_gene():
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
    else:
        # Any other type is kept for the file and never annotated against, so it takes
        # no fallbacks: a name only where the row names one, a product only from
        # `product`/`Note`. The GenBank loader builds these the same way.
        feature.name = _joined(attributes, 'Name') if attributes.get('Name') else (
            _joined(attributes, 'gene') if attributes.get('gene') else accession)
        feature.locus_tag = accession
        feature.product = _joined(attributes, 'product') if attributes.get('product') else (
            _joined(attributes, 'Note') if attributes.get('Note') else '')
        if feature_type not in NEVER_PSEUDO_TYPES:
            feature.pseudogene = _is_true(_first(attributes, 'Pseudo')) \
                or _is_true(_first(attributes, 'pseudo'))

    feature.name = make_safe(feature.name)
    feature.locus_tag = make_safe(feature.locus_tag)
    feature.product = feature.product.replace(MULTIPLE_SEPARATOR, ';')
    feature.attributes = _extra_attributes(attributes)
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

        # Feature rows name the contig as the file spells it; `names` maps that spelling
        # onto the one breseq would store, so a row still finds its contig after the
        # rename. breseq's own GFF3 is already safe and maps every name onto itself.
        names = {}
        contigs = {}
        for seq_id, sequence in sequences:
            safe = references.breseq_name(seq_id)
            names[seq_id] = safe
            contigs[safe] = AnnotatedSequence(safe, sequence.upper())
        for row in rows:
            row['seq_id'] = names.get(row['seq_id'], row['seq_id'])

        # breseq writes one `region` row per sequence carrying `Is_circular`, and so does
        # the renderer below. It is topology, not a feature: `_build_feature` drops the
        # row, and this is the one thing read off it. A region row with no `Is_circular`
        # leaves the contig at None, the same as no region row at all.
        # Every other attribute of the region row is the record's own -- its DEFINITION
        # as `Note`, organism, strain, comment -- and goes to `metadata`.
        for row in rows:
            if row['type'] != REGION_TYPE:
                continue
            contig = contigs.get(row['seq_id'])
            if contig is None:
                continue
            flag = _first(row['attributes'], CIRCULAR_ATTRIBUTE)
            if flag:
                contig.circular = _is_true(flag)
            for key, values in row['attributes'].items():
                if key in (CIRCULAR_ATTRIBUTE, 'ID'):
                    continue
                values = [value for value in values if value != '']
                if values and key not in contig.metadata:
                    contig.metadata[key] = values

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
            contig.features = fold_gene_features(contig.features)
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


def _extra_keys(attributes):
    """The storable keys of a `Feature.attributes` / `metadata` dict, sorted."""
    return sorted(key for key in attributes
                  if key not in UNSTORABLE_ATTRIBUTES and _is_attribute_key(key))


def _is_attribute_key(key):
    return bool(key) and not any(c in key for c in ';=,\t\n\r ') and not key.startswith('#')


def _escape_value(value):
    """An attribute value: a list is several values joined by an unescaped comma."""
    if isinstance(value, (list, tuple)):
        return ','.join(escape(part) for part in value)
    return escape(value)


def _feature_ids(features):
    """One `ID` per feature, unique within the contig: the locus tag, else the name, else
    `feature<n>`; a locus tag or name that two features share takes `_<n>` after it.

    Unique because the reader -- this module's, and breseq's -- merges every row sharing
    an ID into one spliced feature. A genome whose genes carry only `/product` loads every
    one as `unknown`, and written under that one ID the whole contig came back as a single
    CDS with hundreds of sublocations; two copies of `insA` with no locus tags merged the
    same way. `n` is the feature's position in the contig's sorted order, so the same
    annotation renders the same bytes every time.
    """
    wanted = []
    for feature in features:
        name = feature.name if feature.name and feature.name != 'unknown' else ''
        wanted.append(feature.get_locus_tag() or name)
    shared = {value for value in wanted if wanted.count(value) > 1}
    ids, used = [], set()
    for index, value in enumerate(wanted, start=1):
        candidate = value if value and value not in shared else (
            '%s_%d' % (value, index) if value else 'feature%d' % index)
        while candidate in used:
            candidate += '_'
        used.add(candidate)
        ids.append(candidate)
    return ids


def _feature_rows(seq_id, feature, feature_id):
    attributes = {}
    if not feature.is_repeat():
        # Rows of one feature are grouped on reload by shared ID, which is how a
        # spliced gene keeps its sublocations. Repeats deliberately carry no ID
        # -- breseq does not give them one, and every copy of an IS family shares
        # a name, so an ID would merge them into one multi-location feature and
        # update_feature_lists() drops those.
        attributes['ID'] = feature_id
        if feature.get_locus_tag():
            attributes['Alias'] = feature.get_locus_tag()
    if feature.name:
        attributes['Name'] = feature.name
    if feature.product:
        attributes['Note'] = feature.product

    feature_type = feature.type
    # Pseudo for a gene and for a partial mobile_element alike: breseq writes and reads it for
    # any feature type its flag_pseudo() applies to, and a repeat_region is never one.
    if feature.pseudogene and feature_type not in NEVER_PSEUDO_TYPES:
        # breseq writes a pseudo CDS as fCDS and reads it back as CDS+Pseudo.
        if feature_type == 'CDS':
            feature_type = PSEUDO_CDS_TYPE
        attributes['Pseudo'] = 'true'
    if not feature.is_repeat() and feature_type in ('CDS', PSEUDO_CDS_TYPE):
        attributes['transl_table'] = str(feature.translation_table)

    # What the model does not interpret, after what it does, sorted so two files that
    # agree on content agree byte for byte. breseq keeps and rewrites every one of them.
    for key in _extra_keys(feature.attributes):
        attributes[key] = list(feature.attributes[key])

    rows = []
    for location in feature.locations:
        row_attributes = dict(attributes)
        # Positional, per sublocation, as breseq writes and reads it
        # (reference_sequence.cpp, the GFF3 writer and ReadGFF): `start` is the low
        # coordinate whatever the strand, which is also what the model's flags, the GenBank
        # loader and the GenBank export all mean. The stranded accessors are the
        # annotator's question (which end is 5'), not the file's. A list, so the comma
        # between the two words is a GFF3 value separator and not escaped: the loader
        # splits on `,` before unescaping, and `start%2Cend` would match neither word.
        indeterminate = []
        if location.start_is_indeterminate:
            indeterminate.append('start')
        if location.end_is_indeterminate:
            indeterminate.append('end')
        if indeterminate:
            row_attributes['indeterminate_coordinate'] = indeterminate

        rendered = ';'.join(
            '%s=%s' % (key, _escape_value(row_attributes[key]))
            for key in list(ATTRIBUTE_ORDER) + _extra_keys(feature.attributes)
            if key in row_attributes)
        rows.append('\t'.join([
            seq_id, 'mutint', feature_type, str(location.start_1), str(location.end_1),
            '.', '+' if location.strand == 1 else '-', '0', rendered]))
    return rows


def _sort_key(feature):
    starts = [location.start_1 for location in feature.locations]
    return (min(starts) if starts else 0, feature.type, feature.name,
            feature.get_locus_tag())


def region_rows(regions):
    """The `region` rows for ``[(seq_id, length, circular[, metadata]), ...]``.

    One per contig that has something to say -- a known topology, or any record-level
    metadata -- and nothing for one with neither. `Is_circular` first, where known, then
    `Note` (the DEFINITION, which is what breseq reads a region row's Note as), then the
    rest of `metadata` sorted by key. One shape, used by the renderer and by
    `reference.with_topology`, so the stored file and a rewrite of it cannot spell the row
    two ways.
    """
    rows = []
    for entry in regions:
        seq_id, length, circular = entry[:3]
        metadata = entry[3] if len(entry) > 3 and entry[3] else {}
        attributes = []
        if circular is not None:
            attributes.append((CIRCULAR_ATTRIBUTE, 'true' if circular else 'false'))
        if metadata.get('Note'):
            attributes.append(('Note', list(metadata['Note'])))
        for key in _extra_keys(metadata):
            attributes.append((key, list(metadata[key])))
        if not attributes:
            continue
        rows.append('\t'.join((
            seq_id, REGION_SOURCE, REGION_TYPE, '1', str(length), '.', '+', '.',
            ';'.join('%s=%s' % (key, _escape_value(value)) for key, value in attributes))))
    return rows


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
    # The region block: one row per contig with a known topology or any record-level
    # metadata, straight after the pragmas and before any feature, so
    # `reference.with_topology` can rewrite it line-wise and reproduce these bytes
    # exactly. Nothing for a contig with neither.
    lines.extend(region_rows(
        (contig.seq_id, len(contig), contig.circular, contig.metadata) for contig in contigs))

    for contig in contigs:
        features = [f for f in contig.features if f.locations]
        features.sort(key=_sort_key)
        for feature, feature_id in zip(features, _feature_ids(features)):
            lines.extend(_feature_rows(contig.seq_id, feature, feature_id))

    lines.append('##FASTA')
    for contig in contigs:
        lines.append('>%s' % contig.seq_id)
        for offset in range(0, len(contig.sequence), line_length):
            lines.append(contig.sequence[offset:offset + line_length])
    return '\n'.join(lines) + '\n'
