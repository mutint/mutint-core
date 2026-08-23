"""The genome-browser page reached from a mutation-table frequency cell.

The alignment routes it points at were built and tested long before anything linked to them
(`test_alignments.py`); this covers the page that finally does.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_seq.models import ExperimentReference, ObservedMutation, ResequencingExperiment


class BrowseMutationTestCase(TestCase):
    def setUp(self):
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

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="tester")

        self.reseq = ResequencingExperiment.objects.get()
        self.observed = ObservedMutation.objects.filter(
            sequencing_experiment=self.reseq).first()
        self.experiment = self.reseq.ale_experiment

    def _get(self, observed_mut_id=None):
        return self.client.get("/mutations/browse", {
            "observed_mut_id": self.observed.id if observed_mut_id is None else observed_mut_id})

    # --- the normal case ----------------------------------------------------------------

    def test_renders_with_the_locus_and_the_file_urls(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        mutation = self.observed.mutation
        self.assertIn("%s:" % mutation.reseq_reference, html)          # locus contig
        self.assertIn("/mutations/alignments/%d/bam" % self.reseq.id, html)
        self.assertIn("/mutations/alignments/%d/bai" % self.reseq.id, html)
        self.assertIn("/mutations/reference/%d/fasta" % self.experiment.ale_id, html)
        self.assertIn("/mutations/reference/%d/fai" % self.experiment.ale_id, html)
        self.assertIn("js/igv.min.js", html)

    def test_the_locus_buffers_the_whole_mutation_not_just_its_start(self):
        """A deletion has to open showing the deletion, not 200 bp of its left junction."""
        from aledb_seq.views.browse import LOCUS_BUFFER_BASES, _locus

        mutation = self.observed.mutation
        mutation.position = 5000          # clear of the contig start, so nothing is clamped
        mutation.start_position, mutation.end_position = 5000, 25000
        mutation.save(update_fields=["position", "start_position", "end_position"])

        contig, _, span = _locus(mutation).partition(":")
        start, _, end = span.partition("-")

        self.assertEqual(contig, mutation.reseq_reference)
        self.assertEqual(int(start), 5000 - LOCUS_BUFFER_BASES)
        self.assertEqual(int(end), 25000 + LOCUS_BUFFER_BASES)

    def test_an_unannotated_mutation_is_measured_from_its_gd_data(self):
        """Imported before a reference arrived, it has no extent columns -- but the same
        breseq rule applies to the raw record, or a DEL would collapse to a point."""
        from aledb_seq.views.browse import _extent

        mutation = self.observed.mutation
        mutation.start_position = mutation.end_position = None
        mutation.gd_data = {"type": "DEL", "seq_id": "ref", "position": 5000, "size": 20001}
        mutation.save(update_fields=["start_position", "end_position", "gd_data"])

        self.assertEqual(_extent(mutation), (5000, 25000))

    def test_a_mutation_near_the_contig_start_does_not_go_below_one(self):
        mutation = self.observed.mutation
        mutation.start_position = mutation.end_position = mutation.position = 5
        mutation.save(update_fields=["position", "start_position", "end_position"])

        from aledb_seq.views.browse import _locus
        self.assertIn(":1-", _locus(mutation))

    # --- the states that must explain rather than break ---------------------------------

    def test_a_sample_without_an_alignment_explains_itself(self):
        """A bare .gd import has no reads. Say so rather than render an empty browser."""
        self.reseq.bam_stored = False
        self.reseq.save(update_fields=["bam_stored"])

        response = self._get()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("No alignment is stored", html)
        self.assertNotIn("igv-browser", html)
        self.assertNotIn("js/igv.min.js", html)

    def test_an_experiment_without_a_reference_explains_itself(self):
        ExperimentReference.objects.all().delete()

        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIn("no reference genome", response.content.decode("utf-8"))

    # --- addressing and access ----------------------------------------------------------

    def test_unknown_or_missing_observed_mutation_is_a_404(self):
        self.assertEqual(self._get(observed_mut_id=999999).status_code, 404)
        self.assertEqual(self._get(observed_mut_id="nonsense").status_code, 404)
        self.assertEqual(self.client.get("/mutations/browse").status_code, 404)

    def test_a_stranger_is_forbidden(self):
        stranger = User.objects.create(username="s", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        self.assertEqual(self._get().status_code, 403)

    # --- the other-samples control ------------------------------------------------------

    def test_other_samples_with_alignments_are_offered_as_tracks(self):
        # A separate drop: re-importing the first sample's directory would rebuild its
        # observed mutations and invalidate the row this test addresses.
        second = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, second, True)
        breseq_fixture.write_sample(second, "s2")
        breseq_folder.import_breseq_folders(
            second, project_name="P", experiment_name="e", person="tester")
        other = ResequencingExperiment.objects.exclude(id=self.reseq.id).first()
        self.assertIsNotNone(other)

        html = self._get().content.decode("utf-8")
        self.assertIn("/mutations/alignments/%d/bam" % other.id, html)
        # The sample being viewed is the track, not an option to add again.
        self.assertIn('id="add-track"', html)
