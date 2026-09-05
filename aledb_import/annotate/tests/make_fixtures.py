"""
Regenerate the synthetic annotation fixtures.

Run this only when the fixtures need to change; the generated files are checked
in so that the test suite needs neither Biopython's writer nor breseq.

    python aledb_import/annotate/tests/make_fixtures.py
    gdtools ANNOTATE -f GD -r <fixtures>/synthetic.gbk \
        -o <fixtures>/synthetic.expected.gd <fixtures>/synthetic.gd

The reference is laid out so that every branch of breseq's annotation has a
case: both strands, spliced genes, a tRNA, a pseudogene, an IS family with two
copies, a fuzzy-ended CDS, overlapping genes, and convergent/divergent gene
pairs with intergenic gaps both under and over the 150 bp promoter distance.
"""

import os

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import AfterPosition, BeforePosition, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord

FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))
SEQ_ID = 'SYN001'
GENOME_LENGTH = 6000

# "ATCGGC" contains no stop codon in any reading frame on either strand, so the
# background sequence never accidentally truncates a CDS.
BACKGROUND_UNIT = 'ATCGGC'

# 1-based inclusive (start, end) of codons we overwrite so that specific SNPs
# have a known effect. See the GD below.
FORCED_CODONS = [
    (128, 'TAC'),   # thrA codon 10: SNP at 130 C->A makes TAA, a nonsense change
    (158, 'TTT'),   # thrA codon 20: SNP at 160 T->C makes TTC, synonymous
    (188, 'TTT'),   # thrA codon 30: SNP at 190 T->G makes TTG, nonsynonymous
    (1595, 'GTA'),  # araJ is on the minus strand; its codon 3 reads TAC here
    (2101, 'GTG'),  # divB codon 1: a table-11 start codon, so it translates as M
    (5995, 'TAC'),  # truncA's last full codon (29), before its trailing partial one
]


def _background():
    repeats = GENOME_LENGTH // len(BACKGROUND_UNIT) + 1
    return list((BACKGROUND_UNIT * repeats)[:GENOME_LENGTH])


def _gene(bases, start_1, end_1, strand, name, locus_tag, product,
          feature_type='CDS', pseudo=False, fuzzy_start=False, fuzzy_end=False,
          transl_table='11'):
    start = BeforePosition(start_1 - 1) if fuzzy_start else start_1 - 1
    end = AfterPosition(end_1) if fuzzy_end else end_1
    qualifiers = {
        'gene': [name],
        'locus_tag': [locus_tag],
        'product': [product],
    }
    if feature_type == 'CDS':
        qualifiers['transl_table'] = [transl_table]
    if pseudo:
        qualifiers['pseudo'] = ['']
    return SeqFeature(SimpleLocation(start, end, strand=strand),
                      type=feature_type, qualifiers=qualifiers)


def _spliced(part_ranges, strand, name, locus_tag, product):
    from Bio.SeqFeature import CompoundLocation
    parts = [SimpleLocation(s - 1, e, strand=strand) for s, e in part_ranges]
    if strand == -1:
        parts = list(reversed(parts))
    return SeqFeature(CompoundLocation(parts), type='CDS', qualifiers={
        'gene': [name], 'locus_tag': [locus_tag], 'product': [product],
        'transl_table': ['11'],
    })


def _repeat(start_1, end_1, strand, element):
    return SeqFeature(SimpleLocation(start_1 - 1, end_1, strand=strand),
                      type='repeat_region', qualifiers={
                          'mobile_element_type': ['insertion sequence:%s' % element],
                      })


def build_record():
    bases = _background()
    for start_1, codon in FORCED_CODONS:
        for offset, base in enumerate(codon):
            bases[start_1 - 1 + offset] = base

    record = SeqRecord(Seq(''.join(bases)), id=SEQ_ID, name=SEQ_ID,
                       description='Synthetic reference for annotation tests')
    record.annotations['molecule_type'] = 'DNA'
    record.annotations['topology'] = 'linear'

    record.features = [
        SeqFeature(SimpleLocation(0, GENOME_LENGTH, strand=1), type='source',
                   qualifiers={'organizm': ['synthetic construct'],
                               'mol_type': ['genomic DNA']}),

        # thrA -> ... <- thrB : convergent, 100 bp apart. Neither promoter is hit.
        _gene(bases, 101, 400, 1, 'thrA', 'b0001', 'aspartokinase I'),
        _gene(bases, 501, 800, -1, 'thrB', 'b0002', 'homoserine kinase'),

        # <- araJ, 200 bp after thrB. A mutation between them is in thrB's promoter.
        _gene(bases, 1001, 1600, -1, 'araJ', 'b0003', 'predicted transporter'),

        # <- divA ... divB -> : divergent, 100 bp apart. Both promoters are hit.
        _gene(bases, 1701, 2000, -1, 'divA', 'b0004', 'divergent gene A'),
        _gene(bases, 2101, 2400, 1, 'divB', 'b0005', 'divergent gene B'),

        _spliced([(2501, 2600), (2701, 2801)], 1, 'splA', 'b0006', 'spliced gene, plus strand'),
        _spliced([(3001, 3101), (3201, 3300)], -1, 'splB', 'b0007', 'spliced gene, minus strand'),

        _gene(bases, 3501, 3574, 1, 'trnA', 'b0008', 'tRNA-Ala', feature_type='tRNA'),
        _gene(bases, 3701, 4000, 1, 'pseuA', 'b0009', 'pseudogene, decayed', pseudo=True),

        _repeat(4201, 4400, 1, 'IS150'),
        _repeat(4601, 4800, -1, 'IS150'),

        _gene(bases, 5001, 5300, 1, 'fuzA', 'b0010', 'partial gene', fuzzy_start=True),

        # Overlapping genes on opposite strands, so one SNP annotates against both.
        _gene(bases, 5401, 5700, 1, 'ovlA', 'b0011', 'overlapping gene A'),
        _gene(bases, 5500, 5799, -1, 'ovlB', 'b0012', 'overlapping gene B'),

        # A CDS with a fuzzy 3' end, and one whose length is not a multiple of
        # 3 so that a SNP in its trailing partial codon hits breseq's fallback.
        _gene(bases, 5851, 5900, 1, 'fuzB', 'b0013', 'partial gene, open 3-prime end',
              fuzzy_end=True),
        _gene(bases, 5911, 5999, 1, 'truncA', 'b0014', 'truncated reading frame'),
    ]
    return record


GD_LINES = [
    '#=GENOME_DIFF\t1.0',
    '#=REFSEQ\tsynthetic.gbk',
    # --- coding SNPs in thrA (plus strand) ---
    'SNP\t1\t.\t%s\t130\tA' % SEQ_ID,        # TAC -> TAA, nonsense
    'SNP\t2\t.\t%s\t160\tC' % SEQ_ID,        # TTT -> TTC, synonymous
    'SNP\t3\t.\t%s\t190\tG' % SEQ_ID,        # TTT -> TTG, nonsynonymous
    'SNP\t4\t.\t%s\t101\tG' % SEQ_ID,        # codon 1, exercises the initiation table
    # --- minus-strand coding SNP in araJ ---
    'SNP\t5\t.\t%s\t1595\tC' % SEQ_ID,
    # --- intergenic SNPs ---
    'SNP\t6\t.\t%s\t450\tA' % SEQ_ID,        # convergent pair, no promoter
    'SNP\t7\t.\t%s\t850\tA' % SEQ_ID,        # 50 bp downstream of <-thrB: promoter
    'SNP\t8\t.\t%s\t2050\tA' % SEQ_ID,       # divergent pair: both promoters
    'SNP\t9\t.\t%s\t900\tA' % SEQ_ID,        # >150 bp from both: no promoter
    # --- non-CDS and pseudogene ---
    'SNP\t10\t.\t%s\t3520\tA' % SEQ_ID,      # inside tRNA: noncoding
    'SNP\t11\t.\t%s\t3750\tA' % SEQ_ID,      # inside pseudogene
    # --- spliced genes ---
    'SNP\t12\t.\t%s\t2750\tA' % SEQ_ID,      # second exon of splA
    'SNP\t13\t.\t%s\t3050\tA' % SEQ_ID,      # first exon (genomic) of splB
    # --- fuzzy-ended CDS ---
    'SNP\t14\t.\t%s\t5100\tA' % SEQ_ID,
    # --- overlapping genes: one SNP, two annotations ---
    'SNP\t15\t.\t%s\t5600\tA' % SEQ_ID,
    # --- indels inside a CDS ---
    'INS\t16\t.\t%s\t200\tG' % SEQ_ID,       # frameshift
    'INS\t17\t.\t%s\t210\tGGG' % SEQ_ID,     # in-frame
    'DEL\t18\t.\t%s\t250\t3' % SEQ_ID,       # in-frame deletion
    'DEL\t19\t.\t%s\t260\t1' % SEQ_ID,       # frameshift
    'SUB\t20\t.\t%s\t300\t3\tAAA' % SEQ_ID,
    # --- larger events ---
    'DEL\t21\t.\t%s\t90\t1000' % SEQ_ID,     # spans thrA and thrB entirely
    'DEL\t22\t.\t%s\t350\t400' % SEQ_ID,     # runs off thrA's right end into thrB
    'DEL\t23\t.\t%s\t1400\t80\tmediated=IS150' % SEQ_ID,
    'AMP\t24\t.\t%s\t1750\t500\t3' % SEQ_ID,
    'AMP\t25\t.\t%s\t1750\t10\t2' % SEQ_ID,
    'INV\t26\t.\t%s\t1650\t800' % SEQ_ID,
    'MOB\t27\t.\t%s\t1300\tIS150\t1\t6' % SEQ_ID,
    'MOB\t28\t.\t%s\t950\tIS150\t-1\t0' % SEQ_ID,
    'CON\t29\t.\t%s\t2200\t30\t%s:3001-3030' % (SEQ_ID, SEQ_ID),
    'INS\t30\t.\t%s\t4500\tT' % SEQ_ID,      # between the two IS copies
    # --- initiation codon and truncated reading frame ---
    'SNP\t31\t.\t%s\t2101\tA' % SEQ_ID,      # divB codon 1: GTG (start, M) -> ATG
    'SNP\t32\t.\t%s\t2103\tA' % SEQ_ID,      # divB codon 1: GTG -> GTA, no longer a start
    'SNP\t33\t.\t%s\t5997\tA' % SEQ_ID,      # truncA: last full codon, TAC -> TAA
    'SNP\t34\t.\t%s\t5999\tA' % SEQ_ID,      # truncA: inside the trailing partial codon
    'SNP\t35\t.\t%s\t5890\tA' % SEQ_ID,      # fuzB, the gene with an open 3-prime end
    # --- a substitution that introduces a premature stop in-frame ---
    'SUB\t36\t.\t%s\t155\t3\tTAA' % SEQ_ID,
]


def main():
    record = build_record()
    genbank_path = os.path.join(FIXTURE_DIR, 'fixtures', 'synthetic.gbk')
    gd_path = os.path.join(FIXTURE_DIR, 'fixtures', 'synthetic.gd')
    os.makedirs(os.path.dirname(genbank_path), exist_ok=True)

    with open(genbank_path, 'w') as handle:
        SeqIO.write(record, handle, 'genbank')
    with open(gd_path, 'w') as handle:
        handle.write('\n'.join(GD_LINES) + '\n')

    print('wrote %s' % genbank_path)
    print('wrote %s' % gd_path)


if __name__ == '__main__':
    main()
