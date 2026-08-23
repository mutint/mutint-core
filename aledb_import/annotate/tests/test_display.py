"""breseq's display strings, where this port makes a choice of its own.

Most of display.py is a mechanical port and is covered through the annotator and
the Samples page. What is worth pinning here is the gene-list collapse: breseq has
two branches for it and the port originally took the wrong one.
"""

import unittest

from aledb_import.annotate.display import MAX_GENES_BEFORE_SUMMARY, add_html_fields


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
