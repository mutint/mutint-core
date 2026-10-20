"""The Export tab's CSV and HTML, run under node.

`mutation_matrix_export.js` turns what `collect` took off the DataTable into the file the
reader asked for. `collect` itself needs a DataTable and a browser; what is pinned here is
the two pure halves over a hand-built collection -- the shape `collect` returns -- because
nothing on the server reads either file. Skipped, saying why, where node is not installed.
"""

import json
import os
import shutil
import subprocess
import unittest

import mutint_common

NODE = shutil.which("node")
JS = os.path.join(os.path.dirname(os.path.abspath(mutint_common.__file__)), "staticfiles", "js")
SCRIPT = os.path.join(JS, "mutation_matrix_export.js")
SVG_SCRIPT = os.path.join(JS, "mutation_matrix_svg.js")
SKIP_REASON = "node is not installed, so the Export tab's files cannot be built here"

_PROGRAM = """
const api = require(process.argv[1]);
let input = "";
process.stdin.on("data", chunk => { input += chunk; });
process.stdin.on("end", () => {
    const job = JSON.parse(input);
    const svg = require(process.argv[2]);
    process.stdout.write(JSON.stringify({
        csv: api.csv(job.collected),
        html: api.html(job.collected, job.options || {}),
        text: job.collected.rows.map(row => job.collected.columns.map(c => api.cellText(row, c))),
        heat: [svg.heatColor(0), svg.heatColor(0.42), svg.heatColor(1)],
        tint: svg.mixSrgb("#ffffff", "#337ab7", 0.42),
        halfTint: svg.mixSrgb("#ffffff", "#337ab7", 0.21),
    }));
});
"""

COLUMNS = [
    {"kind": "text", "key": "gene", "title": "Gene", "className": "breseq-gene"},
    {"kind": "text", "key": "description", "title": "Description", "className": "breseq-description"},
    {"kind": "text", "key": "annotation", "title": "Annotation", "className": "breseq-annotation"},
    {"kind": "sample", "index": 0, "title": "Ara-1 500", "className": "breseq-sample sample-palette-0",
     "color": "#337ab7"},
    {"kind": "sample", "index": 1, "title": "Ara-2 500", "className": "breseq-sample sample-palette-1",
     "color": "#d9534f"},
]

ROWS = [
    {"gene": "<i>thrA</i>, <i>thrB</i>", "gene_text": "thrA, thrB",
     "description": '<a href="/x">frame, shift</a> <button>Show</button>'
                    '<span class="breseq_gene_list" hidden>yaaA, yaaB</span> a hidden word',
     "annotation": 'I34S <span class="snp_type_synonymous">(ATC→AGC)</span>',
     "samples": [{"f": "42.0%", "s": 0.42, "u": "/browse/1", "p": True}, {"f": "✓", "s": None}]},
    {"gene": "<i>mutL</i>", "gene_text": "mutL", "description": 'say "hi"', "annotation": "",
     "ancestral": True, "samples": [None, {"f": "100%", "s": 1}]},
]


def run(collected, options=None):
    completed = subprocess.run([NODE, "-e", _PROGRAM, SCRIPT, SVG_SCRIPT],
                               input=json.dumps({"collected": collected, "options": options}),
                               capture_output=True, text=True, timeout=60, check=True)
    return json.loads(completed.stdout)


@unittest.skipUnless(NODE, SKIP_REASON)
class CellTextTestCase(unittest.TestCase):

    def test_a_text_twin_wins_over_the_markup(self):
        text = run({"columns": COLUMNS, "rows": ROWS})["text"]
        self.assertEqual("thrA, thrB", text[0][0])

    def test_without_one_the_tags_go_and_every_word_stays(self):
        """The hidden half of a collapsed gene list is still the gene list; the Show button is
        not. Buttons' own tag-stripping behaved the same way, and the CSV keeps it."""
        text = run({"columns": COLUMNS, "rows": ROWS})["text"]
        self.assertEqual("frame, shift yaaA, yaaB a hidden word", text[0][1])

    def test_a_sample_cell_is_the_bare_number_it_shows(self):
        text = run({"columns": COLUMNS, "rows": ROWS})["text"]
        self.assertEqual(["42", "✓"], text[0][3:])
        self.assertEqual(["", "100"], text[1][3:])


@unittest.skipUnless(NODE, SKIP_REASON)
class CsvTestCase(unittest.TestCase):

    def setUp(self):
        self.csv = run({"columns": COLUMNS, "rows": ROWS})["csv"]

    def test_it_starts_with_the_byte_order_mark(self):
        """Excel reads a .csv in the legacy code page without it, and the gene column's arrows
        and the check mark come out as nonsense -- the rule `spreadsheet.py` states."""
        self.assertTrue(self.csv.startswith("﻿"))

    def test_the_header_is_the_titles_and_the_lines_end_crlf(self):
        lines = self.csv[1:].split("\r\n")
        self.assertEqual("Gene,Description,Annotation,Ara-1 500,Ara-2 500", lines[0])
        self.assertEqual("", lines[-1])
        self.assertEqual(4, len(lines))

    def test_a_field_with_a_comma_or_a_quote_is_quoted(self):
        lines = self.csv[1:].split("\r\n")
        self.assertEqual('"thrA, thrB","frame, shift yaaA, yaaB a hidden word",I34S (ATC→AGC),42,✓',
                         lines[1])
        self.assertEqual('mutL,"say ""hi""",,,100', lines[2])


@unittest.skipUnless(NODE, SKIP_REASON)
class HtmlTestCase(unittest.TestCase):
    """The file carries its formatting inline on every cell, because Excel's HTML importer
    reads inline styles, `<i>` and `mso-rotate` and nothing of custom properties,
    pseudo-elements or `color-mix`. A frequency cell is a number on a shaded cell."""

    def build(self, **options):
        base = {"title": "exp_1_showing", "styles": ".breseq-table th { color: red }",
                "format": "number", "view": "normal", "color": "treatment"}
        base.update(options)
        return run({"columns": COLUMNS, "rows": ROWS}, base)

    def setUp(self):
        self.result = self.build()
        self.html = self.result["html"]

    def test_it_is_a_standalone_document_carrying_the_pages_styles(self):
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn('<meta charset="utf-8">', self.html)
        self.assertIn("<title>exp_1_showing</title>", self.html)
        self.assertIn(".breseq-table th { color: red }", self.html)

    def test_the_table_wears_the_padding_and_colouring_classes_and_no_frequency_one(self):
        """`freq-<f>` would describe bars or a hidden number the file does not draw."""
        self.assertIn('<table class="breseq-table mutation-matrix-table dataTable view-normal '
                      'color-treatment">', self.html)
        self.assertNotIn("freq-", self.html.split("<table")[1])

    def test_headers_carry_their_colours_inline_and_sample_names_rotate_for_excel(self):
        self.assertIn('<th class="breseq-gene" style="background-color: #4b5158; color: #ffffff; '
                      'font-weight: bold; text-align: center">Gene</th>', self.html)
        self.assertIn('<th class="breseq-description" style="background-color: #4b5158; color: #ffffff; '
                      'font-weight: bold; text-align: left">Description</th>', self.html)
        self.assertIn('<th class="breseq-sample sample-palette-1" style="background-color: #d9534f; '
                      'color: #ffffff; font-weight: bold; text-align: center; vertical-align: bottom; '
                      'mso-rotate: 90"><span class="mutation-matrix-vertical">Ara-2 500</span></th>',
                      self.html)

    def test_rows_are_striped_and_the_ancestral_one_is_marked_on_every_cell(self):
        """Excel reads a cell's own style, not its row's class, so the ground is on each td."""
        self.assertIn('<tr class="alternate_table_row_0">', self.html)
        self.assertIn('<tr class="alternate_table_row_1 ancestral_table_row">', self.html)
        self.assertIn('<td class="breseq-gene" style="background-color: #ffffff; text-align: center">'
                      "<i>thrA</i>, <i>thrB</i></td>", self.html)
        self.assertIn('<td class="breseq-gene" style="background-color: #ffdada; text-align: center">'
                      "<i>mutL</i></td>", self.html)

    def test_a_text_cell_keeps_its_markup_but_not_its_links_buttons_or_hiding(self):
        """A collapsed gene list opens, because the file has no script to open it; a link
        into this server becomes its words; a SNP type's colour goes inline."""
        self.assertNotIn("<a ", self.html)
        self.assertNotIn("<button", self.html)
        self.assertIn('<span class="breseq_gene_list">yaaA, yaaB</span> a hidden word', self.html)
        self.assertIn('<span class="snp_type_synonymous" style="color: green">(ATC→AGC)</span>',
                      self.html)

    def test_a_number_cell_is_plain_text_on_the_rows_ground(self):
        self.assertIn('<td class="breseq-sample sample-palette-0" style="background-color: #ffffff; '
                      'color: #333333; text-align: center" title="42.0%">42</td>', self.html)
        self.assertIn('title="✓">✓</td>', self.html)
        # The absent cell: empty, on the ancestral ground.
        self.assertIn('<td class="breseq-sample sample-palette-0" style="background-color: #ffdada; '
                      'color: #333333; text-align: center"></td>', self.html)
        self.assertNotIn("--f", self.html)
        self.assertNotIn("mutation-matrix-cell", self.html)

    def test_heat_shades_the_cell_and_whitens_the_digits_once_dark(self):
        result = self.build(format="heat")
        heat0, heat42, heat1 = result["heat"]
        self.assertEqual("#ffffd4", heat0)
        self.assertEqual("#253494", heat1)
        self.assertIn('style="background-color: %s; color: #333333; text-align: center" title="42.0%%">42</td>'
                      % heat42, result["html"])
        self.assertIn('style="background-color: #253494; color: #ffffff; text-align: center" '
                      'title="100%">100</td>', result["html"])
        self.assertEqual(result["html"], self.build(format="both")["html"])

    def test_bars_become_the_population_colour_tinted_by_frequency(self):
        """A spreadsheet cannot draw a bar. A polymorphic call takes the bar's half tone."""
        result = self.build(format="bars")
        self.assertIn('style="background-color: %s; color: #333333; text-align: center" title="42.0%%">42</td>'
                      % result["halfTint"], result["html"])
        self.assertNotIn(result["tint"], result["html"])
        # A full call in the second column takes that column's own colour, whole.
        self.assertIn('style="background-color: #d9534f; color: #333333; text-align: center" '
                      'title="100%">100</td>', result["html"])


@unittest.skipUnless(NODE, SKIP_REASON)
class SvgModuleTestCase(unittest.TestCase):
    """The export module takes its colours from the SVG module, so that one must load under
    node too -- which it did not while it made a canvas at load."""

    def test_it_loads_and_mixes(self):
        result = run({"columns": COLUMNS, "rows": ROWS})
        self.assertEqual("#ffffd4", result["heat"][0])
        self.assertEqual("#253494", result["heat"][2])
        self.assertRegex(result["heat"][1], r"^#[0-9a-f]{6}$")
        self.assertEqual("#a9c7e1", result["tint"])
