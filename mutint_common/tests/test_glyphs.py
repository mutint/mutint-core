"""Which glyph each kind of mutation gets, and that every glyph has a symbol to draw."""

import os
import re

from django.test import SimpleTestCase

from mutint_common import glyphs
from mutint_common.glyphs import glyph_for, size_change


class GlyphTestCase(SimpleTestCase):
    def test_base_substitutions_by_functional_class(self):
        self.assertEqual(glyphs.CIRCLE, glyph_for("SNP", "synonymous", "snp_synonymous"))
        self.assertEqual(glyphs.SQUARE, glyph_for("SNP", "nonsynonymous", "snp_nonsynonymous"))
        self.assertEqual(glyphs.TOMBSTONE, glyph_for("SNP", "nonsense", "snp_nonsense"))
        self.assertEqual(glyphs.DIAMOND_DOWN, glyph_for("SNP", "intergenic", "snp_intergenic"))
        self.assertEqual(glyphs.DIAMOND, glyph_for("SNP", "noncoding", "snp_noncoding"))
        self.assertEqual(glyphs.DIAMOND, glyph_for("SNP", "pseudogene", "snp_pseudogene"))

    def test_a_compound_class_takes_the_most_severe(self):
        self.assertEqual(glyphs.TOMBSTONE,
                         glyph_for("SNP", "synonymous|nonsense", "snp_synonymous|nonsense"))

    def test_an_unannotated_snp_is_a_square(self):
        self.assertEqual(glyphs.SQUARE, glyph_for("SNP", "", ""))

    def test_indels_by_size(self):
        self.assertEqual(glyphs.TRIANGLE, glyph_for("DEL", "", "small_indel"))
        self.assertEqual(glyphs.TRAPEZOID_DOWN, glyph_for("DEL", "", "large_deletion"))
        self.assertEqual(glyphs.TRIANGLE_DOWN, glyph_for("INS", "", "small_indel"))
        self.assertEqual(glyphs.TRAPEZOID, glyph_for("INS", "", "large_insertion"))
        self.assertEqual(glyphs.TRIANGLE_DOWN, glyph_for("AMP", "", "small_indel"))
        self.assertEqual(glyphs.TRAPEZOID, glyph_for("AMP", "", "large_amplification"))

    def test_the_other_types(self):
        self.assertEqual(glyphs.BOWTIE, glyph_for("MOB", "", "mobile_element_insertion"))
        self.assertEqual(glyphs.PARALLELOGRAM, glyph_for("CON", "", "gene_conversion"))
        self.assertEqual(glyphs.PARALLELOGRAM, glyph_for("INT", "", "integration"))
        self.assertEqual(glyphs.BARBELL, glyph_for("INV", "", "inversion"))

    def test_a_substitution_is_drawn_by_its_net_change(self):
        self.assertEqual(glyphs.SQUARE, glyph_for("SUB", "", "small_indel", 0))
        self.assertEqual(glyphs.TRIANGLE_DOWN, glyph_for("SUB", "", "small_indel", 3))
        self.assertEqual(glyphs.TRIANGLE, glyph_for("SUB", "", "small_indel", -3))
        self.assertEqual(glyphs.TRAPEZOID, glyph_for("SUB", "", "large_substitution", 80))
        self.assertEqual(glyphs.TRAPEZOID_DOWN, glyph_for("SUB", "", "large_substitution", -80))

    def test_every_glyph_has_a_symbol_in_the_sprite(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "templates", "glyphs", "sprite.html")
        with open(path, encoding="utf-8") as handle:
            symbols = set(re.findall(r'<symbol id="glyph-([a-z-]+)"', handle.read()))
        self.assertEqual({name for name, _ in glyphs.GLYPHS}, symbols)

    def test_size_change_reads_the_record(self):
        self.assertEqual(2, size_change({"new_seq": "ACG", "size": 1}))
        self.assertEqual(-3, size_change({"size": 3}))
        self.assertEqual(0, size_change({}))
        self.assertEqual(0, size_change(None))
        self.assertEqual(0, size_change({"size": "lots"}))
