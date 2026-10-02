"""The mutation matrix: what a row is, what a cell is, and what the partial renders.

Built from an imported breseq folder, the way the rest of this app's tests are, so the rows
come out of the same importer every page reads.
"""

import re
import shutil
import tempfile
from collections import OrderedDict

from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.middleware import SessionMiddleware
from django.template import loader
from django.test import RequestFactory, TestCase, override_settings

from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample import mutation_matrix
from mutint_sample.models import Mutation, MutationCall
from mutint_sample.util import get_all_calls_filtered, get_ordered_sample_dict

GD_FIXED = """#=GENOME_DIFF\t1.0
#=REFSEQ\ttest_ref
SNP\t1\t.\ttest_ref\t100\tA\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=1
AMP\t2\t.\ttest_ref\t120\t10\t3\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=1
"""

GD_POLYMORPHIC = """#=GENOME_DIFF\t1.0
#=REFSEQ\ttest_ref
SNP\t1\t.\ttest_ref\t100\tA\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=0.42
"""


class _Fixture(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "1-1-1-1", gd_text=GD_FIXED)
        breseq_fixture.write_sample(self.drop, "1-2-1-1", gd_text=GD_POLYMORPHIC)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")
        from mutint_experiment.models import Experiment
        self.experiment = Experiment.objects.get()
        self.sample_dict = get_ordered_sample_dict(self.experiment.id)
        self.calls = get_all_calls_filtered(self.experiment.id)

    def matrix(self, **kwargs):
        return mutation_matrix.build_matrix(self.calls, self.sample_dict,
                                            experiment=self.experiment, **kwargs)

    def row(self, matrix, position):
        return [r for r in matrix.rows if r["position_sort"] == position][0]


class WhatTheBrowserDecidesByTestCase(_Fixture):
    """Compare decides in the browser, so the matrix carries what it decides by: each
    column's population, treatment, time point and type, each row's gene names, and which
    cells have no recorded frequency."""

    def test_each_sample_column_says_what_can_hide_it(self):
        matrix = self.matrix()
        for column in matrix.samples:
            sample = self.sample_dict[column.id]
            self.assertEqual(sample.population_id, column.population_id)
            self.assertEqual(sample.population_name, column.population)
            self.assertEqual(sample.time_point, column.time_point)
            self.assertEqual(sample.is_clonal, column.clonal)
        self.assertEqual((1.0, 2.0), matrix.time_points)
        self.assertTrue(matrix.offers_time)
        self.assertEqual(1, len(matrix.populations))
        self.assertEqual((), matrix.treatments)
        self.assertTrue(matrix.has_untreated)

    def test_a_row_carries_its_genes_as_the_filter_splits_them(self):
        self.assertEqual(["thrA"], self.row(self.matrix(), 100)["genes"])

    def test_a_cell_with_no_recorded_frequency_says_so(self):
        """A frequency cutoff never removes such a call -- the server's NULL never matched
        its exclusion -- and `s` alone, drawn as 1, cannot tell the script that."""
        call = [c for c in self.calls if c.mutation.start_position == 100][0]
        call.frequency = None
        cell = mutation_matrix._sample_cell(call, None)
        self.assertTrue(cell["n"])
        call.frequency = 0.5
        self.assertNotIn("n", mutation_matrix._sample_cell(call, None))

    def test_each_header_carries_them(self):
        from django.template import engines
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        html = engines["django"].from_string(
            "{% load mutation_matrix %}{% mutation_matrix matrix %}").render(
            {"matrix": self.matrix()}, request)
        for column in self.matrix().samples:
            self.assertIn('data-sample="%d" data-index="%d" data-population="%d" data-treatment="" '
                          'data-time="%r" data-clonal="1"'
                          % (column.id, column.index, column.population_id, column.time_point), html)
        # Search renders the tag alone, and gets none of Compare's layers.
        for role in ("populations", "treatments", "sample-types", "time", "view-filter"):
            self.assertNotIn('data-role="%s"' % role, html)


class ColumnsTestCase(_Fixture):
    def test_the_descriptive_columns_are_the_per_sample_tables_minus_freq(self):
        keys = [c.key for c in self.matrix().columns]
        self.assertEqual(["type", "seq_id", "position", "mutation", "annotation", "gene",
                          "description", "genes_inactivated", "genes_overlapping",
                          "genes_promoter", "locus_tags_inactivated", "locus_tags_overlapping",
                          "locus_tags_promoter", "single_gene_affected",
                          "single_locus_tag_affected"], keys)
        self.assertNotIn("freq", keys)

    def test_every_column_carries_a_breseq_class(self):
        for column in self.matrix().columns:
            self.assertTrue(column.css_class.startswith("breseq-"), column.key)

    def test_description_and_the_gene_lists_are_hidden_by_default(self):
        hidden = [c.key for c in self.matrix().columns if not c.default_visible]
        self.assertEqual(["description"] + [c.key for c in mutation_matrix.GENE_LIST_COLUMNS],
                         hidden)

    def test_one_sample_column_per_listed_sample_in_order(self):
        matrix = self.matrix()
        self.assertEqual([s.id for s in self.sample_dict.values()],
                         [s.id for s in matrix.samples])
        self.assertEqual(list(range(len(self.sample_dict))), [s.index for s in matrix.samples])
        self.assertEqual(len(self.sample_dict) + len(mutation_matrix.DESCRIPTIVE), matrix.width)


class RowsTestCase(_Fixture):
    def test_each_row_has_a_cell_per_sample(self):
        matrix = self.matrix()
        self.assertTrue(matrix.rows)
        for row in matrix.rows:
            self.assertEqual(len(matrix.samples), len(row["samples"]))

    def test_a_shared_mutation_is_one_row_with_two_cells(self):
        row = self.row(self.matrix(), 100)
        fixed, polymorphic = row["samples"]
        self.assertEqual("100%", fixed["f"])
        self.assertEqual(1.0, fixed["s"])
        self.assertFalse(fixed["p"])
        self.assertEqual("42.0%", polymorphic["f"])
        self.assertAlmostEqual(0.42, polymorphic["s"])
        self.assertTrue(polymorphic["p"])

    def test_an_absent_sample_is_a_null_cell(self):
        row = self.row(self.matrix(), 120)     # the AMP, called in one sample only
        self.assertIsNotNone(row["samples"][0])
        self.assertIsNone(row["samples"][1])

    def test_a_cell_links_to_the_browser_only_when_the_sample_has_reads(self):
        row = self.row(self.matrix(), 100)
        self.assertIn("/mutations/browse?mutation_call_id=", row["samples"][0]["u"])
        first = list(self.sample_dict.values())[0]
        first.bam_stored = False
        row = self.row(self.matrix(), 100)
        self.assertNotIn("u", row["samples"][0])

    def test_the_type_links_to_the_browser_showing_every_carrying_sample(self):
        # At the leftmost sample with reads, asking for all the mutant samples.
        row = self.row(self.matrix(), 100)
        self.assertEqual(row["samples"][0]["u"] + "&samples=mutant", row["type_url"])
        first, second = self.sample_dict.values()
        first.bam_stored = False
        row = self.row(self.matrix(), 100)
        self.assertEqual(row["samples"][1]["u"] + "&samples=mutant", row["type_url"])
        second.bam_stored = False
        self.assertIsNone(self.row(self.matrix(), 100)["type_url"])

    def test_a_present_call_with_no_frequency_shows_the_mark(self):
        # A hand-added mutation need not claim a frequency.
        mutation = Mutation.objects.create(
            experiment=self.experiment, start_position=4242, seq_id="test_ref",
            mutation_type="SNP", sequence_change="A->G", gene="thrA")
        MutationCall.objects.create(sample=list(self.sample_dict.values())[0],
                                    mutation=mutation, present=True, frequency=None)
        self.calls = get_all_calls_filtered(self.experiment.id)
        cell = self.row(self.matrix(), 4242)["samples"][0]
        self.assertEqual(mutation_matrix.PRESENT_MARK, cell["f"])
        self.assertEqual(1.0, cell["s"])

    def test_a_mutation_carried_only_by_an_unlisted_sample_has_no_row(self):
        only_first = OrderedDict(list(self.sample_dict.items())[:1])
        matrix = mutation_matrix.build_matrix(self.calls, only_first, experiment=self.experiment)
        positions = {r["position_sort"] for r in matrix.rows}
        self.assertIn(100, positions)
        self.assertIn(120, positions)
        # And a call recorded as looked-for-and-absent does not make a row either.
        mutation = Mutation.objects.create(
            experiment=self.experiment, start_position=9000, seq_id="test_ref",
            mutation_type="SNP", sequence_change="A->G", gene="thrA")
        MutationCall.objects.create(sample=list(self.sample_dict.values())[0],
                                    mutation=mutation, present=False)
        self.calls = get_all_calls_filtered(self.experiment.id)
        self.assertNotIn(9000, {r["position_sort"] for r in self.matrix().rows})

    def test_rows_are_ordered_by_reference_then_position(self):
        positions = [r["position_sort"] for r in self.matrix().rows]
        self.assertEqual(sorted(positions), positions)

    def test_the_descriptive_half_is_the_per_sample_tables(self):
        row = self.row(self.matrix(), 100)
        self.assertEqual("SNP", row["type"])
        self.assertEqual("test_ref", row["seq_id_text"])
        self.assertIn("thrA", row["gene"])
        self.assertIn("aspartokinase", row["description"])
        self.assertTrue(row["annotated"])

    def test_the_gene_lists_are_the_annotations_as_markup_and_as_text(self):
        """breseq's `genes_inactivated` and siblings, read from the stored annotation: markup
        for the cell, and `<key>_text` beside it, which the CSV export reads instead of a
        collapsed list's Show button."""
        mutation = Mutation.objects.get(start_position=100)
        mutation.annotation = dict(mutation.annotation, genes_inactivated="thrA,thrB",
                                   locus_tags_inactivated="b0001,b0002",
                                   genes_promoter="")
        mutation.save()
        self.calls = get_all_calls_filtered(self.experiment.id)
        row = self.row(self.matrix(), 100)
        self.assertEqual("<i>thrA</i>, <i>thrB</i>", row["genes_inactivated"])
        self.assertEqual("thrA, thrB", row["genes_inactivated_text"])
        self.assertEqual("b0001, b0002", row["locus_tags_inactivated"])
        self.assertEqual("", row["genes_promoter"])
        self.assertEqual("", row["genes_promoter_text"])

    def test_the_reference_links_to_ncbi_and_says_it_is_unverified(self):
        row = self.row(self.matrix(), 100)
        self.assertIn("/mutations/ncbi?mutation_id=%d" % row["id"], row["seq_id_url"])
        self.assertEqual(mutation_matrix.UNVERIFIED_TITLE, row["seq_id_title"])

    def test_labels_are_qualified_on_request(self):
        plain = [s.label for s in self.matrix().samples]
        qualified = [s.label for s in self.matrix(labels="qualified").samples]
        for p, q in zip(plain, qualified):
            self.assertTrue(q.endswith(p))
            self.assertTrue(q.startswith(self.experiment.name))

    def test_the_types_are_the_rows_types_sorted(self):
        self.assertEqual(("AMP", "SNP"), self.matrix().types)

    def test_the_references_are_the_rows_seq_ids_sorted(self):
        """For the References menu: the contigs the rows are on, an empty name left out."""
        self.assertEqual(("test_ref",), self.matrix().seq_ids)
        first = list(self.sample_dict.values())[0]
        for seq_id, position in (("a_plasmid", 7), ("", 8)):
            mutation = Mutation.objects.create(
                experiment=self.experiment, start_position=position, seq_id=seq_id,
                mutation_type="SNP", sequence_change="A->G", gene="thrA")
            MutationCall.objects.create(sample=first, mutation=mutation, present=True,
                                        frequency=1.0)
        self.calls = get_all_calls_filtered(self.experiment.id)
        self.assertEqual(("a_plasmid", "test_ref"), self.matrix().seq_ids)

    def test_no_experiment_means_no_experiment_id(self):
        matrix = mutation_matrix.build_matrix(self.calls, self.sample_dict)
        self.assertIsNone(matrix.experiment_id)

    def test_the_palette_colors_by_population_in_order_of_first_appearance(self):
        seen, expected = [], []
        for sample in self.sample_dict.values():
            if sample.population_id not in seen:
                seen.append(sample.population_id)
            expected.append(seen.index(sample.population_id))
        self.assertEqual(expected, [s.palette for s in self.matrix().samples])
        self.assertEqual([0, 1, 0, 2], mutation_matrix.palette_indexes([5, 7, 5, 9]))
        size = mutation_matrix.PALETTE_SIZE
        self.assertEqual(list(range(size)) + [0],
                         mutation_matrix.palette_indexes(range(size + 1)))

    def test_row_sets_annotate_the_rows_and_are_counted_by_row(self):
        """A set names mutations; the matrix says which rows fall in it, and counts only the
        rows -- an id no listed sample carries is not a row and is not counted."""
        snp = Mutation.objects.get(start_position=100)
        amp = Mutation.objects.get(start_position=120)
        sets = (mutation_matrix.RowSet("fixed", "Fixed", frozenset({snp.id, 999999})),
                mutation_matrix.RowSet("both", "Both", frozenset({snp.id, amp.id})))
        matrix = self.matrix(sets=sets)

        self.assertEqual(["fixed", "both"], self.row(matrix, 100)["sets"])
        self.assertEqual(["both"], self.row(matrix, 120)["sets"])
        self.assertEqual([("fixed", "Fixed", 1), ("both", "Both", 2)],
                         [(s.key, s.label, s.count) for s in matrix.sets])

    def test_without_sets_the_rows_carry_none(self):
        matrix = self.matrix()
        self.assertEqual((), matrix.sets)
        self.assertNotIn("sets", self.row(matrix, 100))

    def test_ancestral_ids_mark_the_rows_and_filter_nothing(self):
        """The script tints from the mark; the rows are whatever the caller handed in."""
        snp = Mutation.objects.get(start_position=100)
        matrix = self.matrix(ancestral_mutation_ids=frozenset({snp.id}))
        self.assertTrue(self.row(matrix, 100)["ancestral"])
        self.assertFalse(self.row(matrix, 120)["ancestral"])
        self.assertEqual(len(self.matrix().rows), len(matrix.rows))
        self.assertNotIn("ancestral", self.row(self.matrix(), 100))

    def test_each_sample_links_to_its_own_mutations_page(self):
        """The experiment given, when there is one; the sample's own when there is not --
        the cross-experiment page has none to give, and the link must still be right."""
        for matrix in (self.matrix(), mutation_matrix.build_matrix(self.calls, self.sample_dict)):
            for sample in matrix.samples:
                self.assertEqual("/mutations/breseq?experiment_id=%d&sample_id=%d"
                                 % (self.experiment.id, sample.id), sample.url)


class PartialTestCase(_Fixture):
    def _render(self, user=None, **extra):
        request = RequestFactory().get("/")
        request.user = user or AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        context = {"experiment_id": self.experiment.id, "matrix": self.matrix(),
                   "view_filter_state": {"min_freq": None, "max_freq": None, "genes": []},
                   "empty_message": "Nothing."}
        context.update(extra)
        return loader.get_template("mutation_matrix/page.html").render(context, request)

    def test_the_header_has_one_th_per_column_each_with_its_class(self):
        html = self._render()
        self.assertEqual(len(mutation_matrix.DESCRIPTIVE) + len(self.sample_dict),
                         html.count("<th "))
        for column in mutation_matrix.DESCRIPTIVE:
            self.assertIn('<th class="%s" data-key="%s"' % (column.css_class, column.key), html)
        # Each sample header carries both palettes: the population's, which colors it by
        # default, and the treatment's, which colors it when the reader asks.
        self.assertEqual(len(self.sample_dict),
                         html.count('class="breseq-sample sample-palette-0 treatment-palette-none"'))

    def test_the_menus_list_columns_and_samples(self):
        html = self._render()
        self.assertIn('data-role="columns"', html)
        self.assertIn('data-role="samples"', html)
        self.assertIn('<li data-value="description">', html)   # not active
        self.assertIn('<li data-value="gene" class="active">', html)
        for sample in self.sample_dict.values():
            self.assertIn('<li data-value="%d" class="active">' % sample.id, html)
        self.assertIn('data-samples="all"', html)
        self.assertIn('data-samples="none"', html)
        # And the third menu: the mutation types this table holds, with its two presets.
        self.assertIn('data-role="types"', html)
        self.assertIn('<li data-value="SNP" class="active">', html)
        self.assertIn('<li data-value="AMP" class="active">', html)
        self.assertIn('data-types="all"', html)
        self.assertIn('data-types="none"', html)
        # And the fourth: the reference sequences the rows are on, the same partial the
        # per-sample Mutations page renders.
        self.assertIn('data-role="references"', html)
        self.assertIn('<li data-value="test_ref" class="active">', html)
        self.assertIn('data-role="reference-count">1</span>', html)
        self.assertIn('data-references="all"', html)
        self.assertIn('data-references="none"', html)

    def test_the_frequency_display_menu_offers_its_four_formats(self):
        html = self._render()
        self.assertIn('data-role="frequency"', html)
        for value in ("number", "bars", "heat", "both"):
            self.assertIn('<li data-value="%s"' % value, html)
        self.assertIn('<li data-value="number" class="active">', html)
        self.assertIn('data-role="frequency-legend"', html)

    def test_the_show_menu_renders_only_when_the_matrix_has_sets(self):
        """Search and a plain Compare offer no sets and get no menu; a page that hands sets
        over gets All plus one entry per set, each with its count."""
        self.assertNotIn('data-role="show"', self._render())
        snp = Mutation.objects.get(start_position=100)
        sets = (mutation_matrix.RowSet("fixed", "Fixed", frozenset({snp.id})),)
        html = self._render(matrix=self.matrix(sets=sets))
        self.assertIn('data-role="show"', html)
        self.assertIn('<li data-value="" class="active"><a href="#">All (<span data-role="all-count">2</span>)</a></li>', html)
        self.assertIn('<li data-value="fixed"><a href="#">Fixed (1)</a></li>', html)
        self.assertIn('data-role="show-label">All<', html)

    def test_a_client_set_is_offered_and_counted_by_the_browser(self):
        """Compare's sets are decided in the browser: the menu carries the entry, marked
        `data-client-set`, with a count the script fills in, and the server sends no ids."""
        html = self._render(matrix=self.matrix(
            client_sets=(mutation_matrix.ClientSet("convergent", "Convergent"),)))
        self.assertIn('<li data-value="convergent" data-client-set><a href="#">Convergent '
                      '(<span data-role="set-count">0</span>)</a></li>', html)

    def test_a_page_that_extends_this_one_can_add_controls_and_a_summary(self):
        """The two blocks a plugin fills -- the controls of its row sets beside the Show menu,
        and a summary under the sets' own sentences, all on the Sets tab -- so it extends the
        page rather than copying it. There is no form and no Apply: every control works in
        the browser."""
        source = loader.get_template("mutation_matrix/page.html").template.source
        start = source.index('id="mutation_matrix-pane-sets"')
        sets_pane = source[start:source.index("{% endif %}", start)]
        self.assertIn("_show_menu.html", sets_pane)
        self.assertIn("{% block matrix_controls %}{% endblock %}", sets_pane)
        self.assertIn('data-role="set-notes"', sets_pane)
        self.assertIn("{% block matrix_summary %}{% endblock %}", sets_pane)
        self.assertNotIn("<form", source)
        self.assertNotIn('value="Apply"', source)

    def test_the_controls_are_tabs_in_order(self):
        """Populations, Samples, Time Points, Mutations, References, Columns, Frequencies,
        Export. Treatments only where some sample carries one and Sets only with sets, neither
        of which this fixture has; its two samples are
        at time points 1 and 2, so Time has a range to offer. Mutations is not offered."""
        html = self._render()
        self.assertIn('class="nav nav-tabs"', html)
        self.assertIn('data-control-tabs="mutation_matrix"', html)
        keys = ["populations", "samples", "time", "types", "references", "columns",
                "frequency", "export"]
        self.assertEqual(keys, re.findall(r'data-toggle="tab" data-tab="(\w+)"', html))
        for key, label in (("time", "Time Points"), ("types", "Mutations"),
                           ("references", "References"), ("columns", "Columns"),
                           ("frequency", "Frequencies")):
            self.assertIn('data-tab="%s" href="#mutation_matrix-pane-%s">%s</a>' % (key, key, label), html)
        for key in keys:
            self.assertIn('id="mutation_matrix-pane-%s"' % key, html)
        self.assertNotIn("pane-filter", html)
        self.assertIn('<li class="active"><a data-toggle="tab" data-tab="populations"', html)
        self.assertIn('class="tab-pane active" id="mutation_matrix-pane-populations"', html)
        self.assertIn('data-mutation-matrix-controls="mutation-matrix"', html)

    def test_each_control_sits_in_its_own_pane(self):
        html = self._render(matrix=self.matrix(
            client_sets=(mutation_matrix.ClientSet("convergent", "Convergent"),)),
            ancestor={"name": "REL606", "sample_id": 1})
        def pane(key):
            start = html.index('id="mutation_matrix-pane-%s"' % key)
            end = html.find('class="tab-pane', start)
            return html[start:end if end > 0 else html.index("<table", start)]
        populations = pane("populations")
        self.assertIn('data-role="populations"', populations)
        self.assertIn('data-color="population" checked', populations)
        samples = pane("samples")
        self.assertIn('data-role="samples"', samples)
        self.assertIn('data-role="sample-types"', samples)
        types = pane("types")
        self.assertIn('data-role="types"', types)
        self.assertIn('data-role="ancestral-toggle"', types)
        self.assertNotIn('data-role="ancestral-summary"', html)
        self.assertIn('data-role="references"', pane("references"))
        for role in ("show", "set-notes"):
            self.assertIn('data-role="%s"' % role, pane("sets"))
        columns = pane("columns")
        self.assertIn('data-role="columns"', columns)
        self.assertIn("Cell padding:", columns)
        self.assertIn('data-role="view-control"', columns)
        frequency = pane("frequency")
        for role in ("frequency-control", "view-filter", "filter-min", "filter-max",
                     "filter-summary"):
            self.assertIn('data-role="%s"' % role, frequency)
        # The ignored-genes box is not offered, so the script applies no gene list here.
        self.assertNotIn('data-role="filter-genes"', html)
        # Empty until the script moves DataTables' Export buttons into it.
        self.assertIn('data-role="export"', pane("export"))
        # The page rendered the panes; the tag did not render them again.
        self.assertEqual(1, html.count('data-role="columns"'))

    def test_the_tag_alone_renders_the_matrix_tabs(self):
        """Search renders the tag with no page around it and still gets a strip -- the
        matrix's own tabs, and none of Compare's."""
        from django.template import engines
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        html = engines["django"].from_string(
            "{% load mutation_matrix %}{% mutation_matrix matrix %}").render(
            {"matrix": self.matrix()}, request)
        self.assertEqual(["samples", "types", "references", "columns", "frequency", "export"],
                         re.findall(r'data-toggle="tab" data-tab="(\w+)"', html))
        self.assertIn('<li class="active"><a data-toggle="tab" data-tab="samples"', html)
        self.assertNotIn("pane-filter", html)
        self.assertNotIn('data-role="view-filter"', html)
        self.assertNotIn("data-color", html)
        self.assertIn('data-mutation-matrix-controls="mutation-matrix"', html)

    def test_a_signed_in_readers_tab_is_embedded(self):
        from mutint_common.preferences import set_preference
        set_preference(self.user, "mutation_matrix.tab", {"tab": "references"})
        html = self._render(user=self.user)
        self.assertIn("mutation_matrix.tab", html)
        self.assertIn('data-prefs-id="mutation-matrix-prefs"', html)

    def test_the_ancestral_button_appears_with_an_ancestor(self):
        """The Show/Hide button sits on the Mutations tab, offered only when the experiment
        designates an ancestor -- otherwise there is nothing for it to show. It carries no
        count and no sentence: the rows it draws are tinted and say what they are."""
        html = self._render()
        self.assertNotIn('data-role="ancestral-toggle"', html)
        html = self._render(ancestor={"name": "REL606", "sample_id": 1})
        self.assertIn('data-role="ancestral-toggle">Show ancestral mutations</button>', html)
        self.assertNotIn('data-tab="ancestral"', html)
        self.assertNotIn("REL606", html)

    def test_treatments_and_time_appear_when_they_can_do_something(self):
        from mutint_sample.models import Sample
        samples = list(Sample.objects.filter(pk__in=self.sample_dict.keys()).order_by("pk"))
        samples[0].treatment = "glucose"
        samples[0].save(update_fields=["treatment"])
        for index, sample in enumerate(samples):
            sample.time_point = 500 * (index + 1)
            sample.save(update_fields=["time_point"])
        self.sample_dict = {s.id: s for s in samples}
        html = self._render()
        self.assertEqual(["treatments", "populations", "samples", "time", "types",
                          "references", "columns", "frequency", "export"],
                         re.findall(r'data-toggle="tab" data-tab="(\w+)"', html))
        # Treatments leads now, and carries the other half of the coloring choice.
        self.assertIn('class="tab-pane active" id="mutation_matrix-pane-treatments"', html)
        self.assertIn('data-color="treatment"', html)
        self.assertIn('treatment-palette-0', html)
        self.assertIn('<li data-value="glucose" class="active">', html)
        self.assertIn('(no treatment)', html)
        self.assertRegex(html, r'data-times="500(\.0)?,1000(\.0)?"')

    def test_vertical_padding_offers_normal_and_condensed(self):
        html = self._render()
        self.assertIn('data-role="view-control">Cell padding:', html)
        self.assertIn('data-view="normal">Normal</button>', html)
        self.assertIn('class="btn btn-default active" data-view="condensed">Condensed</button>', html)

    def test_a_sample_header_is_a_vertical_link_to_its_mutations_page(self):
        """The scroll box the table sits in is the script's (DataTables wraps the table in
        it), so the markup carries none; what it carries is one vertical header per sample,
        linking to that sample's page rather than sorting anything."""
        html = self._render()
        self.assertNotIn("mutation-matrix-scroll", html)
        self.assertEqual(len(self.sample_dict), html.count('class="mutation-matrix-vertical"'))
        for sample in self.sample_dict.values():
            self.assertIn('href="/mutations/breseq?experiment_id=%d&amp;sample_id=%d"'
                          % (self.experiment.id, sample.id), html)

    def test_rows_travel_as_json_and_the_assets_are_linked(self):
        html = self._render()
        self.assertIn('id="mutation-matrix-rows"', html)
        self.assertIn('data-experiment-id="%d"' % self.experiment.id, html)
        for asset in ("css/breseq_table.css", "js/breseq_table.js", "js/mutation_matrix.js"):
            self.assertIn(asset, html)

    def test_preferences_are_embedded_only_for_a_signed_in_reader(self):
        from mutint_common.preferences import set_preference
        set_preference(self.user, "mutation_matrix.columns", {"hidden": ["gene"]})
        anonymous = self._render()
        self.assertIn('data-authenticated="0"', anonymous)
        # The strip names the json_script it would read; only a signed-in reader gets it.
        self.assertNotIn('<script id="mutation-matrix-prefs"', anonymous)
        signed_in = self._render(user=self.user)
        self.assertIn('data-authenticated="1"', signed_in)
        self.assertIn('id="mutation-matrix-prefs"', signed_in)
        self.assertIn("mutation_matrix.columns", signed_in)

    def test_nothing_from_the_old_table_survives(self):
        html = self._render()
        for gone in ("toggle-mut-tag", "fa-tags", "tag_select", "hidden_columns",
                     "column_sort_from_right", "Column Sort from Right"):
            self.assertNotIn(gone, html)
