"""Amplifications in the cross-sample table, and mutations added by hand.

`/mutations/amplifications` was a copy of `mutation_table` with one argument changed, and it
was the only page that showed AMP mutations -- `/mutations` passed `filter_type="AMP"`, which
means *exclude* AMP, not include it. Deleting the page without dropping that argument would
have hidden every amplification in the UI, so these tests guard the pairing.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample.util import get_all_calls_filtered
from mutint_sample.models import Mutation

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
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "1-1-1-1", gd_text=GD_WITH_AMP)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")
        from mutint_experiment.models import Experiment
        self.experiment = Experiment.objects.get()

    # --- the page is gone --------------------------------------------------------------

    def test_the_amplifications_route_no_longer_resolves(self):
        response = self.client.get(
            "/mutations/amplifications", {"experiment_id": self.experiment.id})
        self.assertEqual(response.status_code, 404)

    def test_no_amplifications_entry_in_the_sidebar(self):
        from mutint_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

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
        calls = get_all_calls_filtered(self.experiment.id)
        types = {call.mutation.mutation_type for call in calls}

        self.assertIn("AMP", types)
        self.assertIn("SNP", types)   # and it did not become AMP-only by accident

    # --- excluding by type, which the plugin tables still do -----------------------------

    def test_excluding_by_type_still_works_for_the_plugin_tables(self):
        """`filter_type='AMP'` means *exclude* AMP -- the values read backwards. Fixed and
        Converged Mutations keep that exclusion; Compare deliberately does not."""
        from mutint_filter.util import filter_mutation_calls
        from mutint_sample.util import get_evolved_call_queryset

        calls = filter_mutation_calls(get_evolved_call_queryset(self.experiment.id),
                                      filter_type="AMP")
        types = {call.mutation.mutation_type for call in calls}
        self.assertNotIn("AMP", types)
        self.assertIn("SNP", types)


class ManuallyAddedMutationTestCase(TestCase):
    """A mutation somebody typed in has to appear in the cross-sample table.

    `mutint_curate` writes a call with `present=True` and no caller flags --
    breseq did not call it, because a person asserted it. Every read path that decides "is
    this mutation in this sample" by asking which *caller* found it therefore answers no, and
    the row vanishes from a table that is supposed to be the experiment's contents.
    """

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

        breseq_fixture.write_sample(self.drop, "1-1-1-1", gd_text=GD_WITH_AMP)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")
        from mutint_experiment.models import Experiment
        self.experiment = Experiment.objects.get()

    def _add_by_hand(self):
        """The rows `mutation_add_apply` produces, built by the code that produces them --
        `build_call` rather than a literal dict, so this goes on testing what the add form
        actually writes after that function changes."""
        from mutint_curate.record_builder import build_call
        from mutint_sample.models import MutationCall
        from mutint_sample.util import get_ordered_sample_dict

        mutation = Mutation.objects.create(
            experiment=self.experiment,
            start_position=4242,
            seq_id="test_ref",
            mutation_type="SNP",
            sequence_change="A->G",
            gene="thrA")
        sample = list(get_ordered_sample_dict(self.experiment.id).values())[0]
        MutationCall.objects.create(sample=sample, mutation=mutation, **build_call(1.0))
        return mutation

    def _matrix(self):
        from mutint_sample.mutation_matrix import build_matrix
        from mutint_sample.util import get_ordered_sample_dict

        sample_dict = get_ordered_sample_dict(self.experiment.id)
        return build_matrix(get_all_calls_filtered(self.experiment.id), sample_dict,
                            experiment=self.experiment)

    def test_a_hand_added_mutation_has_a_row_in_the_cross_sample_table(self):
        """The defect. Compare, fixation, converge and mutint_search all render from here."""
        self._add_by_hand()
        positions = {row["position_sort"] for row in self._matrix().rows}
        self.assertIn(4242, positions,
                      "a mutation added through the editor is missing from the table")

    def test_its_cell_carries_the_frequency_that_was_entered(self):
        """Not merely present: the cell has to say what the row is worth in that sample, the
        same as a called one. A blank cell reads as 'not in this sample'."""
        self._add_by_hand()
        row = [r for r in self._matrix().rows if r["position_sort"] == 4242][0]
        self.assertEqual("100%", row["samples"][0]["f"])
