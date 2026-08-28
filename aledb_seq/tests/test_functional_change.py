"""The severity hierarchy, over every value breseq's `snp_type` can hold.

No database: this is a pure `str -> str` rule and the point of giving it its own module is that
it can be pinned without one. Every case below is a value that actually occurs -- the compounds
and the bare `'|'` are taken from the dev database, and `None` is there because two rows imported
before the annotator existed have SQL NULL.
"""

from django.test import SimpleTestCase

from aledb_seq.functional_change import (
    FUNCTIONAL_CHANGE_TYPE_LIST, UNANNOTATED, functional_change_bucket,
)


class FunctionalChangeBucketTestCase(SimpleTestCase):

    # ---- the plain values -------------------------------------------------------------
    def test_every_value_buckets_to_itself(self):
        """The six breseq writes, plus the fall-through. Stated whole, so adding a token to the
        vocabulary without deciding where it ranks fails here."""
        for change in FUNCTIONAL_CHANGE_TYPE_LIST:
            self.assertEqual(change, functional_change_bucket(change))

    def test_the_vocabulary_is_in_severity_order(self):
        """The list *is* the hierarchy -- there is deliberately no second ordered tuple -- so the
        order is worth asserting rather than leaving to be read off the source."""
        self.assertEqual(
            ['nonsense', 'nonsynonymous', 'synonymous', 'noncoding', 'pseudogene',
             'intergenic', UNANNOTATED],
            FUNCTIONAL_CHANGE_TYPE_LIST)

    def test_a_nonsynonymous_change_is_not_read_as_synonymous(self):
        """`synonymous` is a substring of `nonsynonymous`, and matching on substrings is exactly
        what this replaced. Tokens are compared whole after splitting, so the containment is no
        longer something the list's order has to work around."""
        self.assertEqual('nonsynonymous', functional_change_bucket('nonsynonymous'))

    # ---- compounds resolve by severity ------------------------------------------------
    def test_a_compound_takes_the_more_severe_token(self):
        """`annotator.py:508` joins one value per overlapping gene, so a base in two reading
        frames can be silent in one and not the other."""
        self.assertEqual('nonsynonymous', functional_change_bucket('synonymous|nonsynonymous'))

    def test_the_answer_does_not_depend_on_gene_order(self):
        """Both spellings occur in the dev database -- 11 rows one way, 7 the other -- because the
        order is the order the genes were found in. An implementation that took the first token
        would answer differently for the same biology, and passes the test above."""
        self.assertEqual(functional_change_bucket('synonymous|nonsynonymous'),
                         functional_change_bucket('nonsynonymous|synonymous'))

    def test_nonsense_outranks_everything_it_is_found_with(self):
        for value in ('nonsense|synonymous', 'nonsense|nonsynonymous',
                      'synonymous|nonsense', 'pseudogene|nonsense'):
            self.assertEqual('nonsense', functional_change_bucket(value), value)

    def test_a_coding_change_outranks_a_pseudogene_one(self):
        """The one compound shape where the severe token is not a codon change in both frames."""
        self.assertEqual('nonsynonymous', functional_change_bucket('pseudogene|nonsynonymous'))

    def test_a_repeated_token_is_still_that_token(self):
        """`nonsynonymous|nonsynonymous` is the commonest compound in the dev database, 21 rows:
        two overlapping genes agreeing."""
        self.assertEqual('nonsynonymous', functional_change_bucket('nonsynonymous|nonsynonymous'))

    def test_noncoding_outranks_pseudogene(self):
        """A change in a tRNA or rRNA still touches a product; one in a pseudogene does not."""
        self.assertEqual('noncoding', functional_change_bucket('pseudogene|noncoding'))

    # ---- the ways there is no answer --------------------------------------------------
    def test_an_empty_value_is_unannotated(self):
        """Every non-SNP has one, and so does a SNP in a CDS's partial last codon."""
        self.assertEqual(UNANNOTATED, functional_change_bucket(''))

    def test_null_is_unannotated(self):
        """Two rows in the dev database predate the annotator and hold SQL NULL, which arrives
        here as None. `values_list` hands it over without complaint, so this must not raise."""
        self.assertEqual(UNANNOTATED, functional_change_bucket(None))

    def test_a_bare_separator_is_unannotated(self):
        """The join at `annotator.py:508` is unconditional, so a non-SNP inside two overlapping
        genes gets two empty values joined. An INS in the dev database is exactly this."""
        self.assertEqual(UNANNOTATED, functional_change_bucket('|'))

    def test_an_unknown_token_is_unannotated_rather_than_an_error(self):
        """A `.gd` annotated by a different breseq version can carry a value this vocabulary has
        never seen. Calling it unannotated is a better answer than 500ing a page over it."""
        self.assertEqual(UNANNOTATED, functional_change_bucket('coding'))

    def test_an_unknown_token_does_not_hide_a_known_one(self):
        self.assertEqual('synonymous', functional_change_bucket('coding|synonymous'))
