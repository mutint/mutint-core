"""What a mutation did to the protein, from breseq's `snp_type`.

`Mutation.snp_type` is breseq's own functional class, written by `aledb_import.annotate.annotator`
(a port of `cReferenceSequences::annotate_1_mutation`) and promoted to an indexed column by
`aledb_import.annotation.PROMOTED_COLUMNS`. This module turns one of its values into one of the
buckets `/stats` and `/dashboard` count.

**It exists because both pages used to classify from `Mutation.protein_change` instead**, which is
a *display* string -- `text_mutation_annotation` strips the tags off breseq's
`html_mutation_annotation`, so a coding SNP reads `I34S (ATC->AGC)` and contains neither
"synonymous" nor "nonsynonymous". Substring-matching this vocabulary against it put 19 982 of the
dev database's 24 088 mutations in `unannotated`, including every one of the 12 793 nonsynonymous
and 4 878 synonymous SNPs. `snp_type` held the right answer the whole time and nothing read it.

The complete value space, and where each value is written:

* `intergenic` -- `annotator.py:285`, when the mutation overlaps no gene. Assigned on its own and
  **never joined**, so it cannot appear in a compound.
* `pseudogene` -- `annotator.py:402`, a SNP in a gene flagged pseudogene.
* `noncoding` -- `annotator.py:407`, a SNP in a feature that is not a CDS (tRNA, rRNA).
* `nonsense` / `nonsynonymous` / `synonymous` -- `annotator.py:470-474`, from the translated codon.
* `''` -- three separate ways, all legitimate: a non-SNP inside a gene, a SNP in a CDS's partial
  last codon (`len(codon_ref_seq) != 3`, where breseq warns and gives up), and a non-SNP in a
  pseudogene or non-CDS feature. `_annotate_spanning_genes` writes no `snp_type` at all, and rows
  imported before the annotator existed have SQL NULL.
* **any `|`-join of the above.** `annotator.py:508` joins one value per overlapping gene, so
  `nonsynonymous|synonymous` means a base in two reading frames: silent in one, amino-acid-changing
  in the other. The join is unconditional, which is why a non-SNP in two genes gets the literal
  `'|'` -- the dev database contains an INS with exactly that.

**A compound resolves to its most severe token**, which is what the ordering of
`FUNCTIONAL_CHANGE_TYPE_LIST` means. One list does both jobs deliberately: splitting on `|` and
testing set membership removes the substring hazard that used to force `nonsynonymous` to precede
`synonymous`, which frees the order to carry severity instead. A second ordered tuple would be two
lists to keep in step, and a token present in one and missing from the other would be silently
unreachable.

**The result is SNP-only, and that is a decision rather than a limitation.** breseq assigns
`snp_type` for `SNP` and `RA` entries only, so every DEL, INS, MOB, AMP, SUB and INV answers
`unannotated` here -- 2 720 mutations in the dev database, of which 822 currently borrow an
`intergenic` bucket from `protein_change`. Recovering those would mean reading
`annotation['gene_position']`, whose leading word is one of `intergenic`/`coding`/`pseudogene`/
`noncoding`. It is a clean enough key, but it answers a *different question* -- which feature the
mutation sits in, not what it did to a protein -- and 1 631 non-SNPs are `coding`, a word with no
bucket on this axis. Merging the two is what the old `protein_change` matching did by accident,
its `intergenic` count being 3 046 SNPs that changed a protein not at all plus 822 indels that sit
between genes. If it is ever wanted, the honest implementation is a bucket-sized `feature_class`
added to `aledb_import.annotation.PROMOTED_COLUMNS` with a backfill, not a fallback here.
"""

#: `annotator.py:508` joins one value per overlapping gene with this, mirroring breseq's
#: `MULTIPLE_SEPARATOR` (`reference_sequence.cpp:29-57`). Named rather than spelled inline so the
#: provenance of a bare '|' is not something the next reader has to go looking for.
MULTIPLE_SEPARATOR = '|'

UNANNOTATED = 'unannotated'

#: breseq's `snp_type` vocabulary, **ordered by severity, most severe first**, with `UNANNOTATED`
#: last as the natural fall-through. The order is load-bearing twice over: it resolves a compound
#: to one bucket, and it is the order the two pages render their rows in.
#:
#: `nonsense` was missing from this list until the change that moved the classification onto
#: `snp_type`. `annotator.py:470` has written it since the port landed and 392 SNPs in the dev
#: database carry it, so they were all being counted as something else.
FUNCTIONAL_CHANGE_TYPE_LIST = [
    'nonsense',
    'nonsynonymous',
    'synonymous',
    'noncoding',
    'pseudogene',
    'intergenic',
    UNANNOTATED,
]


def functional_change_bucket(snp_type):
    """The single bucket a `snp_type` value counts under.

    Splits on `|` and returns the most severe token present, so a SNP that is silent in one
    reading frame and nonsense in another counts as nonsense. Exact tokens, not substrings --
    which is what stops `nonsynonymous` being read as `synonymous`, a hazard the old
    substring matching had to order this list around.

    Answers `UNANNOTATED` for the empty string, for `None` (rows imported before the annotator
    existed), for the bare separator `'|'`, and for any token this vocabulary does not know. That
    last case is deliberate rather than an oversight: a `.gd` that arrived already annotated by a
    different breseq version can carry a value we have never seen, and a page that raises on it
    would be a worse answer than a page that calls it unannotated.
    """
    tokens = set((snp_type or '').split(MULTIPLE_SEPARATOR))
    for change in FUNCTIONAL_CHANGE_TYPE_LIST:
        if change in tokens:
            return change
    return UNANNOTATED
