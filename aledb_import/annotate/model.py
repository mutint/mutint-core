"""
The reference-feature model breseq annotates against.

Both loaders -- GenBank via Biopython, and breseq's own GFF3 -- build these same
objects, so the annotator never has to care which format a reference arrived in.

Coordinates are 1-based inclusive throughout, like breseq and like the GD format,
not Biopython's 0-based half-open.
"""

from aledb_import.annotate.codon_tables import DEFAULT_TRANSLATION_TABLE

# Separators that must not appear inside a feature name or locus tag, because
# annotation joins lists on them. reference_sequence.cpp:29-57
INTERGENIC_SEPARATOR = '/'
GENE_LIST_SEPARATOR = ','
MULTIPLE_SEPARATOR = '|'

# reference_sequence.cpp:711-773 -- only these become "genes" for annotation.
# Plain `gene` features are deliberately excluded; breseq annotates against the
# CDS/RNA features, and counting both would double every gene.
GENE_TYPES = frozenset(('CDS', 'rRNA', 'tRNA', 'ncRNA', 'RNA'))
REPEAT_TYPES = frozenset(('repeat_region', 'mobile_element'))

# flag_pseudo() is a no-op for these. reference_sequence.h:528-537
NEVER_PSEUDO_TYPES = frozenset(('region', 'source', 'repeat_region'))

DEFAULT_REPEAT_NAME = 'repeat_region'
DEFAULT_REPEAT_PRODUCT = 'repeat region'
INSERTION_SEQUENCE_PREFIX = 'insertion sequence:'

_COMPLEMENT = str.maketrans('ACGTacgtNn', 'TGCAtgcaNn')


def reverse_complement(sequence):
    return sequence.translate(_COMPLEMENT)[::-1]


def make_safe(name):
    """reference_sequence.cpp:139-160 -- keep list separators out of names."""
    for separator in (INTERGENIC_SEPARATOR, MULTIPLE_SEPARATOR, GENE_LIST_SEPARATOR):
        name = name.replace(separator, '_')
    return name


def trim_repeat_name(name):
    """
    E. coli mobile_element names carry a prefix and a copy suffix that breseq
    strips so that IS186B and IS186 are the same repeat family.
    reference_sequence.cpp:2418-2434
    """
    position = name.find(INSERTION_SEQUENCE_PREFIX)
    if position != -1:
        # breseq passes (pos, pos+19) to std::string::erase, which is
        # (index, count) -- so it removes the prefix when it is at the front and
        # overshoots otherwise. Reproduced rather than corrected.
        name = name[:position] + name[position + position + len(INSERTION_SEQUENCE_PREFIX):]

    first_digit = next((i for i, c in enumerate(name) if c.isdigit()), -1)
    if first_digit != -1:
        after_digits = next(
            (i for i in range(first_digit, len(name)) if not name[i].isdigit()), -1)
        if after_digits != -1:
            name = name[:after_digits]
    return name


class FeatureLocation(object):
    """One sublocation of a feature. 1-based inclusive."""

    __slots__ = ('start_1', 'end_1', 'strand', 'start_is_indeterminate',
                 'end_is_indeterminate', 'feature')

    def __init__(self, start_1, end_1, strand,
                 start_is_indeterminate=False, end_is_indeterminate=False, feature=None):
        self.start_1 = start_1
        self.end_1 = end_1
        self.strand = strand
        self.start_is_indeterminate = start_is_indeterminate
        self.end_is_indeterminate = end_is_indeterminate
        self.feature = feature

    def is_top_strand(self):
        return self.strand == 1

    def distance_to_position(self, position_1):
        """0 when the position falls inside this location. reference_sequence.h:292-298"""
        if position_1 < self.start_1:
            return self.start_1 - position_1
        if position_1 > self.end_1:
            return position_1 - self.end_1
        return 0

    def stranded_start_is_indeterminate(self):
        """reference_sequence.h:236-242 -- which end is 5' depends on the strand."""
        return self.start_is_indeterminate if self.strand == 1 else self.end_is_indeterminate

    def stranded_end_is_indeterminate(self):
        return self.end_is_indeterminate if self.strand == 1 else self.start_is_indeterminate

    def sort_key(self):
        return (self.start_1, self.end_1)

    def __repr__(self):
        return 'FeatureLocation(%d..%d, strand=%d)' % (self.start_1, self.end_1, self.strand)


class Feature(object):
    """A gene (CDS/RNA) or repeat region, with its sublocations in 5'->3' order."""

    def __init__(self, feature_type, name='', product='', locus_tag='',
                 pseudogene=False, translation_table=DEFAULT_TRANSLATION_TABLE):
        self.type = feature_type
        self.name = name
        self.product = product
        self.locus_tag = locus_tag
        self.pseudogene = pseudogene
        self.translation_table = translation_table
        self.locations = []

    def is_gene(self):
        return self.type in GENE_TYPES

    def is_repeat(self):
        return self.type in REPEAT_TYPES

    def flag_pseudo(self):
        if self.type in NEVER_PSEUDO_TYPES:
            return
        self.pseudogene = True

    def get_locus_tag(self):
        return self.locus_tag

    def start_is_indeterminate(self):
        """Feature-level: the 5' end of the first sublocation. reference_sequence.h:557-572"""
        if not self.locations:
            return False
        return self.locations[0].stranded_start_is_indeterminate()

    def end_is_indeterminate(self):
        if not self.locations:
            return False
        return self.locations[-1].stranded_end_is_indeterminate()

    def get_length(self):
        """Spliced length, summed over sublocations. reference_sequence.h:551-558"""
        return sum(loc.end_1 - loc.start_1 + 1 for loc in self.locations)

    def get_nucleotide_sequence(self, annotated_sequence):
        """Spliced and strand-oriented, so it reads 5'->3' in the gene's own frame."""
        return ''.join(
            annotated_sequence.get_stranded_sequence_1(loc.strand, loc.start_1, loc.end_1)
            for loc in self.locations)

    def genomic_position_to_index_strand_1(self, position_1):
        """
        Map a genomic position to a 1-based index into the spliced, strand-oriented
        gene sequence. reference_sequence.cpp:114-135

        When the position is not inside the feature, breseq falls through leaving
        index == the gene length and strand == 0. Callers test `index != 0` as
        their found-check, so that fallthrough is load-bearing; do not "fix" it.
        """
        index_1 = 0
        for loc in self.locations:
            if loc.start_1 <= position_1 <= loc.end_1:
                if loc.strand == 1:
                    index_1 += position_1 - loc.start_1 + 1
                else:
                    index_1 += loc.end_1 - position_1 + 1
                return index_1, loc.strand
            index_1 += loc.end_1 - loc.start_1 + 1
        return index_1, 0

    def __repr__(self):
        return 'Feature(%s %s)' % (self.type, self.name)


class AnnotatedSequence(object):
    """One reference contig: its sequence plus sorted gene and repeat locations."""

    def __init__(self, seq_id, sequence):
        self.seq_id = seq_id
        self.sequence = sequence
        self.features = []
        self.gene_locations = []
        self.repeat_locations = []

    def __len__(self):
        return len(self.sequence)

    def get_sequence_1(self, start_1, end_1):
        return self.sequence[start_1 - 1:end_1]

    def get_stranded_sequence_1(self, strand, start_1, end_1):
        subsequence = self.get_sequence_1(start_1, end_1)
        return subsequence if strand == 1 else reverse_complement(subsequence)

    def update_feature_lists(self):
        """reference_sequence.cpp:711-773"""
        self.gene_locations = []
        self.repeat_locations = []
        kept = []

        for feature in self.features:
            if feature.start_is_indeterminate() and feature.end_is_indeterminate():
                feature.flag_pseudo()
            if not feature.locations:
                continue
            kept.append(feature)

            if feature.is_repeat():
                # breseq ignores repeats with complex locations rather than
                # guessing which sublocation the mobile element really is.
                if len(feature.locations) == 1:
                    self.repeat_locations.extend(feature.locations)
            elif feature.is_gene():
                self.gene_locations.extend(feature.locations)

        self.features = kept
        # Stable sort on (start, end). Order matters: get_overlapping_feature()
        # returns the LAST match, and gene name lists follow this order.
        self.gene_locations.sort(key=FeatureLocation.sort_key)
        self.repeat_locations.sort(key=FeatureLocation.sort_key)


class LoadedReferenceSequences(object):
    """All contigs of a reference, parsed and keyed by seq id.

    **`Loaded` distinguishes it from `aledb_sample.models.ReferenceSequences`**, the row
    recording that an experiment has a reference and where its files are. This is what you
    get by *reading* those files: sequence and features in memory, for the annotator and for
    the mutation editor's validator. Nothing here is stored, and one is built from the other.
    """

    def __init__(self):
        self.sequences = {}

    def __contains__(self, seq_id):
        return seq_id in self.sequences

    def __getitem__(self, seq_id):
        return self.sequences[seq_id]

    def __iter__(self):
        return iter(self.sequences)

    def seq_ids(self):
        return list(self.sequences)

    def get_sequence_1(self, seq_id, start_1, end_1):
        return self.sequences[seq_id].get_sequence_1(start_1, end_1)

    def add(self, annotated_sequence):
        """
        Register a contig under its id, exactly as written.

        A deliberate divergence from breseq, which trims the version suffix off an
        accession when matching GD seq_ids so that `NC_000913.3` and `NC_000913`
        resolve to the same contig. breseq annotates one run against one reference,
        where that is a convenience. ALEdb holds many experiments side by side and
        two of them may legitimately be against different versions of the same
        accession; conflating those would annotate a mutation against the wrong
        genome and say nothing about it. Names are matched exactly, and a .gd whose
        seq_ids are not the reference's is refused at import instead -- see
        ``aledb_import.reference_store.known_seq_ids``.
        """
        self.sequences[annotated_sequence.seq_id] = annotated_sequence

    def repeat_family_sequence(self, repeat_name, strand):
        """
        The consensus sequence of a repeat family, oriented to `strand`.

        breseq (reference_sequence.cpp:2804-2956) picks the most common sequence
        among copies, falling back to the most common length. Used only to fill
        MOB `repeat_size`, which mutation_size_change needs.
        """
        copies = []
        for annotated_sequence in self.sequences.values():
            for location in annotated_sequence.repeat_locations:
                feature = location.feature
                if feature.name != repeat_name or feature.pseudogene:
                    continue
                copies.append((
                    annotated_sequence.get_stranded_sequence_1(
                        location.strand, location.start_1, location.end_1),
                    location.strand,
                ))
        if not copies:
            return ''

        counts = {}
        for sequence, _ in copies:
            counts[sequence] = counts.get(sequence, 0) + 1
        best_sequence, best_count = max(counts.items(), key=lambda kv: kv[1])
        if best_count > 1:
            picked = next(c for c in copies if c[0] == best_sequence)
        else:
            length_counts = {}
            for sequence, _ in copies:
                length_counts[len(sequence)] = length_counts.get(len(sequence), 0) + 1
            best_length = max(length_counts.items(), key=lambda kv: kv[1])[0]
            picked = next(c for c in copies if len(c[0]) == best_length)

        picked_sequence, picked_strand = picked
        if strand not in (1, -1):
            return picked_sequence
        return picked_sequence if strand == picked_strand else reverse_complement(picked_sequence)
