"""
breseq's mutation annotation, in Python.

A port of cReferenceSequences::annotate_1_mutation and friends
(reference_sequence.cpp:3109-4016) so that MutInt can annotate a plain breseq
`output.gd` from a reference GenBank file instead of requiring the output of
`gdtools ANNOTATE`.

Mutations are plain dicts, as produced by mutint_import.gdparse -- the same shape as
breseq's cDiffEntry key/value map. Annotation keys are written back into the
dict, exactly as `gdtools ANNOTATE` would write them.

Fidelity over correctness: where breseq has a quirk that shows up in its output
(the end-indeterminate padding length, the not-found index fallthrough, C-style
truncating division), this reproduces the quirk so that our annotation matches
what breseq itself would have produced. Those spots are commented.
"""

import math

from mutint_import.annotate.codon_tables import (
    DEFAULT_TRANSLATION_TABLE,
    UNKNOWN_AMINO_ACID,
    translate_codon,
)
from mutint_import.annotate.model import reverse_complement

# reference_sequence.cpp:29-57
INTERGENIC_SEPARATOR = '/'
GENE_LIST_SEPARATOR = ','
MULTIPLE_SEPARATOR = '|'
NO_GENE_NAME = '–'          # en-dash
GENE_RANGE_SEPARATOR = '–'  # en-dash
GENE_STRAND_FORWARD = '>'
GENE_STRAND_REVERSE = '<'

# Defaults; breseq exposes these as gdtools ANNOTATE command-line options.
INACTIVATING_OVERLAP_FRACTION = 0.8
INACTIVATING_SIZE_CUTOFF = 15
PROMOTER_DISTANCE = 150
LARGE_SIZE_CUTOFF = 50  # settings.cpp:36, kBreseq_large_mutation_size_cutoff

MUTATION_TYPES = ('SNP', 'SUB', 'DEL', 'INS', 'MOB', 'AMP', 'CON', 'INV', 'INT')

# Every key annotate_1_mutation writes. Stripped before storing a raw GD line so
# that re-annotation never inherits a stale value.
ANNOTATION_KEYS = (
    'locus_tag', 'aa_position', 'aa_ref_seq', 'aa_new_seq', 'codon_position',
    'codon_ref_seq', 'codon_new_seq', 'codon_number', 'codon_position_is_indeterminate',
    'gene_name', 'gene_position', 'gene_strand', 'gene_product', 'genes_overlapping',
    'locus_tags_overlapping', 'genes_inactivated', 'locus_tags_inactivated',
    'genes_promoter', 'locus_tags_promoter', 'snp_type', 'transl_table',
    'mutation_category', 'position_start', 'position_end', 'ref_seq', 'repeat_size',
    'multiple_polymorphic_SNPs_in_same_codon',
)

MAX_REF_SEQ_LENGTH = 20  # longer spans are recorded as "<n>-bp"
LOOK_AHEAD_ENTRIES = 6   # reference_sequence.cpp:4164, k_num_entries_to_look_ahead

# See _merge_codon_pair: breseq always re-translates a merged codon with
# initiation table 1, regardless of the gene's actual table or codon number.
MERGED_CODON_TABLE = 1
MERGED_CODON_NUMBER = 1


class AnnotationError(Exception):
    pass


def _int(mutation, key, default=0):
    value = mutation.get(key, default)
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str(mutation, key, default=''):
    value = mutation.get(key, default)
    return default if value is None else str(value)


def _c_div(numerator, denominator):
    """C-style integer division, truncating toward zero."""
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def _c_mod(numerator, denominator):
    return numerator - _c_div(numerator, denominator) * denominator


def _strand_string(is_top_strand):
    return GENE_STRAND_FORWARD if is_top_strand else GENE_STRAND_REVERSE


def list_to_entry(values, ignore):
    """
    reference_sequence.cpp:3400-3413

    Blank the whole field when every gene contributed the ignore value, so that
    a non-coding mutation does not carry a row of "NA|NA".
    """
    if not any(value != ignore for value in values):
        return ''
    return MULTIPLE_SEPARATOR.join(values)


def mutation_interval(mutation):
    """
    The reference interval a mutation occupies, 1-based inclusive.
    genome_diff_entry.cpp:715-781
    """
    mutation_type = _str(mutation, 'type')
    position = _int(mutation, 'position')

    if mutation_type in ('SNP', 'INS'):
        return position, position
    if mutation_type in ('SUB', 'DEL', 'AMP', 'INV', 'CON', 'INT'):
        return position, position + _int(mutation, 'size') - 1
    if mutation_type == 'MOB':
        duplication_size = _int(mutation, 'duplication_size')
        if duplication_size == 0:
            return position, position
        return position, position + abs(duplication_size) - 1
    if mutation_type in ('MC', 'UN', 'CN'):
        return _int(mutation, 'start'), _int(mutation, 'end')
    return position, position


def get_overlapping_feature(locations, position_1):
    """reference_sequence.cpp:3091-3101 -- the LAST location covering the position."""
    found = None
    for location in locations:
        if location.distance_to_position(position_1) == 0:
            found = location
    return found


def find_nearby_genes(gene_locations, pos_1, pos_2):
    """
    Classify every gene sublocation against the mutation interval.
    reference_sequence.cpp:3109-3170

    Returns within / inside_left / inside_right / between lists plus the nearest
    flanking locations. Classification is per sublocation, not per gene.
    """
    within, between, inside_left, inside_right = [], [], [], []
    previous = following = None
    min_distance_to_previous = min_distance_to_next = math.inf

    for location in gene_locations:
        if location.end_1 < pos_1:
            distance = pos_1 - location.end_1
            if distance < min_distance_to_previous:
                min_distance_to_previous = distance
                previous = location

        covers_left = location.distance_to_position(pos_1) == 0
        covers_right = location.distance_to_position(pos_2) == 0
        if covers_left and covers_right:
            within.append(location)
        elif covers_left:
            inside_left.append(location)
        elif covers_right:
            inside_right.append(location)
        elif location.start_1 >= pos_1 and location.end_1 <= pos_2:
            between.append(location)

        if location.start_1 > pos_2:
            distance = location.start_1 - pos_2
            if distance < min_distance_to_next:
                min_distance_to_next = distance
                following = location

    return {
        'within': within,
        'between': between,
        'inside_left': inside_left,
        'inside_right': inside_right,
        'previous': previous,
        'next': following,
    }


def mutation_size_change(mutation, references):
    """genome_diff_entry.cpp:795-880"""
    mutation_type = _str(mutation, 'type')

    if mutation_type in ('SNP', 'INV', 'MASK'):
        return 0
    if mutation_type == 'SUB':
        return -_int(mutation, 'size') + len(_str(mutation, 'new_seq'))
    if mutation_type == 'INS':
        return len(_str(mutation, 'new_seq'))
    if mutation_type == 'DEL':
        return -_int(mutation, 'size')
    if mutation_type == 'AMP':
        copies = _int(mutation, 'new_copy_number') - 1
        size_change = _int(mutation, 'size') * copies
        mediated = _str(mutation, 'mediated')
        if mediated:
            mediated_sequence = references.repeat_family_sequence(
                mediated, _int(mutation, 'mediated_strand', 1))
            size_change += len(mediated_sequence) * copies
        return size_change
    if mutation_type == 'MOB':
        # repeat_size must have been filled in already; see annotate_mutations.
        size_change = _int(mutation, 'repeat_size') + _int(mutation, 'duplication_size')
        size_change -= _int(mutation, 'del_start')
        size_change -= _int(mutation, 'del_end')
        size_change += len(_str(mutation, 'ins_start'))
        size_change += len(_str(mutation, 'ins_end'))
        return size_change
    return 0


def mutation_overlapping_gene_is_inactivating(
        mutation, snp_type, start, end, gene, references,
        inactivating_overlap_fraction=INACTIVATING_OVERLAP_FRACTION,
        inactivating_size_cutoff=INACTIVATING_SIZE_CUTOFF):
    """
    Would this mutation plausibly knock the gene out?
    reference_sequence.cpp:3304-3397
    """
    index_start_1, _ = gene.genomic_position_to_index_strand_1(start)
    index_end_1, _ = gene.genomic_position_to_index_strand_1(end)
    overlap_length = math.floor(inactivating_overlap_fraction * gene.get_length())

    # index != 0 is breseq's found-check; see genomic_position_to_index_strand_1.
    hits_start_region = (
        (index_start_1 != 0 and index_start_1 <= overlap_length)
        or (index_end_1 != 0 and index_end_1 <= overlap_length))
    if not hits_start_region:
        return False

    mutation_type = _str(mutation, 'type')

    if mutation_type in ('MOB', 'INV', 'INT'):
        return True

    if gene.type == 'CDS' and mutation_type == 'SNP':
        return snp_type == 'nonsense'

    if mutation_type not in ('INS', 'DEL', 'SUB'):
        return False

    size_change = mutation_size_change(mutation, references)

    if abs(size_change) > inactivating_size_cutoff or gene.type != 'CDS':
        return True

    if size_change % 3 != 0:
        return True  # frameshift

    if mutation_type == 'DEL':
        return False

    # In-frame insertion or substitution: does it introduce a stop codon?
    annotated_sequence = references[_str(mutation, 'seq_id')]
    gene_sequence = gene.get_nucleotide_sequence(annotated_sequence)

    codon_start_1 = index_start_1 - (index_start_1 - 1) % 3
    if mutation_type == 'INS':
        mutant_region = gene_sequence[codon_start_1 - 1:index_start_1]
    else:
        mutant_region = gene_sequence[codon_start_1 - 1:index_start_1 - 1]

    mutant_region += _str(mutation, 'new_seq')
    needed = (3 - len(mutant_region) % 3) % 3
    mutant_region += gene_sequence[index_end_1:index_end_1 + needed]

    for offset in range(0, len(mutant_region) - 2, 3):
        # Codon number 2 forces the standard table: an internal codon is never
        # an initiation codon.
        if translate_codon(mutant_region[offset:offset + 3], gene.translation_table, 2) == '*':
            return True
    return False


def _annotate_intergenic(mutation, nearby, start, end, promoter_distance):
    """reference_sequence.cpp:3726-3822 -- the mutation overlaps no gene at all."""
    mutation_type = _str(mutation, 'type')
    if mutation_type in ('SNP', 'RA'):
        mutation['snp_type'] = 'intergenic'

    previous_location = nearby['previous']
    next_location = nearby['next']
    previous_gene = previous_location.feature if previous_location else None
    next_gene = next_location.feature if next_location else None

    if _is_mutation(mutation):
        # A gene's promoter is affected only when the mutation sits upstream of
        # its start codon, so orientation decides which neighbour qualifies.
        # Both do for a divergent pair.
        promoter_genes, promoter_locus_tags = [], []

        previous_distance = math.inf
        if previous_gene is not None:
            for location in previous_gene.locations:
                if location.strand == -1:
                    previous_distance = min(start - location.end_1, previous_distance)

        next_distance = math.inf
        if next_gene is not None:
            for location in next_gene.locations:
                if location.strand == 1:
                    next_distance = min(location.start_1 - end, next_distance)

        if previous_distance <= promoter_distance:
            promoter_genes.append(previous_gene.name)
            promoter_locus_tags.append(previous_gene.get_locus_tag())
        if next_distance <= promoter_distance:
            promoter_genes.append(next_gene.name)
            promoter_locus_tags.append(next_gene.get_locus_tag())

        mutation['genes_promoter'] = GENE_LIST_SEPARATOR.join(promoter_genes)
        mutation['locus_tags_promoter'] = GENE_LIST_SEPARATOR.join(promoter_locus_tags)

    has_previous = previous_gene is not None and len(previous_gene.name) > 0
    has_next = next_gene is not None and len(next_gene.name) > 0

    mutation['gene_strand'] = INTERGENIC_SEPARATOR.join((
        _strand_string(previous_location.is_top_strand()) if has_previous else NO_GENE_NAME,
        _strand_string(next_location.is_top_strand()) if has_next else NO_GENE_NAME,
    ))
    mutation['gene_name'] = INTERGENIC_SEPARATOR.join((
        previous_gene.name if has_previous else NO_GENE_NAME,
        next_gene.name if has_next else NO_GENE_NAME,
    ))
    mutation['locus_tag'] = INTERGENIC_SEPARATOR.join((
        previous_gene.get_locus_tag() if has_previous and previous_gene.get_locus_tag() else NO_GENE_NAME,
        next_gene.get_locus_tag() if has_next and next_gene.get_locus_tag() else NO_GENE_NAME,
    ))

    # The distance is always positive; the sign reports which side of the gene's
    # own reading direction the mutation falls on. "-1" means 1 bp upstream of a
    # start codon, "+1" means 1 bp past a stop codon.
    if has_previous:
        previous_part = ('+' if previous_location.is_top_strand() else '-') \
            + str(start - previous_location.end_1)
    else:
        previous_part = NO_GENE_NAME
    if has_next:
        next_part = ('-' if next_location.is_top_strand() else '+') \
            + str(next_location.start_1 - end)
    else:
        next_part = NO_GENE_NAME
    mutation['gene_position'] = 'intergenic (%s%s%s)' % (
        previous_part, INTERGENIC_SEPARATOR, next_part)

    mutation['gene_product'] = INTERGENIC_SEPARATOR.join((
        previous_gene.product if has_previous and previous_gene.product else NO_GENE_NAME,
        next_gene.product if has_next and next_gene.product else NO_GENE_NAME,
    ))


def _annotate_in_genes(mutation, within_locations, start, end, references,
                       ignore_pseudogenes, inactivating_overlap_fraction,
                       inactivating_size_cutoff):
    """
    reference_sequence.cpp:3417-3651 -- the mutation fits entirely inside one or
    more gene sublocations. Per-gene values are joined with "|".
    """
    mutation_type = _str(mutation, 'type')
    is_mutation = _is_mutation(mutation)

    gene_names, gene_products, locus_tags = [], [], []
    gene_positions, gene_strands, snp_types = [], [], []
    codon_indeterminate, codon_positions, codon_numbers, aa_positions = [], [], [], []
    codon_ref_seqs, codon_new_seqs, aa_ref_seqs, aa_new_seqs, transl_tables = [], [], [], [], []
    genes_inactivated, locus_tags_inactivated = [], []
    genes_overlapping, locus_tags_overlapping = [], []

    for gene_location in within_locations:
        gene = gene_location.feature

        # Position is measured from this SUBLOCATION's 5' end, and the "/N nt"
        # denominator below is that sublocation's length -- not the spliced gene.
        sublocation_start = (gene_location.start_1 if gene_location.is_top_strand()
                             else gene_location.end_1)
        if start == end:
            gene_position = str(abs(start - sublocation_start) + 1)
        else:
            offset_start = abs(start - sublocation_start) + 1
            offset_end = abs(end - sublocation_start) + 1
            low, high = sorted((offset_start, offset_end))
            gene_position = '%d-%d' % (low, high)

        gene_nt_size = str(gene_location.end_1 - gene_location.start_1 + 1)
        gene_strand = _strand_string(gene_location.is_top_strand())

        codon_position_is_indeterminate = '0'
        codon_position = codon_number = aa_position = 'NA'
        codon_ref_seq = codon_new_seq = aa_ref_seq = aa_new_seq = transl_table = 'NA'
        snp_type = ''

        if not ignore_pseudogenes and gene.pseudogene:
            if mutation_type in ('SNP', 'RA'):
                snp_type = 'pseudogene'
            gene_position = 'pseudogene (%s/%s nt)' % (gene_position, gene_nt_size)

        elif gene.type != 'CDS':
            if mutation_type in ('SNP', 'RA'):
                snp_type = 'noncoding'
            gene_position = 'noncoding (%s/%s nt)' % (gene_position, gene_nt_size)

        elif mutation_type != 'SNP':
            # Only substitutions get codon analysis; everything else in a CDS
            # gets the generic "coding (n/N nt)" form.
            gene_position = 'coding (%s/%s nt)' % (gene_position, gene_nt_size)

        else:
            annotated_sequence = references[_str(mutation, 'seq_id')]
            gene_nt_sequence = gene.get_nucleotide_sequence(annotated_sequence)

            indeterminate_offset_0 = 0
            if gene.start_is_indeterminate():
                indeterminate_offset_0 = (3 - len(gene_nt_sequence) % 3) % 3
                gene_nt_sequence = 'N' * indeterminate_offset_0 + gene_nt_sequence
                codon_position_is_indeterminate = '1'
            if gene.end_is_indeterminate():
                # breseq pads by (len % 3) rather than ((3 - len % 3) % 3).
                # Reproduced deliberately so our codons match breseq's.
                gene_nt_sequence += 'N' * (len(gene_nt_sequence) % 3)

            mutated_position_1 = _int(mutation, 'position')
            mutated_index_1, mutated_strand = gene.genomic_position_to_index_strand_1(
                mutated_position_1)

            mutated_codon_position_1 = 1 + _c_mod(
                mutated_index_1 + indeterminate_offset_0 - 1, 3)
            mutated_codon_number_1 = 1 + _c_div(
                indeterminate_offset_0 + mutated_index_1 - 1, 3)
            codon_ref_seq = gene_nt_sequence[
                (mutated_codon_number_1 - 1) * 3:(mutated_codon_number_1 - 1) * 3 + 3]

            codon_position = str(mutated_codon_position_1)
            codon_number = str(mutated_codon_number_1)
            aa_position = str(mutated_codon_number_1)

            if len(codon_ref_seq) != 3:
                # A CDS whose length is not a multiple of 3, mutated in its last
                # partial codon. breseq warns and falls back to a generic coding
                # annotation with no snp_type -- but it keeps the partial
                # codon_ref_seq and the codon/aa positions it already computed.
                gene_position = 'coding (%s/%s nt)' % (gene_position, gene_nt_size)
            else:
                # A gene with an indeterminate start has no trustworthy codon 1,
                # so force the standard table there.
                effective_codon_number = (
                    2 if (gene.start_is_indeterminate() and mutated_codon_number_1 == 1)
                    else mutated_codon_number_1)
                aa_ref_seq = translate_codon(
                    codon_ref_seq, gene.translation_table, effective_codon_number)

                new_seq = _str(mutation, 'new_seq')
                new_base = (new_seq[0] if mutated_strand == 1
                            else reverse_complement(new_seq)[0]) if new_seq else 'N'
                codon_new_seq = (codon_ref_seq[:mutated_codon_position_1 - 1]
                                 + new_base
                                 + codon_ref_seq[mutated_codon_position_1:])
                aa_new_seq = translate_codon(
                    codon_new_seq, gene.translation_table, effective_codon_number)
                transl_table = str(gene.translation_table)

                if aa_ref_seq != '*' and aa_new_seq == '*':
                    snp_type = 'nonsense'
                elif aa_ref_seq != aa_new_seq:
                    snp_type = 'nonsynonymous'
                else:
                    snp_type = 'synonymous'

        codon_indeterminate.append(codon_position_is_indeterminate)
        codon_positions.append(codon_position)
        codon_numbers.append(codon_number)
        aa_positions.append(aa_position)
        codon_ref_seqs.append(codon_ref_seq)
        codon_new_seqs.append(codon_new_seq)
        aa_ref_seqs.append(aa_ref_seq)
        aa_new_seqs.append(aa_new_seq)
        transl_tables.append(transl_table)

        if is_mutation:
            if mutation_overlapping_gene_is_inactivating(
                    mutation, snp_type, start, end, gene, references,
                    inactivating_overlap_fraction, inactivating_size_cutoff):
                genes_inactivated.append(gene.name)
                locus_tags_inactivated.append(gene.get_locus_tag())
            else:
                genes_overlapping.append(gene.name)
                locus_tags_overlapping.append(gene.get_locus_tag())

        gene_names.append(gene.name)
        gene_products.append(gene.product)
        locus_tags.append(gene.get_locus_tag())
        gene_positions.append(gene_position)
        gene_strands.append(gene_strand)
        snp_types.append(snp_type)

    mutation['gene_name'] = MULTIPLE_SEPARATOR.join(gene_names)
    mutation['gene_product'] = MULTIPLE_SEPARATOR.join(gene_products)
    mutation['locus_tag'] = MULTIPLE_SEPARATOR.join(locus_tags)
    mutation['gene_position'] = MULTIPLE_SEPARATOR.join(gene_positions)
    mutation['gene_strand'] = MULTIPLE_SEPARATOR.join(gene_strands)
    mutation['snp_type'] = MULTIPLE_SEPARATOR.join(snp_types)

    mutation['codon_position_is_indeterminate'] = list_to_entry(codon_indeterminate, '0')
    mutation['codon_position'] = list_to_entry(codon_positions, 'NA')
    mutation['codon_number'] = list_to_entry(codon_numbers, 'NA')
    mutation['aa_position'] = list_to_entry(aa_positions, 'NA')
    mutation['codon_ref_seq'] = list_to_entry(codon_ref_seqs, 'NA')
    mutation['codon_new_seq'] = list_to_entry(codon_new_seqs, 'NA')
    mutation['aa_ref_seq'] = list_to_entry(aa_ref_seqs, 'NA')
    mutation['aa_new_seq'] = list_to_entry(aa_new_seqs, 'NA')
    mutation['transl_table'] = list_to_entry(transl_tables, 'NA')

    if genes_inactivated:
        mutation['genes_inactivated'] = GENE_LIST_SEPARATOR.join(genes_inactivated)
    if locus_tags_inactivated:
        mutation['locus_tags_inactivated'] = GENE_LIST_SEPARATOR.join(locus_tags_inactivated)
    if genes_overlapping:
        mutation['genes_overlapping'] = GENE_LIST_SEPARATOR.join(genes_overlapping)
    if locus_tags_overlapping:
        mutation['locus_tags_overlapping'] = GENE_LIST_SEPARATOR.join(locus_tags_overlapping)


def _annotate_spanning_genes(mutation, nearby, start, end, references,
                             inactivating_overlap_fraction, inactivating_size_cutoff):
    """
    reference_sequence.cpp:3823-3949 -- the mutation spans whole genes or runs
    off the end of one. Brackets mark genes that are only partly covered.
    """
    mutation_type = _str(mutation, 'type')
    gene_names, locus_tag_list = [], []

    for location in nearby['inside_left']:
        gene = location.feature
        gene_names.append('[%s]' % gene.name)
        if gene.get_locus_tag():
            locus_tag_list.append('[%s]' % gene.get_locus_tag())
    for location in nearby['between']:
        gene = location.feature
        gene_names.append(gene.name)  # fully covered, so no brackets
        if gene.get_locus_tag():
            # ...but breseq brackets the locus tag even here.
            locus_tag_list.append('[%s]' % gene.get_locus_tag())
    for location in nearby['inside_right']:
        gene = location.feature
        gene_names.append('[%s]' % gene.name)
        if gene.get_locus_tag():
            locus_tag_list.append('[%s]' % gene.get_locus_tag())

    if _is_mutation(mutation):
        inactivated, inactivated_tags = [], []
        overlapping, overlapping_tags = [], []

        for location in nearby['inside_left'] + nearby['inside_right']:
            gene = location.feature
            if mutation_overlapping_gene_is_inactivating(
                    mutation, '', start, end, gene, references,
                    inactivating_overlap_fraction, inactivating_size_cutoff):
                inactivated.append(gene.name)
                inactivated_tags.append(gene.get_locus_tag())
            else:
                overlapping.append(gene.name)
                overlapping_tags.append(gene.get_locus_tag())

        for location in nearby['between']:
            gene = location.feature
            if mutation_type == 'DEL':
                inactivated.append(gene.name)   # the gene is gone entirely
                inactivated_tags.append(gene.get_locus_tag())
            elif mutation_type != 'INV':
                # Genes wholly inside an inversion are left out of both lists:
                # they move but are not disrupted.
                overlapping.append(gene.name)
                overlapping_tags.append(gene.get_locus_tag())

        mutation['genes_inactivated'] = GENE_LIST_SEPARATOR.join(inactivated)
        mutation['locus_tags_inactivated'] = GENE_LIST_SEPARATOR.join(inactivated_tags)
        mutation['genes_overlapping'] = GENE_LIST_SEPARATOR.join(overlapping)
        mutation['locus_tags_overlapping'] = GENE_LIST_SEPARATOR.join(overlapping_tags)

    # In this branch gene_product carries the list of GENE NAMES, not products.
    # Downstream code (and genes.util.get_annotated_gene_list) relies on it.
    mutation['gene_product'] = GENE_LIST_SEPARATOR.join(gene_names)

    if len(gene_names) == 1:
        mutation['gene_name'] = gene_names[0]
    elif gene_names:
        mutation['gene_name'] = gene_names[0] + GENE_RANGE_SEPARATOR + gene_names[-1]

    if len(locus_tag_list) == 1:
        mutation['locus_tag'] = locus_tag_list[0]
    elif len(locus_tag_list) > 1:
        mutation['locus_tag'] = locus_tag_list[0] + GENE_RANGE_SEPARATOR + locus_tag_list[-1]


def _is_mutation(mutation):
    return _str(mutation, 'type') in MUTATION_TYPES


def annotate_1_mutation(mutation, references, start, end, repeat_override=False,
                        ignore_pseudogenes=False,
                        inactivating_overlap_fraction=INACTIVATING_OVERLAP_FRACTION,
                        inactivating_size_cutoff=INACTIVATING_SIZE_CUTOFF,
                        promoter_distance=PROMOTER_DISTANCE):
    """
    Annotate one mutation in place. reference_sequence.cpp:3653-3951

    Exactly one of three branches runs: intergenic, contained-in-genes, or
    spanning-genes. Containment wins outright -- if any gene fully contains the
    mutation, genes it merely overlaps are ignored.
    """
    for key in ('locus_tag', 'aa_position', 'aa_ref_seq', 'aa_new_seq', 'codon_position',
                'codon_ref_seq', 'codon_new_seq', 'gene_name', 'gene_position',
                'gene_strand', 'gene_product', 'genes_overlapping', 'locus_tags_overlapping',
                'genes_inactivated', 'locus_tags_inactivated', 'genes_promoter',
                'locus_tags_promoter'):
        mutation[key] = ''

    seq_id = _str(mutation, 'seq_id')
    if seq_id not in references:
        raise AnnotationError(
            'Reference has no sequence "%s" (it has: %s)'
            % (seq_id, ', '.join(sorted(set(references.seq_ids())))))
    annotated_sequence = references[seq_id]

    repeat_location = None
    if repeat_override:
        repeat_location = get_overlapping_feature(annotated_sequence.repeat_locations, start)

    if repeat_location is not None:
        nearby = {'within': [repeat_location], 'between': [], 'inside_left': [],
                  'inside_right': [], 'previous': None, 'next': None}
    else:
        nearby = find_nearby_genes(annotated_sequence.gene_locations, start, end)

    overlapping_count = (len(nearby['within']) + len(nearby['between'])
                         + len(nearby['inside_left']) + len(nearby['inside_right']))

    if overlapping_count == 0:
        _annotate_intergenic(mutation, nearby, start, end, promoter_distance)
    elif nearby['within']:
        _annotate_in_genes(mutation, nearby['within'], start, end, references,
                           ignore_pseudogenes, inactivating_overlap_fraction,
                           inactivating_size_cutoff)
    else:
        _annotate_spanning_genes(mutation, nearby, start, end, references,
                                 inactivating_overlap_fraction, inactivating_size_cutoff)


def categorize_1_mutation(mutation, large_size_cutoff=LARGE_SIZE_CUTOFF):
    """reference_sequence.cpp:3953-4016"""
    mutation_type = _str(mutation, 'type')

    if mutation_type == 'SNP':
        mutation['mutation_category'] = 'snp_' + _str(mutation, 'snp_type')
    elif mutation_type == 'DEL':
        mutation['mutation_category'] = (
            'large_deletion' if _int(mutation, 'size') > large_size_cutoff else 'small_indel')
    elif mutation_type == 'INS':
        mutation['mutation_category'] = (
            'large_insertion' if len(_str(mutation, 'new_seq')) > large_size_cutoff
            else 'small_indel')
    elif mutation_type == 'SUB':
        delta = abs(len(_str(mutation, 'new_seq')) - _int(mutation, 'size'))
        mutation['mutation_category'] = (
            'large_substitution' if delta > large_size_cutoff else 'small_indel')
    elif mutation_type == 'CON':
        mutation['mutation_category'] = 'gene_conversion'
    elif mutation_type == 'MOB':
        mutation['mutation_category'] = 'mobile_element_insertion'
    elif mutation_type == 'AMP':
        amplified = _int(mutation, 'size') * (_int(mutation, 'new_copy_number') - 1)
        mutation['mutation_category'] = (
            'large_amplification' if amplified > large_size_cutoff else 'small_indel')
    elif mutation_type == 'INV':
        mutation['mutation_category'] = 'inversion'
    elif mutation_type == 'INT':
        mutation['mutation_category'] = 'integration'


def _merge_snps_in_same_codon(mutations):
    """
    reference_sequence.cpp:4164-4350

    Two consensus SNPs in one codon each need the other's base to translate
    correctly. Polymorphic SNPs are flagged instead of merged, since they may
    not be on the same chromosome.
    """
    candidates = [m for m in mutations
                  if _str(m, 'type') == 'SNP'
                  and m.get('codon_number') not in (None, '', 'NA')
                  and m.get('codon_position') not in (None, '', 'NA')]
    if len(candidates) < 2:
        return

    by_sequence = {}
    for mutation in candidates:
        by_sequence.setdefault(_str(mutation, 'seq_id'), []).append(mutation)

    for entries in by_sequence.values():
        entries.sort(key=lambda m: _int(m, 'position'))
        for index, mutation in enumerate(entries):
            window = entries[index + 1:index + 1 + LOOK_AHEAD_ENTRIES]
            for other in window:
                if _str(mutation, 'codon_number') != _str(other, 'codon_number'):
                    continue
                position_gap = abs(_int(mutation, 'position') - _int(other, 'position'))
                codon_gap = abs(_int(mutation, 'codon_position')
                                - _int(other, 'codon_position'))
                if position_gap != codon_gap:
                    continue

                if _is_polymorphic(mutation) or _is_polymorphic(other):
                    mutation['multiple_polymorphic_SNPs_in_same_codon'] = '1'
                    other['multiple_polymorphic_SNPs_in_same_codon'] = '1'
                    continue

                _merge_codon_pair(mutation, other)
                _merge_codon_pair(other, mutation)


def _is_polymorphic(mutation):
    frequency = mutation.get('frequency')
    if frequency in (None, ''):
        return False
    try:
        return float(frequency) != 1.0
    except (TypeError, ValueError):
        return False


def _merge_codon_pair(target, other):
    codon = _str(target, 'codon_new_seq')
    if len(codon) != 3:
        return
    transl_table = target.get('transl_table')
    if transl_table in (None, '', 'NA'):
        return
    # A SNP inside two overlapping genes carries "|"-joined per-gene values.
    # breseq merges those element-wise; we do not, so leave them alone rather
    # than write something wrong for a doubly-rare case.
    if MULTIPLE_SEPARATOR in codon or MULTIPLE_SEPARATOR in str(transl_table):
        return

    other_position = _int(other, 'codon_position')
    if not 1 <= other_position <= 3:
        return
    other_new_seq = _str(other, 'new_seq')
    if not other_new_seq:
        return

    base = other_new_seq[0]
    if _str(target, 'gene_strand') == GENE_STRAND_REVERSE:
        base = reverse_complement(other_new_seq)[0]

    merged = codon[:other_position - 1] + base + codon[other_position:]
    target['codon_new_seq'] = merged
    # breseq re-translates the merged codon with translate_codon(codon,
    # from_string(transl_table), from_string(aa_position)). Neither call names a
    # template type, so both bind to the `bool` overload of from_string and
    # collapse to 1 -- meaning the merged codon is always translated with
    # initiation table 1, whatever the gene's real table and codon number are.
    # That is a breseq bug, but reproducing it is the point: our annotation has
    # to equal what gdtools would have written. It only bites when two consensus
    # SNPs share codon 1 of a gene whose table disagrees with table 1 there.
    target['aa_new_seq'] = translate_codon(merged, MERGED_CODON_TABLE,
                                           MERGED_CODON_NUMBER)


def annotate_mutations(mutations, references, ignore_pseudogenes=False,
                       large_size_cutoff=LARGE_SIZE_CUTOFF,
                       inactivating_overlap_fraction=INACTIVATING_OVERLAP_FRACTION,
                       inactivating_size_cutoff=INACTIVATING_SIZE_CUTOFF,
                       promoter_distance=PROMOTER_DISTANCE):
    """
    Annotate every mutation in `mutations` in place, the way
    `gdtools ANNOTATE` would. reference_sequence.cpp:4018-4143

    `mutations` is a list of GD mutation dicts. Evidence entries (RA/JC/MC/UN)
    are not annotated -- MutInt does not store them per-mutation.
    """
    annotated = []
    for mutation in mutations:
        if not _is_mutation(mutation):
            continue
        start, end = mutation_interval(mutation)
        annotate_1_mutation(
            mutation, references, start, end,
            repeat_override=False, ignore_pseudogenes=ignore_pseudogenes,
            inactivating_overlap_fraction=inactivating_overlap_fraction,
            inactivating_size_cutoff=inactivating_size_cutoff,
            promoter_distance=promoter_distance)

        mutation['position_start'] = str(start)
        mutation['position_end'] = str(end)
        span = end - start + 1
        if span > MAX_REF_SEQ_LENGTH:
            mutation['ref_seq'] = '%d-bp' % span
        else:
            mutation['ref_seq'] = references.get_sequence_1(
                _str(mutation, 'seq_id'), start, end)

        if _str(mutation, 'type') == 'MOB':
            repeat_sequence = references.repeat_family_sequence(
                _str(mutation, 'repeat_name'), _int(mutation, 'strand', 1))
            mutation['repeat_size'] = str(len(repeat_sequence))

        categorize_1_mutation(mutation, large_size_cutoff)
        annotated.append(mutation)

    _merge_snps_in_same_codon(annotated)
    return annotated


def is_annotated(mutations):
    """
    Whether a GD already carries annotation.

    True for a file produced by `gdtools ANNOTATE` (or the COMPARE merges the
    old pipeline built), false for breseq's plain output.gd. Only the latter
    needs a reference sequence, so this decides whether a missing reference is
    a problem.
    """
    for mutation in mutations:
        if _is_mutation(mutation) and mutation.get('gene_name'):
            return True
    return False


def strip_annotation(mutation):
    """
    The raw GD fields of a mutation, with every annotation key removed.

    This is what gets persisted as Mutation.gd_attributes: re-annotating against
    a different reference must not inherit the old reference's answers.
    """
    return {key: value for key, value in mutation.items()
            if key not in ANNOTATION_KEYS and not key.startswith('html_')
            and not key.startswith('_')}
