"""The sample flags: hypermutator, contaminated, low coverage.

Three booleans that replaced `Sample.tags`. What is pinned: that every flag is edited on the
sample page and drawn as a badge wherever a sample is named, and that nothing draws a badge for
a sample with no flag set. (The data migration that read the old comma-joined text, and the
test that pinned its reading, went with the migration collapse.)
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample import flags
from mutint_sample.models import Sample


class VocabularyTestCase(SimpleTestCase):
    def test_the_three_flags_and_their_columns(self):
        self.assertEqual(["is_hypermutator", "is_contaminated", "is_low_coverage"],
                         list(flags.FLAG_FIELDS))
        for flag in flags.FLAGS:
            self.assertTrue(hasattr(Sample, flag.field), flag.field)
            self.assertTrue(flag.help)

    def test_flags_of_reads_the_columns(self):
        sample = Sample(is_contaminated=True)
        self.assertEqual(["contaminated"], [f.key for f in flags.flags_of(sample)])
        self.assertEqual([], flags.flags_of(Sample()))


class BadgeTestCase(TestCase):
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
        breseq_fixture.write_sample(self.drop, "1-1-1-1")
        breseq_fixture.write_sample(self.drop, "1-2-1-1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")
        from mutint_experiment.models import Experiment
        self.experiment = Experiment.objects.get()
        self.flagged, self.plain = Sample.objects.order_by("time_point")
        self.flagged.is_hypermutator = True
        self.flagged.save()

    def _badges(self, html):
        return html.count('class="sample-flag sample-flag-hypermutator"')

    def test_the_stats_table_shows_the_badge_once(self):
        html = self.client.get("/stats/?experiment_id=%d" % self.experiment.id).content.decode()
        self.assertEqual(1, self._badges(html))
        self.assertIn(">Hypermutator<", html)

    def test_the_mutations_picker_shows_it(self):
        html = self.client.get("/mutations/breseq", {
            "experiment_id": self.experiment.id, "sample_id": self.plain.id}).content.decode()
        self.assertEqual(1, self._badges(html))

    def test_the_matrix_header_and_menu_show_it(self):
        from collections import OrderedDict
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.template import loader
        from django.test import RequestFactory
        from mutint_sample.mutation_matrix import build_matrix
        from mutint_sample.util import get_all_calls_filtered, get_ordered_sample_dict

        matrix = build_matrix(get_all_calls_filtered(self.experiment.id),
                              get_ordered_sample_dict(self.experiment.id), experiment=self.experiment)
        by_id = {s.id: s for s in matrix.samples}
        self.assertEqual(["hypermutator"], [f.key for f in by_id[self.flagged.id].flags])
        self.assertEqual((), by_id[self.plain.id].flags)

        request = RequestFactory().get("/")
        request.user = self.user
        SessionMiddleware(lambda r: None).process_request(request)
        html = loader.get_template("mutation_matrix/page.html").render(
            {"experiment_id": self.experiment.id, "population_names": ["1"], "matrix": matrix,
             "empty_message": "x"}, request)
        # Once in the Samples menu, once in the header.
        self.assertEqual(2, self._badges(html))

    def test_a_sample_with_no_flag_draws_no_badge(self):
        self.flagged.is_hypermutator = False
        self.flagged.save()
        html = self.client.get("/stats/?experiment_id=%d" % self.experiment.id).content.decode()
        self.assertNotIn("sample-flag", html)
