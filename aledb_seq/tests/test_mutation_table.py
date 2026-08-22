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
        from aledb_experiment.models import AleExperiment
        self.experiment = AleExperiment.objects.get()

    # --- the page is gone --------------------------------------------------------------

    def test_the_amplifications_route_no_longer_resolves(self):
        response = self.client.get(
            "/mutations/amplifications", {"ale_experiment_id": self.experiment.ale_id})
        self.assertEqual(response.status_code, 404)

    def test_no_amplifications_entry_in_the_sidebar(self):
        from aledb_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

        labels = [item["label"] for item in get_nav_items(EXPERIMENT_SECTION)]
        self.assertNotIn("Amplifications", labels)
        self.assertIn("Mutations", labels)   # the one it was wedged beside

    # --- and its mutations did not go with it ------------------------------------------

    def test_the_fixture_really_contains_an_amp_mutation(self):
        """Guards the guard: if the AMP record stopped importing, the test below would
        pass for the wrong reason."""
        self.assertTrue(Mutation.objects.filter(mutation_type="AMP").exists())

    def test_amp_mutations_are_in_the_mutation_table(self):
        """The regression this whole change hangs on. /mutations used to exclude these."""
        observed = get_all_observed_mutations_filtered(self.experiment.ale_id)
        types = {obs.mutation.mutation_type for obs in observed}

        self.assertIn("AMP", types)
        self.assertIn("SNP", types)   # and it did not become AMP-only by accident

    def test_excluding_by_type_still_works_for_the_plugin_tables(self):
        """filter_type is now unused by core, but fixation and converge still pass it
        through get_table_body. Its values read backwards: 'AMP' means exclude AMP."""
        observed = get_all_observed_mutations_filtered(
            self.experiment.ale_id, filter_type="AMP")
        types = {obs.mutation.mutation_type for obs in observed}

        self.assertNotIn("AMP", types)
        self.assertIn("SNP", types)
