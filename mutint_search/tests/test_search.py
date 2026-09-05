"""The Search page renders its results as the mutation matrix.

The app had no tests, which is how it shipped for a while with a script that referenced a
variable only the other table pages defined -- a blank results table and a console error nobody
saw. One end-to-end render is what catches that class of thing.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture

GD = """#=GENOME_DIFF\t1.0
#=REFSEQ\ttest_ref
SNP\t1\t.\ttest_ref\t100\tA\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=1
"""


class SearchPageTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True,
            is_superuser=True)
        self.client.force_login(self.user)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        breseq_fixture.write_sample(self.drop, "1-1-1-1", gd_text=GD)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")

    def _search(self, **params):
        query = {"gene": "", "min_freq": "", "max_freq": "", "min_pos": "", "max_pos": "",
                 "mut_type": "", "project": "", "strain": "", "ref_seq": ""}
        query.update(params)
        return self.client.get("/search/", query)

    def test_the_form_renders_without_a_query(self):
        self.assertEqual(200, self.client.get("/search/").status_code)

    def test_a_hit_renders_the_matrix_with_qualified_sample_labels(self):
        response = self._search(gene="thrA")
        html = response.content.decode()
        self.assertEqual(200, response.status_code)
        self.assertIn("data-mutation-matrix", html)
        self.assertIn('class="breseq-sample"', html)
        # Spans experiments, so the sample says which experiment it is from ...
        self.assertIn("e 1 / 1 / 1-1", html)
        # ... and there is no experiment for the matrix to remember a sample selection for.
        self.assertIn('data-experiment-id=""', html)
        self.assertIn("<b>1</b> Unique Mutations", html)

    def test_no_hit_says_so_rather_than_drawing_a_table(self):
        html = self._search(gene="nosuchgene").content.decode()
        self.assertIn("0</b> Unique Mutations", html)
        self.assertIn("data-mutation-matrix", html)
