"""The mutation matrix: what a row is, what a cell is, and what the partial renders.

Built from an imported breseq folder, the way the rest of this app's tests are, so the rows
come out of the same importer every page reads.
"""

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
from mutint_sample.util import get_all_calls_filtered, get_reseq_ordered_dict

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
        self.reseq_dict = get_reseq_ordered_dict(self.experiment.id)
        self.calls = get_all_calls_filtered(self.experiment.id)

    def matrix(self, **kwargs):
        return mutation_matrix.build_matrix(self.calls, self.reseq_dict,
                                            experiment=self.experiment, **kwargs)

    def row(self, matrix, position):
        return [r for r in matrix.rows if r["position_sort"] == position][0]


class ColumnsTestCase(_Fixture):
    def test_the_descriptive_columns_are_the_per_sample_tables_minus_freq(self):
        keys = [c.key for c in self.matrix().columns]
        self.assertEqual(["type", "seq_id", "position", "mutation", "annotation", "gene",
                          "description"], keys)
        self.assertNotIn("freq", keys)

    def test_every_column_carries_a_breseq_class(self):
        for column in self.matrix().columns:
            self.assertTrue(column.css_class.startswith("breseq-"), column.key)

    def test_description_is_the_only_column_hidden_by_default(self):
        hidden = [c.key for c in self.matrix().columns if not c.default_visible]
        self.assertEqual(["description"], hidden)

    def test_one_sample_column_per_listed_sample_in_order(self):
        matrix = self.matrix()
        self.assertEqual([s.id for s in self.reseq_dict.values()],
                         [s.id for s in matrix.samples])
        self.assertEqual(list(range(len(self.reseq_dict))), [s.index for s in matrix.samples])
        self.assertEqual(len(self.reseq_dict) + 7, matrix.width)


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
        first = list(self.reseq_dict.values())[0]
        first.bam_stored = False
        row = self.row(self.matrix(), 100)
        self.assertNotIn("u", row["samples"][0])

    def test_a_present_call_with_no_frequency_shows_the_mark(self):
        # A hand-added mutation need not claim a frequency.
        mutation = Mutation.objects.create(
            experiment=self.experiment, start_position=4242, seq_id="test_ref",
            mutation_type="SNP", sequence_change="A->G", gene="thrA")
        MutationCall.objects.create(sample=list(self.reseq_dict.values())[0],
                                    mutation=mutation, present=True, frequency=None)
        self.calls = get_all_calls_filtered(self.experiment.id)
        cell = self.row(self.matrix(), 4242)["samples"][0]
        self.assertEqual(mutation_matrix.PRESENT_MARK, cell["f"])
        self.assertEqual(1.0, cell["s"])

    def test_a_mutation_carried_only_by_an_unlisted_sample_has_no_row(self):
        only_first = OrderedDict(list(self.reseq_dict.items())[:1])
        matrix = mutation_matrix.build_matrix(self.calls, only_first, experiment=self.experiment)
        positions = {r["position_sort"] for r in matrix.rows}
        self.assertIn(100, positions)
        self.assertIn(120, positions)
        # And a call recorded as looked-for-and-absent does not make a row either.
        mutation = Mutation.objects.create(
            experiment=self.experiment, start_position=9000, seq_id="test_ref",
            mutation_type="SNP", sequence_change="A->G", gene="thrA")
        MutationCall.objects.create(sample=list(self.reseq_dict.values())[0],
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

    def test_no_experiment_means_no_experiment_id(self):
        matrix = mutation_matrix.build_matrix(self.calls, self.reseq_dict)
        self.assertIsNone(matrix.experiment_id)

    def test_each_sample_links_to_its_own_mutations_page(self):
        """The experiment given, when there is one; the sample's own when there is not --
        the cross-experiment page has none to give, and the link must still be right."""
        for matrix in (self.matrix(), mutation_matrix.build_matrix(self.calls, self.reseq_dict)):
            for sample in matrix.samples:
                self.assertEqual("/mutations/breseq?experiment_id=%d&sample_id=%d"
                                 % (self.experiment.id, sample.id), sample.url)


class PartialTestCase(_Fixture):
    def _render(self, user=None, **extra):
        request = RequestFactory().get("/")
        request.user = user or AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        context = {"experiment_id": self.experiment.id, "ales": ["1"],
                   "matrix": self.matrix(), "empty_message": "Nothing."}
        context.update(extra)
        return loader.get_template("mutation_matrix/page.html").render(context, request)

    def test_the_header_has_one_th_per_column_each_with_its_class(self):
        html = self._render()
        self.assertEqual(7 + len(self.reseq_dict), html.count("<th "))
        for column in mutation_matrix.DESCRIPTIVE:
            self.assertIn('<th class="%s" data-key="%s"' % (column.css_class, column.key), html)
        self.assertEqual(len(self.reseq_dict), html.count('class="breseq-sample"'))

    def test_the_menus_list_columns_and_samples(self):
        html = self._render()
        self.assertIn('data-role="columns"', html)
        self.assertIn('data-role="samples"', html)
        self.assertIn('<li data-value="description">', html)   # not active
        self.assertIn('<li data-value="gene" class="active">', html)
        for sample in self.reseq_dict.values():
            self.assertIn('<li data-value="%d" class="active">' % sample.id, html)
        self.assertIn('data-samples="all"', html)
        self.assertIn('data-samples="none"', html)
        # And the third menu: the mutation types this table holds, with its two presets.
        self.assertIn('data-role="types"', html)
        self.assertIn('<li data-value="SNP" class="active">', html)
        self.assertIn('<li data-value="AMP" class="active">', html)
        self.assertIn('data-types="all"', html)
        self.assertIn('data-types="none"', html)

    def test_a_sample_header_is_a_vertical_link_to_its_mutations_page(self):
        """The scroll box the table sits in is the script's (DataTables wraps the table in
        it), so the markup carries none; what it carries is one vertical header per sample,
        linking to that sample's page rather than sorting anything."""
        html = self._render()
        self.assertNotIn("mutation-matrix-scroll", html)
        self.assertEqual(len(self.reseq_dict), html.count('class="mutation-matrix-vertical"'))
        for sample in self.reseq_dict.values():
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
        self.assertNotIn("mutation-matrix-prefs", anonymous)
        signed_in = self._render(user=self.user)
        self.assertIn('data-authenticated="1"', signed_in)
        self.assertIn('id="mutation-matrix-prefs"', signed_in)
        self.assertIn("mutation_matrix.columns", signed_in)

    def test_nothing_from_the_old_table_survives(self):
        html = self._render()
        for gone in ("toggle-mut-tag", "fa-tags", "tag_select", "hidden_columns",
                     "column_sort_from_right", "Column Sort from Right"):
            self.assertNotIn(gone, html)
