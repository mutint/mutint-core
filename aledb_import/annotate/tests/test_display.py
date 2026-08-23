"""breseq's display strings, where this port makes a choice of its own.

Most of display.py is a mechanical port and is covered through the annotator and
the Samples page. What is worth pinning here is the gene-list collapse: breseq has
two branches for it and the port originally took the wrong one.
"""

import unittest
import warnings

from bs4 import BeautifulSoup

from aledb_import.annotate.display import (
    MAX_GENES_BEFORE_SUMMARY, add_html_fields, text_from_html,
)


def _deletion_over(gene_count):
    genes = ",".join("gene%02d" % index for index in range(gene_count))
    entry = {"type": "DEL", "seq_id": "REL606", "position": "1", "size": "9",
             "gene_name": "[gene00]–[gene%02d]" % (gene_count - 1),
             "gene_product": genes, "gene_strand": ">"}
    add_html_fields(entry)
    return entry["html_gene_product"]


class GeneListCollapseTestCase(unittest.TestCase):

    def test_a_short_list_is_shown_in_full(self):
        product = _deletion_over(MAX_GENES_BEFORE_SUMMARY - 1)
        self.assertNotIn("breseq_gene_toggle", product)
        self.assertIn("<i>gene00</i>", product)

    def test_a_long_list_is_collapsed_behind_a_button(self):
        """breseq collapses past 15 genes; the port took its --no-javascript branch,
        so a deletion spanning hundreds of genes stretched the column instead."""
        product = _deletion_over(MAX_GENES_BEFORE_SUMMARY + 5)

        self.assertIn("<b>%d genes</b>" % (MAX_GENES_BEFORE_SUMMARY + 5), product)
        self.assertIn('<span class="breseq_gene_list" hidden>', product)
        self.assertIn('<button type="button" class="breseq_gene_toggle">Show</button>',
                      product)

    def test_the_threshold_is_breseqs(self):
        """`< 15`, so exactly 15 collapses -- output.cpp:4640."""
        self.assertNotIn("breseq_gene_toggle", _deletion_over(MAX_GENES_BEFORE_SUMMARY - 1))
        self.assertIn("breseq_gene_toggle", _deletion_over(MAX_GENES_BEFORE_SUMMARY))

    def test_the_names_are_still_there_for_a_reader_without_scripting(self):
        """breseq keeps a --no-javascript branch for this; the <noscript> copy is
        the same idea, and is why collapsing does not lose information."""
        product = _deletion_over(MAX_GENES_BEFORE_SUMMARY + 5)
        self.assertIn("<noscript>", product)
        self.assertEqual(2, product.count("<i>gene00</i>"))   # noscript + hidden span

    def test_it_does_not_emit_element_ids(self):
        """A delegated listener needs none, and this markup is embedded in a page
        display.py does not own -- breseq's gene_hide_<type>_<id> scheme assumes it
        is generating the whole document."""
        product = _deletion_over(MAX_GENES_BEFORE_SUMMARY + 5)
        self.assertNotIn("id=", product)
        self.assertNotIn("onclick", product)

    def test_it_does_not_use_bootstraps_hidden_class(self):
        """Bootstrap defines .hidden with !important, so a toggle cannot undo it."""
        product = _deletion_over(MAX_GENES_BEFORE_SUMMARY + 5)
        self.assertNotIn('class="hidden"', product)

    def test_the_class_names_survive_htmlize(self):
        """Every field here passes through htmlize(), which rewrites "-" as a
        non-breaking hyphen so a gene name like insB-14 cannot wrap. A hyphenated
        class name comes out as breseq&#8209;gene&#8209;list and matches no
        stylesheet -- caught exactly once, by this."""
        product = _deletion_over(MAX_GENES_BEFORE_SUMMARY + 5)
        self.assertNotIn("&#8209;", product.split("<noscript>")[0])
        self.assertIn('class="breseq_gene_list"', product)
        self.assertIn('class="breseq_gene_toggle"', product)


def _via_beautifulsoup(value):
    """The implementation text_from_html used for every input, kept here as the
    reference the short-circuit has to agree with."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return BeautifulSoup(value, "lxml").text.replace("\xa0", " ").strip()


# Shapes taken from a real database: an intergenic mutation joins two gene
# products with "/", which is what BeautifulSoup reads as a path separator.
TAGLESS = [
    "hypothetical protein/hypothetical protein",
    "nitrate/nitrite transporter",
    "potassium transporter/IS150 hypothetical protein",
    "intergenic&nbsp;(+6/&#8209;50)",
    "coding (14/1677 nt)",
    "predicted DNA&#8209;binding transcriptional regulator/hypothetical protein",
    "A&rarr;C",
    "&Delta;8,224&nbsp;bp",
    "+G",
]

TAGGED = [
    "<i>thrA</i>&nbsp;&rarr;",
    '<font class="snp_type_nonsynonymous">F239L</font>&nbsp;(TTT&rarr;TTG)&nbsp;',
    "<b>23 genes</b> <span hidden><i>manB</i>, <i>manC</i></span>",
]


class TextFromHtmlTestCase(unittest.TestCase):
    """Flattening an html_* field to the text stored on Mutation.

    BeautifulSoup guesses that a short, tagless string containing a path
    separator is a filename someone meant to open, and warns. Many of these
    fields are exactly that shape, so `./aledb reannotate` printed
    MarkupResemblesLocatorWarning while returning the right answer -- 682 of
    11,642 values on a real database.
    """

    def test_tagless_input_does_not_warn(self):
        for value in TAGLESS:
            with self.subTest(value=value):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    text_from_html(value)
                self.assertEqual(
                    [], [w for w in caught
                         if "MarkupResemblesLocator" in w.category.__name__])

    def test_it_still_agrees_with_beautifulsoup(self):
        """The short-circuit is why there is no warning, so it must not change a
        single answer: these feed Mutation.sequence_change and .protein_change,
        which are meant to keep their shape."""
        for value in TAGLESS + TAGGED:
            with self.subTest(value=value):
                self.assertEqual(_via_beautifulsoup(value), text_from_html(value))

    def test_entities_are_still_decoded_without_a_parser(self):
        self.assertEqual("A\u2192C", text_from_html("A&rarr;C"))
        self.assertEqual("\u03948,224 bp", text_from_html("&Delta;8,224&nbsp;bp"))

    def test_markup_is_still_stripped(self):
        self.assertEqual("thrA \u2192", text_from_html("<i>thrA</i>&nbsp;&rarr;"))

    def test_empty_input(self):
        self.assertEqual("", text_from_html(""))
        self.assertEqual("", text_from_html(None))
