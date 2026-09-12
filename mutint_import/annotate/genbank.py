"""
GenBank -> the annotation feature model.

Biopython does the record, location and sequence parsing. What it does not do is
breseq's feature post-processing -- name precedence, IS-name trimming, the
repeat/gene split, separator escaping -- so that layer is ported explicitly from
reference_sequence.cpp:2397-2508.
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
    AnnotatedSequence,
    Feature,
    FeatureLocation,
    LoadedReferenceSequences,
    make_safe,
    trim_repeat_name,
)


def _first_qualifier(feature, key):
    values = feature.qualifiers.get(key)
    if not values:
        return ''
    value = values[0]
    return '' if value is None else str(value)


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
    if feature_type not in GENE_TYPES and feature_type not in REPEAT_TYPES:
        return None

    product = _first_qualifier(bio_feature, 'product')
    if not product:
        product = _first_qualifier(bio_feature, 'note')

    feature = Feature(feature_type, product=product)

    if feature.is_repeat():
        name = DEFAULT_REPEAT_NAME
        mobile_element = (_first_qualifier(bio_feature, 'mobile_element')
                          or _first_qualifier(bio_feature, 'mobile_element_type'))
        if mobile_element:
            name = trim_repeat_name(mobile_element)
        else:
            # Each fallback only applies while the name is still the default.
            for qualifier in ('rpt_family', 'rpt_type', 'label', 'note'):
                value = _first_qualifier(bio_feature, qualifier)
                if value:
                    name = value
                    break
        feature.name = name
        feature.product = product or DEFAULT_REPEAT_PRODUCT
        # /pseudo on a mobile_element marks a partial IS, as breseq's flag_pseudo() does; a
        # repeat_region is never pseudo. reference_sequence.h:528-537
        if feature_type not in NEVER_PSEUDO_TYPES:
            feature.pseudogene = 'pseudo' in bio_feature.qualifiers
    else:
        feature.name = (_first_qualifier(bio_feature, 'gene')
                        or _first_qualifier(bio_feature, 'locus_tag')
                        or _first_qualifier(bio_feature, 'label')
                        or _first_qualifier(bio_feature, 'note')
                        or 'unknown')
        feature.locus_tag = _first_qualifier(bio_feature, 'locus_tag')
        feature.pseudogene = 'pseudo' in bio_feature.qualifiers
        transl_table = _first_qualifier(bio_feature, 'transl_table')
        if transl_table:
            try:
                feature.translation_table = int(transl_table)
            except ValueError:
                pass

    feature.name = make_safe(feature.name)
    feature.locus_tag = make_safe(feature.locus_tag)
    feature.product = feature.product.replace(MULTIPLE_SEPARATOR, ';')

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


def _gene_feature_labels(bio_features):
    """``(start, end, strand) -> (name, locus_tag)`` from plain ``gene`` features.

    A GenBank locus is normally two features: a ``gene`` and a ``CDS`` at the same
    coordinates. breseq annotates against the CDS and ignores the gene, which is
    right when the CDS repeats /gene and /locus_tag -- but some files put the name
    only on the gene feature, and then the CDS would come through as "unknown".
    """
    labels = {}
    for bio_feature in bio_features:
        if bio_feature.type != 'gene':
            continue
        key = (int(bio_feature.location.start), int(bio_feature.location.end),
               bio_feature.location.strand)
        labels.setdefault(key, (_first_qualifier(bio_feature, 'gene'),
                                _first_qualifier(bio_feature, 'locus_tag')))
    return labels


def _borrow_labels(feature, bio_feature, labels):
    """Fill a gene's missing name or locus tag from the gene feature beside it."""
    key = (int(bio_feature.location.start), int(bio_feature.location.end),
           bio_feature.location.strand)
    name, locus_tag = labels.get(key, ('', ''))
    if name and feature.name == 'unknown':
        feature.name = make_safe(name)
    if locus_tag and not feature.locus_tag:
        feature.locus_tag = make_safe(locus_tag)
        if feature.name == 'unknown':
            feature.name = make_safe(locus_tag)


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


def load_genbank(*paths):
    """Load one or more GenBank files into a LoadedReferenceSequences."""
    references = LoadedReferenceSequences()

    for path in paths:
        for record in parse_records(path):
            seq_id = _record_seq_id(record)
            annotated = AnnotatedSequence(seq_id, str(record.seq).upper())
            labels = _gene_feature_labels(record.features)

            # A file annotated only with `gene` features has nothing else to
            # offer; treat them as coding rather than returning no genes at all.
            has_real_genes = any(f.type in GENE_TYPES for f in record.features)

            for bio_feature in record.features:
                promoted = None
                if not has_real_genes and bio_feature.type == 'gene':
                    promoted = 'CDS'
                feature = _build_feature(bio_feature, promoted_type=promoted)
                if feature is not None:
                    if not feature.is_repeat():
                        _borrow_labels(feature, bio_feature, labels)
                    annotated.features.append(feature)
            annotated.update_feature_lists()
            references.add(annotated)
            # The accession stays reachable under its versioned name, so a GFF3 or .gd
            # that spells the contig `NC_000913.3` still finds it. Alias only: the
            # rendered GFF3 and the stored seq_ids come from `annotated.seq_id`.
            version = record.id
            if (version and not version.startswith('<unknown')
                    and version not in references.sequences):
                references.sequences[version] = annotated

    if not references.sequences:
        raise ValueError('No GenBank records found in: %s' % ', '.join(paths))
    return references


def read_seq_ids(path):
    """Just the contig ids a GenBank file defines."""
    return [_record_seq_id(record) for record in parse_records(path)]


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
