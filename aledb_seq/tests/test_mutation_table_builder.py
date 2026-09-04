"""The mutation table after the Amplifications page was removed.

`/mutations/amplifications` was a copy of `mutation_table` with one argument changed, and it
was the only page that showed AMP mutations -- `/mutations` passed `filter_type="AMP"`, which
means *exclude* AMP, not include it. Deleting the page without dropping that argument would
have hidden every amplification in the UI, so these tests guard the pairing.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_seq.util import get_all_observed_mutations_filtered
from aledb_seq.models import Mutation

# The shipped fixture carries only SNP and DEL. AMP fields are
# seq_id / position / size / new_copy_number (see gdparse.py's field schema).
GD_WITH_AMP = """#=GENOME_DIFF\t1.0
#=REFSEQ\ttest_ref
SNP\t1\t.\ttest_ref\t100\tA\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=1
AMP\t2\t.\ttest_ref\t120\t10\t3\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=1
"""


class AmplificationsRemovedTestCase(TestCase):
    def setUp(self):
        # try_creating_project -> find_user prompts on stdin for an unknown name.
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "1-1-1-1", gd_text=GD_WITH_AMP)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="tester")
        from aledb_experiment.models import Experiment
        self.experiment = Experiment.objects.get()

    # --- the page is gone --------------------------------------------------------------

    def test_the_amplifications_route_no_longer_resolves(self):
        response = self.client.get(
            "/mutations/amplifications", {"experiment_id": self.experiment.id})
        self.assertEqual(response.status_code, 404)

    def test_no_amplifications_entry_in_the_sidebar(self):
        from aledb_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

        labels = [item["label"] for item in get_nav_items(EXPERIMENT_SECTION)]
        # Only core's own entries are asserted. Whether a plugin registered something is
        # not core's business, and the registry is shared -- an assertion about what is
        # *absent* from it would just be a statement about the install set.
        self.assertNotIn("Amplifications", labels)
        self.assertIn("Mutations", labels)

    # --- and its mutations did not go with it ------------------------------------------

    def test_the_fixture_really_contains_an_amp_mutation(self):
        """Guards the guard: if the AMP record stopped importing, the test below would
        pass for the wrong reason."""
        self.assertTrue(Mutation.objects.filter(mutation_type="AMP").exists())

    def test_amp_mutations_are_in_the_mutation_table(self):
        """The regression this whole change hangs on. /mutations used to exclude these."""
        observed = get_all_observed_mutations_filtered(self.experiment.id)
        types = {obs.mutation.mutation_type for obs in observed}

        self.assertIn("AMP", types)
        self.assertIn("SNP", types)   # and it did not become AMP-only by accident

    # --- the columns line up -------------------------------------------------------------

    def test_the_header_and_every_row_have_the_same_number_of_columns(self):
        """DataTables is fed the header and the rows separately, and does not check.

        A row shorter than the header renders silently misaligned -- every column after the
        mismatch shows its neighbour's value, which reads like a CSS problem rather than an
        off-by-one. This is the assertion the close-icon column's removal needed: it shifted
        every column left by one, in two files that had to move together.
        """
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import (
            get_mutation_table_body, get_table_header,
        )

        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = get_all_observed_mutations_filtered(self.experiment.id)

        header = get_table_header(self.user, reseq_dict, self.experiment)
        body = get_mutation_table_body(self.user, observed, reseq_dict, self.experiment)

        self.assertTrue(body, "the fixture produces rows")
        for index, row in enumerate(body):
            self.assertEqual(len(header), len(row), "row %d is a different width" % index)

    def test_the_refseq_column_constant_points_at_the_reference_column(self):
        """table_template.js derives every other column it touches from this one -- the sort
        column, and the default hidden columns -- so if it drifts, the table sorts and hides
        the wrong things while still looking plausible."""
        from aledb_common.constants import (
            HTML_MUTATION_TABLE_HEADER, REFSEQ_COLUMN_IN_MUT_TABLE,
        )

        self.assertEqual("Reference Seq",
                         HTML_MUTATION_TABLE_HEADER[REFSEQ_COLUMN_IN_MUT_TABLE])

    def test_the_row_holds_the_reference_at_that_index(self):
        """The other half of the same invariant: the header says where it is, and the body
        has to actually put it there.

        Compared on the cell's *text*, because the cell is an anchor into the NCBI Sequence
        Viewer -- the contig name is what it reads, not what the whole cell equals. The
        invariant being pinned is which column carries the reference, and that is unchanged
        by the name being linked.
        """
        from django.utils.html import strip_tags

        from aledb_common.constants import REFSEQ_COLUMN_IN_MUT_TABLE
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import get_mutation_table_body

        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = get_all_observed_mutations_filtered(self.experiment.id)
        row = get_mutation_table_body(self.user, observed, reseq_dict, self.experiment)[0]

        references = {mutation.reseq_reference for mutation in Mutation.objects.all()}
        self.assertIn(strip_tags(row[REFSEQ_COLUMN_IN_MUT_TABLE]).strip(), references)

    def test_the_close_icon_column_is_gone(self):
        """It removed the row from the client-side table until the next reload -- the same
        delete-that-is-not-a-delete `aledb_mutation_editor` replaces."""
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import get_mutation_table_body

        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = get_all_observed_mutations_filtered(self.experiment.id)
        row = get_mutation_table_body(self.user, observed, reseq_dict, self.experiment)[0]

        self.assertNotIn("deleteRow", "".join(str(cell) for cell in row))
        self.assertNotIn("close-icon", "".join(str(cell) for cell in row))

    # --- the browser link on a frequency cell -------------------------------------------

    def test_a_cell_links_to_the_browser_when_the_sample_has_an_alignment(self):
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import _get_table_mutation_entry

        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = sorted(get_all_observed_mutations_filtered(self.experiment.id),
                          key=lambda o: o.id)[0]
        reseq_dict[observed.sample_id].bam_stored = True

        cell = _get_table_mutation_entry(observed, reseq_dict)

        self.assertIn("/mutations/browse?observed_mut_id=%d" % observed.id, cell)
        # class="true" is load-bearing: _contains_mutation substring-tests it to decide
        # whether the row renders, and table_template.js tests it to colour the cell.
        self.assertIn('class="true"', cell)

    def test_a_cell_is_plain_text_when_the_sample_has_no_alignment(self):
        """Linking would only lead to a page explaining the alignment's absence."""
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import _get_table_mutation_entry

        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = sorted(get_all_observed_mutations_filtered(self.experiment.id),
                          key=lambda o: o.id)[0]
        reseq_dict[observed.sample_id].bam_stored = False

        cell = _get_table_mutation_entry(observed, reseq_dict)

        self.assertNotIn("browse", cell)
        self.assertIn('class="true"', cell)

    def test_an_empty_cell_never_contains_the_true_marker(self):
        """_contains_mutation substring-tests for `true`; an empty cell carrying it would
        make a row of nothing render as a row of mutations."""
        from aledb_seq.views.mutation_table_builder import HTML_EMPTY_MUTATION_CELL

        self.assertNotIn("true", HTML_EMPTY_MUTATION_CELL)

    def test_excluding_by_type_still_works_for_the_plugin_tables(self):
        """filter_type is now unused by core, but fixation and converge still pass it
        through get_table_body. Its values read backwards: 'AMP' means exclude AMP."""
        observed = get_all_observed_mutations_filtered(
            self.experiment.id, filter_type="AMP")
        types = {obs.mutation.mutation_type for obs in observed}

        self.assertNotIn("AMP", types)
        self.assertIn("SNP", types)


class ManuallyAddedMutationTestCase(TestCase):
    """A mutation somebody typed in has to appear in the cross-sample table.

    `aledb_mutation_editor` writes an observation with `present=True` and no caller flags --
    breseq did not call it, because a person asserted it. Every read path that decides "is
    this mutation in this sample" by asking which *caller* found it therefore answers no, and
    the row vanishes from a table that is supposed to be the experiment's contents.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.user.set_password("pw")
        self.user.save()

        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "1-1-1-1", gd_text=GD_WITH_AMP)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="tester")
        from aledb_experiment.models import Experiment
        self.experiment = Experiment.objects.get()

    def _add_by_hand(self):
        """The rows `mutation_add_apply` produces, built by the code that produces them.

        `build_observation` rather than a literal dict on purpose: this test is about what
        the add form actually writes, and a hand-copied set of kwargs would go on passing
        after that function changed.
        """
        from decimal import Decimal

        from aledb_mutation_editor.record_builder import build_observation
        from aledb_seq.models import ObservedMutation
        from aledb_seq.util import get_reseq_ordered_dict

        mutation = Mutation.objects.create(
            experiment=self.experiment,
            position=4242,
            reseq_reference="test_ref",
            mutation_type="SNP",
            sequence_change="A->G",
            gene="thrA")
        sample = list(get_reseq_ordered_dict(self.experiment.id, None, None, None)
                      .values())[0]
        ObservedMutation.objects.create(
            sample=sample,
            mutation=mutation,
            **build_observation(Decimal("1.0")))
        return mutation

    def test_a_hand_added_mutation_has_a_row_in_the_cross_sample_table(self):
        """The defect. Compare, fixation, converge and aledb_search all render from here."""
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import get_mutation_table_body

        mutation = self._add_by_hand()
        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = get_all_observed_mutations_filtered(self.experiment.id)

        body = get_mutation_table_body(self.user, observed, reseq_dict, self.experiment)
        positions = {str(row[3]) for row in body}
        self.assertIn("4,242", positions,
                      "a mutation added through the editor is missing from the table")

    def test_its_cell_carries_the_frequency_that_was_entered(self):
        """Not merely present: the cell has to say what the row is worth in that sample,
        the same as a called one. An empty cell in a rendered row reads as 'not in this
        sample', which is the opposite of what was recorded."""
        from aledb_seq.util import get_reseq_ordered_dict
        from aledb_seq.views.mutation_table_builder import get_mutation_table_body

        self._add_by_hand()
        reseq_dict = get_reseq_ordered_dict(self.experiment.id, None, None, None)
        observed = get_all_observed_mutations_filtered(self.experiment.id)

        body = get_mutation_table_body(self.user, observed, reseq_dict, self.experiment)
        row = [entry for entry in body if str(entry[3]) == "4,242"][0]
        self.assertIn("1.00", "".join(str(cell) for cell in row[-len(reseq_dict):]))


class GeneCellTestCase(TestCase):
    """The Gene cell's wrapper div, on both sides of the ten-gene branch.

    The expandable branch used to leave the wrapper unclosed. That was invisible -- each cell
    is set as its own `<td>`'s innerHTML, so the parser closed it at the end of the fragment
    and the DOM came out identical -- but the string was malformed, and a malformed string
    only stays harmless while nothing changes how it is delivered.

    A unit test, with no database: `get_gene_table_entry` reads three attributes off whatever
    it is handed, and standing an experiment up to assert on tag balance would say nothing the
    stub does not.
    """

    class _Mutation:
        def __init__(self, gene, mutation_id=7):
            self.gene = gene
            self.id = mutation_id

        def is_ecocyc_gene(self):
            return False

    def _cell(self, count):
        from aledb_seq.views.mutation_table_builder import get_gene_table_entry

        # `Mutation.gene` is comma-SPACE separated -- that is what
        # `aledb_common.util.GENE_RANGE_ANNOTATION_DELIMITER` splits on, and a bare comma
        # yields one long "gene" that never reaches the expandable branch.
        return get_gene_table_entry(
            self._Mutation(", ".join("gene%04d" % index for index in range(count))))

    def test_both_branches_balance_their_tags(self):
        for count in (1, 10, 11, 40):
            with self.subTest(genes=count):
                cell = self._cell(count)
                self.assertEqual(cell.count("<div"), cell.count("</div>"),
                                 "unbalanced <div> in a %d-gene cell: %s" % (count, cell))

    def test_only_a_long_list_is_collapsed(self):
        """Ten is not more than ten -- the boundary is where an off-by-one would hide."""
        self.assertNotIn("fa-plus", self._cell(10))
        self.assertIn("fa-plus", self._cell(11))

    def test_a_list_over_the_limit_is_counted_rather_than_rendered(self):
        """Reached only by rows written before the importer capped -- `aledb_seq.0012` moves
        the ones this database had. No expander, because there is nothing to open."""
        from aledb_common.util import GENE_LIST_LIMIT

        cell = self._cell(GENE_LIST_LIMIT + 1)
        self.assertIn("%d genes" % (GENE_LIST_LIMIT + 1), cell)
        self.assertNotIn("fa-plus", cell)
        self.assertNotIn("gene0000", cell)
        self.assertEqual(cell.count("<div"), cell.count("</div>"))

    def test_a_list_at_the_limit_is_still_rendered_in_full(self):
        from aledb_common.util import GENE_LIST_LIMIT

        cell = self._cell(GENE_LIST_LIMIT)
        self.assertIn("fa-plus", cell)
        self.assertIn("gene0999", cell)

    def test_the_collapsed_list_is_inside_the_wrapper_and_carries_every_gene(self):
        cell = self._cell(25)
        self.assertTrue(cell.startswith("<div style="), cell[:40])
        self.assertTrue(cell.endswith("</div>"), cell[-40:])
        self.assertIn('id="7"', cell)          # what the icon's data-target names
        self.assertIn("gene0024", cell)
