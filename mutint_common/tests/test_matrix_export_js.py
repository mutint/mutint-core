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
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(mutint_common.__file__)),
                      "staticfiles", "js", "mutation_matrix_export.js")
SKIP_REASON = "node is not installed, so the Export tab's files cannot be built here"

_PROGRAM = """
const api = require(process.argv[1]);
let input = "";
process.stdin.on("data", chunk => { input += chunk; });
process.stdin.on("end", () => {
    const job = JSON.parse(input);
    process.stdout.write(JSON.stringify({
        csv: api.csv(job.collected),
        html: api.html(job.collected, job.options || {}),
        text: job.collected.rows.map(row => job.collected.columns.map(c => api.cellText(row, c))),
    }));
});
"""

COLUMNS = [
    {"kind": "text", "key": "gene", "title": "Gene", "className": "breseq-gene"},
    {"kind": "text", "key": "description", "title": "Description", "className": "breseq-description"},
    {"kind": "sample", "index": 0, "title": "Ara-1 500", "className": "breseq-sample sample-palette-0"},
    {"kind": "sample", "index": 1, "title": "Ara-2 500", "className": "breseq-sample sample-palette-1"},
]

ROWS = [
    {"gene": "<i>thrA</i>, <i>thrB</i>", "gene_text": "thrA, thrB",
     "description": '<a href="/x">frame, shift</a> <button>Show</button>'
                    '<span class="breseq_gene_list" hidden>yaaA, yaaB</span> a hidden word',
     "samples": [{"f": "42.0%", "s": 0.42, "u": "/browse/1", "p": True}, {"f": "✓", "s": None}]},
    {"gene": "<i>mutL</i>", "gene_text": "mutL", "description": 'say "hi"', "ancestral": True,
     "samples": [None, {"f": "100%", "s": 1}]},
]


def run(collected, options=None):
    completed = subprocess.run([NODE, "-e", _PROGRAM, SCRIPT],
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
        self.assertEqual(["42", "✓"], text[0][2:])
        self.assertEqual(["", "100"], text[1][2:])


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
        self.assertEqual("Gene,Description,Ara-1 500,Ara-2 500", lines[0])
        self.assertEqual("", lines[-1])
        self.assertEqual(4, len(lines))

    def test_a_field_with_a_comma_or_a_quote_is_quoted(self):
        lines = self.csv[1:].split("\r\n")
        self.assertEqual('"thrA, thrB","frame, shift yaaA, yaaB a hidden word",42,✓', lines[1])
        self.assertEqual('mutL,"say ""hi""",,100', lines[2])


@unittest.skipUnless(NODE, SKIP_REASON)
class HtmlTestCase(unittest.TestCase):

    def setUp(self):
        self.html = run({"columns": COLUMNS, "rows": ROWS},
                        {"title": "exp_1_showing", "styles": ".breseq-table th { color: red }",
                         "format": "bars", "view": "normal", "color": "treatment"})["html"]

    def test_it_is_a_standalone_document_carrying_the_pages_styles(self):
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn('<meta charset="utf-8">', self.html)
        self.assertIn("<title>exp_1_showing</title>", self.html)
        self.assertIn(".breseq-table th { color: red }", self.html)

    def test_the_table_wears_the_classes_the_stylesheet_keys_on(self):
        self.assertIn('<table class="breseq-table mutation-matrix-table dataTable freq-bars '
                      'view-normal color-treatment">', self.html)
        self.assertIn('<th class="breseq-sample sample-palette-0">'
                      '<span class="mutation-matrix-vertical">Ara-1 500</span></th>', self.html)

    def test_rows_are_striped_and_the_ancestral_one_is_marked(self):
        self.assertIn('<tr class="alternate_table_row_0">', self.html)
        self.assertIn('<tr class="alternate_table_row_1 ancestral_table_row">', self.html)

    def test_a_text_cell_keeps_its_markup_but_not_its_links_buttons_or_hiding(self):
        """A collapsed gene list opens, because the file has no script to open it; a link
        into this server becomes its words."""
        self.assertIn("<td class=\"breseq-gene\"><i>thrA</i>, <i>thrB</i></td>", self.html)
        self.assertNotIn("<a ", self.html)
        self.assertNotIn("<button", self.html)
        self.assertIn('<span class="breseq_gene_list">yaaA, yaaB</span> a hidden word', self.html)

    def test_a_sample_cell_is_the_pages_markup_as_a_span(self):
        self.assertIn('<span class="mutation-matrix-cell polymorphic" style="--f: 0.42" '
                      'title="42.0%"><span>42</span></span>', self.html)
        self.assertIn('style="--f: 1" title="✓"><span>✓</span>', self.html)
        self.assertIn('<td class="breseq-sample sample-palette-0"></td>', self.html)

    def test_text_is_escaped(self):
        self.assertIn('say "hi"', self.html)  # markup is the page's own; a quote is fine
