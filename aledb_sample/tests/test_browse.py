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
from aledb_sample.models import ReferenceSequences, Mutation, MutationCall, Sample
from aledb_sample.views.browse import _sample_tracks


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

        self.reseq = Sample.objects.get()
        self.call = MutationCall.objects.filter(
            sample=self.reseq).first()
        self.experiment = self.reseq.experiment

    def _get(self, mutation_call_id=None):
        return self.client.get("/mutations/browse", {
            "mutation_call_id": self.call.id if mutation_call_id is None else mutation_call_id})

    # --- the normal case ----------------------------------------------------------------

    def test_renders_with_the_locus_and_the_file_urls(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        mutation = self.call.mutation
        self.assertIn("%s:" % mutation.seq_id, html)          # locus contig
        self.assertIn("/mutations/alignments/%d/bam" % self.reseq.id, html)
        self.assertIn("/mutations/alignments/%d/bai" % self.reseq.id, html)
        self.assertIn("/mutations/reference/%d/fasta" % self.experiment.id, html)
        self.assertIn("/mutations/reference/%d/fai" % self.experiment.id, html)
        self.assertIn("js/igv.min.js", html)

    def test_the_gene_list_toggle_script_is_loaded(self):
        """This page renders breseq's table too, and the Show button was inert on it.

        The handler was an inline script in the Samples page's own template, so a wide
        deletion opened here showed `N genes` beside a button that did nothing.
        """
        self.assertIn("js/breseq_table.js", self._get().content.decode("utf-8"))

    def test_the_locus_buffers_the_whole_mutation_not_just_its_start(self):
        """A deletion has to open showing the deletion, not 200 bp of its left junction."""
        from aledb_sample.views.browse import LOCUS_BUFFER_BASES, _locus

        mutation = self.call.mutation
        mutation.start_position = 5000          # clear of the contig start, so nothing is clamped
        mutation.start_position, mutation.end_position = 5000, 25000
        mutation.save(update_fields=["start_position", "end_position"])

        contig, _, span = _locus(mutation).partition(":")
        start, _, end = span.partition("-")

        self.assertEqual(contig, mutation.seq_id)
        self.assertEqual(int(start), 5000 - LOCUS_BUFFER_BASES)
        self.assertEqual(int(end), 25000 + LOCUS_BUFFER_BASES)

    def test_an_unannotated_mutation_is_measured_from_its_gd_data(self):
        """Imported before a reference arrived, it has no *extent* -- but the same breseq
        rule applies to the raw record, or a DEL would collapse to a point.

        Only `end_position` is cleared. `start_position` used to be clearable too, when the
        annotator owned it and `position` held the record's value beside it; the two columns
        are one now, it is written at creation, and it is NOT NULL -- so "unannotated" means
        the extent is unknown, never the start.
        """
        from aledb_sample.views.browse import _extent

        mutation = self.call.mutation
        mutation.start_position = 5000
        mutation.end_position = None
        mutation.set_record(
            Mutation.COMPONENT, Mutation.GENOME_DIFF,
            {"type": "DEL", "seq_id": "ref", "position": 5000, "size": 20001}, save=False)
        mutation.save(update_fields=["start_position", "end_position", "extended_fields"])

        self.assertEqual(_extent(mutation), (5000, 25000))

    def test_a_mutation_near_the_contig_start_does_not_go_below_one(self):
        mutation = self.call.mutation
        mutation.start_position = mutation.end_position = 5
        mutation.save(update_fields=["start_position", "end_position"])

        from aledb_sample.views.browse import _locus
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
        ReferenceSequences.objects.all().delete()

        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIn("no reference genome", response.content.decode("utf-8"))

    # --- the chromosome alias table -----------------------------------------------------

    def test_no_alias_url_until_a_sequence_has_been_renamed(self):
        """An experiment that has never been renamed must gain no extra request, and igv
        treats a falsy aliasURL as 'the names are already right'.

        Asserted on the JSON key, quotes included: the template always carries the bare
        identifier `aliasURL: reference.aliasURL`, and only the config value is conditional.
        """
        response = self._get()

        self.assertNotIn('"aliasURL"', response.content.decode("utf-8"))

    def test_a_renamed_sequence_publishes_its_alias_url(self):
        reference = ReferenceSequences.objects.get()
        reference.seq_ids = [dict(reference.seq_ids[0], aliases=["old_name"])]
        reference.save(update_fields=["seq_ids"])

        html = self._get().content.decode("utf-8")

        self.assertIn('"aliasURL"', html)
        self.assertIn("/mutations/reference/%d/chromalias" % self.experiment.id, html)

    def test_the_page_does_not_publish_per_sequence_hashes(self):
        """seq_ids also carries the sequence hashes that establish reference identity;
        this dict is rendered into the page for anyone who can see it."""
        reference = ReferenceSequences.objects.get()
        reference.seq_ids = [dict(reference.seq_ids[0], sha256="deadbeef" * 8)]
        reference.save(update_fields=["seq_ids"])

        html = self._get().content.decode("utf-8")

        self.assertNotIn("deadbeef", html)

    # --- addressing and access ----------------------------------------------------------

    def test_unknown_or_missing_mutation_call_is_a_404(self):
        self.assertEqual(self._get(mutation_call_id=999999).status_code, 404)
        self.assertEqual(self._get(mutation_call_id="nonsense").status_code, 404)
        self.assertEqual(self.client.get("/mutations/browse").status_code, 404)

    def test_a_stranger_is_forbidden(self):
        stranger = User.objects.create(username="s", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        self.assertEqual(self._get().status_code, 403)

    # --- the sample menu ------------------------------------------------------------------

    def _second_sample(self):
        """A separate drop: re-importing the first sample's directory would rebuild its
        mutation calls and invalidate the row these tests address."""
        second = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, second, True)
        breseq_fixture.write_sample(second, "s2")
        breseq_folder.import_breseq_folders(
            second, project_name="P", experiment_name="e", person="tester")
        other = Sample.objects.exclude(id=self.reseq.id).first()
        self.assertIsNotNone(other)
        return other

    def test_every_sample_with_an_alignment_is_in_the_menu(self):
        other = self._second_sample()

        html = self._get().content.decode("utf-8")
        self.assertIn("/mutations/alignments/%d/bam" % other.id, html)
        # The sample being viewed is in the menu like any other -- it is shown and hidden by
        # the same control, so it is not excluded the way the old add-only list excluded it.
        self.assertIn("/mutations/alignments/%d/bam" % self.reseq.id, html)
        self.assertIn('id="sample-list"', html)

    def test_only_the_sample_arrived_at_is_checked(self):
        self._second_sample()

        samples = _sample_tracks(self.experiment, self.call.mutation,
                                 current_id=self.reseq.id)
        self.assertEqual([s["is_current"] for s in samples].count(True), 1)
        self.assertTrue(next(s for s in samples if s["id"] == self.reseq.id)["is_current"])

    def test_a_sample_is_marked_mutant_only_when_the_mutation_is_called_in_it(self):
        """The `*` follows the mutation table's own rule -- `present`, not the mere
        existence of a MutationCall row. A row recording that the mutation was looked
        for and found absent must not earn a star."""
        other = self._second_sample()
        mutation = self.call.mutation
        MutationCall.objects.filter(sample=other,
                                        mutation=mutation).delete()
        MutationCall.objects.create(sample=other, mutation=mutation,
                                        present=False)

        marked = {s["id"]: s["has_mutation"]
                  for s in _sample_tracks(self.experiment, mutation, current_id=self.reseq.id)}

        self.assertTrue(marked[self.reseq.id])
        self.assertFalse(marked[other.id])

    def test_a_sample_that_carries_the_mutation_by_hand_is_marked_too(self):
        """The `*` says the mutation is in that sample, not that a caller found it.

        `aledb_mutation_editor` writes `present=True` with `source="manual"` and no caller
        flags -- which is the exact shape the old `breseq_present or gatk_present` rule
        answered no to, so a mutation somebody added went unstarred in a menu whose whole job
        is saying which pileups to look at.
        """
        from decimal import Decimal

        from aledb_mutation_editor.record_builder import build_call

        other = self._second_sample()
        mutation = self.call.mutation
        MutationCall.objects.filter(sample=other,
                                        mutation=mutation).delete()
        MutationCall.objects.create(sample=other, mutation=mutation,
                                        **build_call(Decimal("1.0")))

        marked = {s["id"]: s["has_mutation"]
                  for s in _sample_tracks(self.experiment, mutation, current_id=self.reseq.id)}

        self.assertTrue(marked[other.id])


class SwitchingMutationTestCase(TestCase):
    """Clicking a mutation on the Mutations track makes it the page's mutation.

    The track draws every mutation in the *experiment*, so a click can land on one the sample
    on screen does not call -- which has no MutationCall to name it by. That is the whole
    reason `?mutation_id=&sample_id=` exists beside `?mutation_call_id=`.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
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

        self.reseq = Sample.objects.get()
        self.call = MutationCall.objects.filter(
            sample=self.reseq).first()
        self.experiment = self.reseq.experiment

    # --- the second spelling of the page's address --------------------------------------

    def test_the_pair_renders_the_same_page_as_the_call(self):
        by_call = self.client.get(
            "/mutations/browse", {"mutation_call_id": self.call.id})
        by_pair = self.client.get("/mutations/browse", {
            "mutation_id": self.call.mutation_id, "sample_id": self.reseq.id})
        self.assertEqual(200, by_call.status_code)
        self.assertEqual(200, by_pair.status_code)
        # The locus is what positions the browser, and it must not depend on how the page
        # was addressed.
        self.assertEqual(by_call.context["locus"], by_pair.context["locus"])
        self.assertEqual(by_call.context["rows"][0]["mutation_id"],
                         by_pair.context["rows"][0]["mutation_id"])

    def _sibling_sample(self):
        """A second sample in the same experiment, carrying the same mutations.

        Needed to express "this sample does not call it": deleting the only call of a
        mutation takes the mutation out of the experiment altogether, which is a 404 and a
        different case entirely.
        """
        breseq_fixture.write_sample(self.drop, "s2")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="tester")
        return Sample.objects.exclude(pk=self.reseq.pk).get()

    def test_a_mutation_this_sample_does_not_call_still_renders(self):
        """The case the pair spelling exists for. An unsaved MutationCall carries it, so
        `build_rows` needed no change -- the Freq cell simply comes out empty."""
        self._sibling_sample()
        MutationCall.objects.filter(
            mutation=self.call.mutation, sample=self.reseq).delete()
        response = self.client.get("/mutations/browse", {
            "mutation_id": self.call.mutation_id, "sample_id": self.reseq.id})
        self.assertEqual(200, response.status_code)
        row = response.context["rows"][0]
        self.assertEqual(self.call.mutation_id, row["mutation_id"])
        self.assertEqual("", row["freq"])

    def test_a_sample_and_a_mutation_from_different_experiments_are_refused(self):
        """Two ids arrive from the client, and nothing else stops them being paired: a
        mutation from another experiment would render a row about a locus these reads cannot
        contain."""
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        breseq_fixture.write_sample(other, "s2")
        breseq_folder.import_breseq_folders(
            other, project_name="P2", experiment_name="e2", person="tester")
        stranger = MutationCall.objects.exclude(
            sample=self.reseq).first()

        response = self.client.get("/mutations/browse", {
            "mutation_id": stranger.mutation_id, "sample_id": self.reseq.id})
        self.assertEqual(404, response.status_code)

    def test_an_unknown_pair_is_a_404(self):
        for params in ({"mutation_id": 999999, "sample_id": self.reseq.id},
                       {"mutation_id": self.call.mutation_id, "sample_id": 999999},
                       {"mutation_id": "x", "sample_id": "y"},
                       {}):
            with self.subTest(params=params):
                self.assertEqual(
                    404, self.client.get("/mutations/browse", params).status_code)

    # --- the endpoint the click calls ---------------------------------------------------

    def test_it_returns_the_row_and_who_calls_it(self):
        response = self.client.get("/mutations/browse/at", {
            "mutation_id": self.call.mutation_id, "sample_id": self.reseq.id})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(self.call.mutation_id, body["mutation_id"])
        self.assertEqual(self.call.id, body["mutation_call_id"])
        self.assertIn(self.reseq.id, body["calling"])
        self.assertIn("breseq-table", body["table_html"])
        self.assertIn("mutation_call_id=%d" % self.call.id, body["url"])

    def test_a_mutation_the_sample_does_not_call_reports_it_by_omission(self):
        """There is no "called here" field: `calling` is what the menu's `*` flags are drawn
        from, and the sample's absence from it *is* the answer."""
        sibling = self._sibling_sample()
        MutationCall.objects.filter(
            mutation=self.call.mutation, sample=self.reseq).delete()
        body = self.client.get("/mutations/browse/at", {
            "mutation_id": self.call.mutation_id, "sample_id": self.reseq.id}).json()
        self.assertNotIn(self.reseq.id, body["calling"])
        # The sibling still calls it, which is what the menu's `*` will now mark.
        self.assertIn(sibling.id, body["calling"])
        self.assertIsNone(body["mutation_call_id"])
        # No call to name it by, so the URL has to be the pair spelling or a reload
        # would 404 on the page it just came from.
        self.assertIn("mutation_id=%d" % self.call.mutation_id, body["url"])
        self.assertIn("sample_id=%d" % self.reseq.id, body["url"])

    def test_it_404s_an_unknown_mutation(self):
        self.assertEqual(404, self.client.get("/mutations/browse/at", {
            "mutation_id": 999999, "sample_id": self.reseq.id}).status_code)

    def test_a_stranger_is_refused(self):
        """Through the same `_may_view` the page uses -- one answer to "may you see it"."""
        self.experiment.project.is_public = False
        self.experiment.project.save()
        stranger = User.objects.create(
            username="nobody", email="n@e.com", is_active=True)
        self.client.force_login(stranger)
        response = self.client.get("/mutations/browse/at", {
            "mutation_id": self.call.mutation_id, "sample_id": self.reseq.id})
        self.assertEqual(403, response.status_code)

    # --- what the page hands the click handler ------------------------------------------

    def test_the_reads_track_does_not_draw_its_own_coverage_row(self):
        """igv puts a coverage row inside every alignment track, and the BigWig above it is
        the same depth drawn again -- measured in a browser as two histograms in one column,
        one blue and one grey, both scaled 0-169.

        They stopped agreeing when coverage started weighting reads by 1/X1: igv counts every
        alignment once, so its row still towers over a repeat while the track above it does
        not. Asserted here because deleting the flag brings the second row back silently.
        """
        html = self.client.get(
            "/mutations/browse", {"mutation_call_id": self.call.id}).content.decode()
        self.assertIn("showCoverage", html)

    def test_the_sample_menu_toggles_rather_than_replacing(self):
        """Each row is a loaded BAM, so the helper's default -- a plain click selecting only
        that row -- unloaded every other showing sample to show one, with no gesture that put
        them back. Asserted because losing the flag restores that quietly."""
        html = self.client.get(
            "/mutations/browse", {"mutation_call_id": self.call.id}).content.decode()
        self.assertIn("aledbSelectList", html)
        self.assertIn("toggle: true", html)

    def test_the_page_names_the_clickable_track(self):
        """The handler matches on the track id rather than on its label, and the id reaches
        the page from tracks.py rather than being written out twice."""
        from aledb_sample.tracks import MUTATION_TRACK_ID

        html = self.client.get(
            "/mutations/browse", {"mutation_call_id": self.call.id}).content.decode()
        self.assertIn(MUTATION_TRACK_ID, html)
        self.assertIn("trackclick", html)
