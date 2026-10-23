"""
GenBank -> the annotation feature model.

Biopython does the record, location and sequence parsing. What it does not do is
breseq's feature post-processing -- name precedence, IS-name trimming, the
repeat/gene split, separator escaping -- so that layer is ported explicitly from
reference_sequence.cpp:2397-2508.

What the model does not interpret is kept rather than dropped: every qualifier that fed no
field rides in `Feature.attributes`, every feature type the file carries is loaded (a `gene`
is folded into the feature under it, a `source` into the record's `metadata`), and the
header's DEFINITION, ORGANISM and COMMENT go to `AnnotatedSequence.metadata`. The stored
GFF3 carries all of it and the GenBank export writes it back.
"""

import os
import warnings

from Bio import BiopythonParserWarning, SeqIO

from mutint_import.annotate.model import (
    DEFAULT_REPEAT_NAME,
    DEFAULT_REPEAT_PRODUCT,
    GENE_TYPES,
    MULTIPLE_SEPARATOR,
    NEVER_PSEUDO_TYPES,
    REPEAT_TYPES,
    UNSTORABLE_ATTRIBUTES,
    AnnotatedSequence,
    Feature,
    FeatureLocation,
    LoadedReferenceSequences,
    fold_gene_features,
    safe_seq_id,
    make_safe,
    trim_repeat_name,
)

#: Feature types that describe the record rather than a place on it. A GenBank's `source`
#: becomes the contig's `metadata`; the GFF3 `region` row is its stored form.
RECORD_LEVEL_TYPES = frozenset(('source', 'region'))

#: Record annotations kept in `metadata`, as (Biopython key, stored key). DEFINITION is
#: stored as `Note`, which is what breseq reads a region row's Note as.
RECORD_ANNOTATIONS = (
    ('organism', 'organism'),
    ('taxonomy', 'taxonomy'),
    ('comment', 'comment'),
    ('date', 'date'),
    ('keywords', 'keywords'),
    ('data_file_division', 'division'),
)

#: What Biopython writes for a header line it was not given; not worth keeping.
PLACEHOLDERS = frozenset(('', '.', 'UNK', '<unknown description>', '<unknown name>',
                          '<unknown id>', '01-JAN-1980'))


def _first_qualifier(feature, key):
    values = feature.qualifiers.get(key)
    if not values:
        return ''
    value = values[0]
    return '' if value is None else str(value)


def _values(values):
    """A qualifier's values as strings, on one line each; a bare `/pseudo` is `['']`.

    Biopython joins a wrapped qualifier's lines with spaces already; a COMMENT keeps its
    line breaks, which would be `%0A` in the GFF3 -- an escape breseq's own reader does
    not know, so it would hand the file back as `%250A`. One line each, then.
    """
    if not isinstance(values, (list, tuple)):
        values = [values]
    return [' '.join(str(value).split('\n')) if value is not None else '' for value in values]


def _other_qualifiers(bio_feature, consumed):
    """Every qualifier but `consumed`, as `{key: [values]}` in the file's order.

    A bare qualifier (`/ribosomal_slippage`) is an empty value, written `key=` in the
    GFF3 and bare again on export. `UNSTORABLE_ATTRIBUTES` -- `translation`, which the
    export regenerates, and the names a loader would misread -- are left out here too, so
    the model reads the same from a GenBank as from its stored GFF3.
    """
    attributes = {}
    for key, values in bio_feature.qualifiers.items():
        if key in consumed or key in UNSTORABLE_ATTRIBUTES:
            continue
        attributes[key] = _values(values)
    return attributes


def _locations_from_biopython(bio_location, feature):
    """
    Biopython's location model -> breseq's sublocation list.

    A CompoundLocation already yields its parts 5'->3' in the feature's own
    orientation, which is what breseq builds by reversing after complementing.
    """
    from Bio.SeqFeature import AfterPosition, BeforePosition

    locations = []
    for part in bio_location.parts:
        strand = -1 if part.strand == -1 else 1
        locations.append(FeatureLocation(
            start_1=int(part.start) + 1,
            end_1=int(part.end),
            strand=strand,
            start_is_indeterminate=isinstance(part.start, BeforePosition),
            end_is_indeterminate=isinstance(part.end, AfterPosition),
            feature=feature,
        ))
    return locations


def _build_feature(bio_feature, promoted_type=None):
    feature_type = promoted_type or bio_feature.type
    if feature_type in RECORD_LEVEL_TYPES:
        return None

    product = _first_qualifier(bio_feature, 'product')
    product_key = 'product' if product else ''
    if not product:
        product = _first_qualifier(bio_feature, 'note')
        product_key = 'note' if product else ''

    feature = Feature(feature_type, product=product)

    if feature.is_repeat():
        name = DEFAULT_REPEAT_NAME
        name_key = ''
        mobile_element = (_first_qualifier(bio_feature, 'mobile_element')
                          or _first_qualifier(bio_feature, 'mobile_element_type'))
        if mobile_element:
            name = trim_repeat_name(mobile_element)
            name_key = ('mobile_element' if _first_qualifier(bio_feature, 'mobile_element')
                        else 'mobile_element_type')
        else:
            # Each fallback only applies while the name is still the default. `name` is
            # what breseq's own GenBank writer calls the element and what its reader puts
            # straight into the feature's name (reference_sequence.cpp:2301-2358).
            for qualifier in ('name', 'rpt_family', 'rpt_type', 'label', 'note'):
                value = _first_qualifier(bio_feature, qualifier)
                if value:
                    # Trimmed as the GFF3 loader trims a `Name`, so the two agree on
                    # what ISEScan's `IS3_176` is called: the family, `IS3`.
                    name = trim_repeat_name(value)
                    name_key = qualifier
                    break
        feature.name = name
        feature.product = product or DEFAULT_REPEAT_PRODUCT
        # /pseudo on a mobile_element marks a partial IS, as breseq's flag_pseudo() does; a
        # repeat_region is never pseudo. reference_sequence.h:528-537
        if feature_type not in NEVER_PSEUDO_TYPES:
            feature.pseudogene = 'pseudo' in bio_feature.qualifiers
        consumed = {name_key, product_key, 'pseudo'}
    elif feature.is_gene():
        # name > gene > locus_tag > label > note, breseq's chain for anything not a repeat.
        for qualifier in ('name', 'gene', 'locus_tag', 'label', 'note'):
            value = _first_qualifier(bio_feature, qualifier)
            if value:
                feature.name = value
                break
        else:
            feature.name = 'unknown'
        feature.locus_tag = _first_qualifier(bio_feature, 'locus_tag')
        feature.pseudogene = 'pseudo' in bio_feature.qualifiers
        transl_table = _first_qualifier(bio_feature, 'transl_table')
        if transl_table:
            try:
                feature.translation_table = int(transl_table)
            except ValueError:
                pass
        # `note` is kept even where it fed the product: NCBI's CDS carry both, and a
        # relabelled note would come back as a product the file never had.
        consumed = {'name', 'gene', 'locus_tag', 'product', 'pseudo', 'transl_table'}
    else:
        # Any other type -- regulatory, misc_feature, a gene with no feature under
        # it. Kept whole for the file, never annotated against, so no fallbacks: a name
        # only where the file gave one, a product only from `/product`.
        feature.name = (_first_qualifier(bio_feature, 'gene')
                        or _first_qualifier(bio_feature, 'locus_tag'))
        feature.locus_tag = _first_qualifier(bio_feature, 'locus_tag')
        feature.product = _first_qualifier(bio_feature, 'product')
        if feature_type not in NEVER_PSEUDO_TYPES:
            feature.pseudogene = 'pseudo' in bio_feature.qualifiers
        consumed = {'gene', 'locus_tag', 'product', 'pseudo'}

    feature.name = make_safe(feature.name)
    feature.locus_tag = make_safe(feature.locus_tag)
    feature.product = feature.product.replace(MULTIPLE_SEPARATOR, ';')
    feature.attributes = _other_qualifiers(bio_feature, consumed)

    feature.locations = _locations_from_biopython(bio_feature.location, feature)
    return feature


def parse_records(path):
    """
    Parse a GenBank file, quietly.

    breseq-produced references routinely have truncated LOCUS lines and
    lower-case molecule types, which Biopython warns about and then handles
    correctly anyway. Those warnings would be noise in the upload log.
    """
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', BiopythonParserWarning)
        return list(SeqIO.parse(path, 'genbank'))


def _record_seq_id(record):
    """The seq_id breseq would give this record: the LOCUS name.

    Biopython puts the LOCUS name in ``record.name`` and the VERSION accession in
    ``record.id``, so a record for E. coli K-12 is ``NC_000913`` and ``NC_000913.3``
    respectively. breseq takes the LOCUS name (reference_sequence.cpp
    ``LoadGenBankFileHeader``), and every seq_id in a .gd it writes is therefore the
    unversioned one. Taking the VERSION here made a GenBank and breseq's own GFF3 of
    the same genome disagree about what its contigs are called, which is exactly the
    disagreement ``LoadedReferenceSequences.add`` refuses to paper over.

    The VERSION is kept as an alias in ``load_genbank`` so a lookup by either name
    still resolves; only the canonical name changes.
    """
    for candidate in (record.name, record.id):
        # Biopython fills these with '<unknown name>'/'<unknown id>' rather than ''.
        if candidate and not candidate.startswith('<unknown'):
            return candidate
    return ''


TOPOLOGIES = {'circular': True, 'linear': False}


def _record_topology(record):
    """The LOCUS line's topology as True (circular), False (linear) or None (not said).

    Biopython keeps it in ``record.annotations['topology']``; a LOCUS line whose field is
    blank leaves the key out. None is kept distinct from False so a file that said
    nothing is not stored as having said linear -- the page marks it *suggested*.
    """
    value = (record.annotations or {}).get('topology') or ''
    return TOPOLOGIES.get(str(value).strip().lower())


def _record_metadata(record):
    """`AnnotatedSequence.metadata` for a record: its header and its `source` feature.

    DEFINITION first, as `Note`; then the `source` feature's qualifiers in the file's
    order (`organism`, `mol_type`, `strain`, `db_xref`...); then the header lines that
    are not already there. Placeholders Biopython invents for a header it was not given
    are left out, so a file that said nothing stores nothing and renders no region row.
    """
    metadata = {}
    description = (record.description or '').strip()
    # The export writes the contig's own name as DEFINITION when it knows nothing
    # better, so that is not a definition either.
    if description not in PLACEHOLDERS and description not in (record.name, record.id):
        metadata['Note'] = [description]

    for bio_feature in record.features:
        if bio_feature.type == 'source':
            for key, values in bio_feature.qualifiers.items():
                values = [value for value in _values(values) if value not in PLACEHOLDERS]
                if values and key not in metadata:
                    metadata[key] = values
            break

    annotations = record.annotations or {}
    for biopython_key, key in RECORD_ANNOTATIONS:
        values = annotations.get(biopython_key)
        values = [str(value) for value in _values(values) if str(value).strip()
                  and str(value).strip() not in PLACEHOLDERS] if values else []
        if values and key not in metadata:
            metadata[key] = values
    return metadata


def load_genbank(*paths):
    """Load one or more GenBank files into a LoadedReferenceSequences."""
    references = LoadedReferenceSequences()

    for path in paths:
        for record in parse_records(path):
            seq_id = references.breseq_name(_record_seq_id(record))
            annotated = AnnotatedSequence(seq_id, str(record.seq).upper(),
                                          circular=_record_topology(record),
                                          metadata=_record_metadata(record))

            # A file annotated only with `gene` features has nothing else to
            # offer; treat them as coding rather than returning no genes at all.
            has_real_genes = any(f.type in GENE_TYPES for f in record.features)

            for bio_feature in record.features:
                promoted = None
                if not has_real_genes and bio_feature.type == 'gene':
                    promoted = 'CDS'
                feature = _build_feature(bio_feature, promoted_type=promoted)
                if feature is not None:
                    annotated.features.append(feature)
            # A GenBank locus is a `gene` over a CDS or RNA at the same coordinates; the
            # gene's labels and qualifiers go onto that feature and the row is not kept.
            annotated.features = fold_gene_features(annotated.features)
            annotated.update_feature_lists()
            references.add(annotated)
            # The accession stays reachable under its versioned name, so a GFF3 or .gd
            # that spells the contig `NC_000913.3` still finds it. Alias only: the
            # rendered GFF3 and the stored seq_ids come from `annotated.seq_id`.
            version = record.id
            if version and not version.startswith('<unknown'):
                version = safe_seq_id(version)
                if version not in references.sequences:
                    references.sequences[version] = annotated

    if not references.sequences:
        raise ValueError('No GenBank records found in: %s' % ', '.join(paths))
    return references


def read_seq_ids(path):
    """Just the contig ids a GenBank file defines, as `load_genbank` would store them."""
    return [safe_seq_id(_record_seq_id(record)) for record in parse_records(path)]


def looks_like_genbank(path):
    if not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', errors='replace') as handle:
            for line in handle:
                if line.strip():
                    return line.startswith('LOCUS')
    except OSError:
        return False
    return False
