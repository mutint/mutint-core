"""
Codon translation tables, matching breseq's behavior exactly.

breseq hardcodes two parallel sets of 64-character tables in
reference_sequence.cpp:3167-3237: `translation_tables` and
`initiation_codon_translation_tables`. The initiation set is used only for
codon 1 of a CDS.

Those initiation tables are exactly the standard NCBI table with every entry in
`start_codons` mapped to 'M'. That was verified against all 18 tables breseq
defines; 35 of the 36 strings are reproduced byte-for-byte from Biopython. The
lone exception is table 3 (yeast mitochondrial), where breseq keeps GTG -> V at
codon 1 while current NCBI lists GTG as a start codon -- breseq's copy predates
that NCBI revision. INITIATION_OVERRIDES pins that difference so we stay
bit-identical with breseq rather than with today's NCBI.

mutint_import/annotate/tests/test_codon_tables.py asserts the equivalence against
breseq's literal strings, so a future Biopython upgrade that resyncs with NCBI
cannot silently change annotation.
"""

from Bio.Data import CodonTable

# breseq defines tables 1-6, 9-16 and 21-24; 7, 8 and 17-20 were deleted by NCBI.
BRESEQ_TABLE_IDS = (1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 21, 22, 23, 24)

DEFAULT_TRANSLATION_TABLE = 11  # bacterial; what breseq assumes absent /transl_table

STOP = '*'
UNKNOWN_AMINO_ACID = '?'

# (table_id, codon) -> amino acid, for codon 1 only. See module docstring.
INITIATION_OVERRIDES = {
    (3, 'GTG'): 'V',
}

_standard_cache = {}
_initiation_cache = {}


def _build(table_id):
    try:
        table = CodonTable.unambiguous_dna_by_id[table_id]
    except KeyError:
        raise ValueError("Unknown translation table #%s" % table_id)

    standard = dict(table.forward_table)
    for codon in table.stop_codons:
        standard[codon] = STOP

    initiation = dict(standard)
    for codon in table.start_codons:
        initiation[codon] = 'M'
    for (override_table_id, codon), amino_acid in INITIATION_OVERRIDES.items():
        if override_table_id == table_id:
            initiation[codon] = amino_acid

    _standard_cache[table_id] = standard
    _initiation_cache[table_id] = initiation


def _table_for(table_id, codon_number_1):
    cache = _initiation_cache if codon_number_1 == 1 else _standard_cache
    if table_id not in cache:
        _build(table_id)
    return cache[table_id]


def translate_codon(codon, translation_table=DEFAULT_TRANSLATION_TABLE, codon_number_1=2):
    """
    Translate a single codon the way breseq's cReferenceSequences::translate_codon does.

    codon_number_1 selects the table: 1 uses the initiation table, anything else
    uses the standard table. Callers that deliberately want the standard table
    pass 2, mirroring breseq.

    Any codon that is not exactly three unambiguous bases translates to '?'.
    """
    if codon is None or len(codon) != 3:
        return UNKNOWN_AMINO_ACID
    return _table_for(translation_table, codon_number_1).get(codon.upper(), UNKNOWN_AMINO_ACID)

