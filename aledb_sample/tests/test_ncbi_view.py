"""The NCBI Sequence Viewer page, and the links that reach it.

The page's whole risk is that a near-miss accession renders a convincing picture of the wrong
gene, so most of what is asserted here is what does **not** render: no viewer, and no
third-party script, until a contig's sequence has been confirmed.
"""

import re
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_sample.models import (ReferenceSequence, Mutation, NcbiSequence, MutationCall,
                              Sample)

SVIEWER_SCRIPT = "sviewer/js/sviewer.js"


class _Fixture(TestCase):
    """A real imported breseq sample, so the reference and its per-contig digests are real."""

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
        self.mutation = self.call.mutation
        self.experiment = self.reseq.experiment
        self.reference = ReferenceSequence.objects.get(experiment=self.experiment)
        self.entry = self.reference.seq_ids[0]

    def _verify_contig(self, accession="NC_000913.3"):
        """Mark this experiment's contig verified, as a successful check would."""
        return NcbiSequence.objects.create(
            sha256=self.entry["sha256"], length=self.entry["length"],
            accession=accession, status=NcbiSequence.VERIFIED,
            detail="matches", checked_at=timezone.now())

    def _get(self, mutation_id=None, **extra):
        params = {"mutation_id": self.mutation.id if mutation_id is None else mutation_id}
        params.update(extra)
        return self.client.get("/mutations/ncbi", params)


class UnverifiedTestCase(_Fixture):
    def test_an_unchecked_contig_asks_rather_than_drawing(self):
        html = self._get().content.decode("utf-8")
        self.assertNotIn(SVIEWER_SCRIPT, html)
        self.assertIn("Nobody has said which NCBI record", html)
        self.assertIn("Check against NCBI", html)

    def test_nothing_is_guessed_from_the_contig_name(self):
        """The name may look like an accession; that is not evidence and must not prefill."""
        self.assertEqual(NcbiSequence.objects.count(), 0)
        html = self._get().content.decode("utf-8")
        # The contig is named in the page, but the box that would carry an accession is
        # empty -- matched on the input itself rather than on a bare value="" anywhere.
        match = re.search(r'id="ncbi-accession"[^>]*', html)
        self.assertIsNotNone(match)
        self.assertIn('value=""', match.group(0))
        self.assertNotIn(self.entry["id"], match.group(0))

    def test_a_mismatch_says_so_and_draws_nothing(self):
        NcbiSequence.objects.create(
            sha256=self.entry["sha256"], length=self.entry["length"],
            accession="NC_000913.3", status=NcbiSequence.MISMATCH,
            detail="NC_000913.3 is 4,641,652 bases; this contig is 200.",
            checked_at=timezone.now())
        html = self._get().content.decode("utf-8")
        self.assertNotIn(SVIEWER_SCRIPT, html)
        self.assertIn("this contig is 200", html)

    def test_an_error_draws_nothing(self):
        NcbiSequence.objects.create(
            sha256=self.entry["sha256"], length=self.entry["length"],
            accession="NC_000913.3", status=NcbiSequence.ERROR,
            detail="Could not reach NCBI: timed out", checked_at=timezone.now())
        html = self._get().content.decode("utf-8")
        self.assertNotIn(SVIEWER_SCRIPT, html)
        self.assertIn("Could not reach NCBI", html)

    def test_an_experiment_with_no_reference_explains_itself(self):
        self.reference.delete()
        html = self._get().content.decode("utf-8")
        self.assertNotIn(SVIEWER_SCRIPT, html)
        self.assertIn("no stored reference sequence", html)

    def test_a_reference_with_no_per_sequence_digest_cannot_be_checked(self):
        """Established before per-sequence hashing: there is nothing to compare against, and
        offering a check that could never conclude would be theatre."""
        self.reference.seq_ids = [{"id": self.entry["id"], "length": self.entry["length"]}]
        self.reference.save()
        html = self._get().content.decode("utf-8")
        self.assertIn("no stored reference sequence", html)
        self.assertNotIn("Check against NCBI", html)


class VerifiedTestCase(_Fixture):
    def test_a_verified_contig_draws_the_viewer(self):
        self._verify_contig()
        html = self._get().content.decode("utf-8")
        self.assertIn(SVIEWER_SCRIPT, html)
        self.assertIn('class="SeqViewerApp"', html)
        self.assertIn("NC_000913.3", html)

    def test_the_viewer_div_carries_data_autoload(self):
        """The attribute that makes sviewer.js instantiate the div.

        Without it the class alone does nothing: the script loads, nothing raises, and the
        page renders an empty box. That is how this shipped -- every state was asserted and
        the one attribute that makes the viewer appear was not, because its absence looks
        identical to a working page in every server-side check. NCBI's own demo page carries
        it on every div it wants drawn.
        """
        self._verify_contig()
        html = self._get().content.decode("utf-8")
        self.assertIn("data-autoload", html)
        # On the app div itself, not merely somewhere on the page.
        match = re.search(r"<div[^>]*SeqViewerApp[^>]*>", html)
        self.assertIsNotNone(match)
        self.assertIn("data-autoload", match.group(0))

    def test_the_marker_spans_the_mutation_and_the_view_is_wider(self):
        """The marker is the mutation's own extent; the view adds context either side."""
        self._verify_contig()
        response = self._get()
        params = response.context["sviewer"]
        start, end = params["start"], params["end"]
        low, high = [int(part) for part in params["view"].split(":")]
        self.assertLessEqual(low, start)
        self.assertGreaterEqual(high, end)
        self.assertIn("%d:%d|" % (start, end), params["marker"])

    def test_the_view_is_clamped_to_the_contig(self):
        """The fixture's contig is short, so an unclamped buffer would run off its end."""
        self._verify_contig()
        params = self._get().context["sviewer"]
        _low, high = [int(part) for part in params["view"].split(":")]
        self.assertLessEqual(high, self.entry["length"])

    def test_the_marker_label_carries_no_delimiters(self):
        """`|` separates a marker's fields and `,` separates markers, so either inside a
        label would silently produce a different marker or none."""
        self._verify_contig()
        marker = self._get().context["sviewer"]["marker"]
        label = marker.split("|")[1]
        for character in ("|", ",", "!", ":", " ", "&", "<", ">", '"', "%", "+", "="):
            self.assertNotIn(character, label)


class LeakTestCase(_Fixture):
    def test_the_sequence_digest_never_reaches_the_page(self):
        """`browse._reference_urls` strips it with a comment calling it identity material.
        This page resolves the verdict through it and must not render it either."""
        self._verify_contig()
        html = self._get().content.decode("utf-8")
        self.assertNotIn(self.entry["sha256"], html)

    def test_it_is_absent_from_the_unverified_page_too(self):
        self.assertNotIn(self.entry["sha256"], self._get().content.decode("utf-8"))


class AccessTestCase(_Fixture):
    def test_a_bad_mutation_id_is_a_404(self):
        self.assertEqual(self._get(mutation_id=999999).status_code, 404)

    def test_a_missing_mutation_id_is_a_404(self):
        self.assertEqual(self.client.get("/mutations/ncbi").status_code, 404)

    def test_a_stranger_gets_403(self):
        stranger = User.objects.create(username="nobody", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(self._get().status_code, 403)

    def test_a_reader_cannot_state_an_accession(self):
        """Stating one writes a row every experiment on this genome then reads."""
        stranger = User.objects.create(username="reader", is_active=True)
        self.client.force_login(stranger)
        response = self.client.post("/mutations/ncbi/check", {
            "experiment_id": self.experiment.id,
            "seq_id": self.entry["id"], "accession": "NC_000913.3"})
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(NcbiSequence.objects.count(), 0)

    def test_a_locked_experiment_refuses_the_check(self):
        """The case `can_edit_project` would have missed: a predicate handed the project
        cannot see a flag that lives on the experiment."""
        self.experiment.locked_at = timezone.now()
        self.experiment.locked_by = self.user
        self.experiment.save()
        response = self.client.post("/mutations/ncbi/check", {
            "experiment_id": self.experiment.id,
            "seq_id": self.entry["id"], "accession": "NC_000913.3"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(NcbiSequence.objects.count(), 0)

    def test_an_empty_accession_is_refused_without_asking_ncbi(self):
        response = self.client.post("/mutations/ncbi/check", {
            "experiment_id": self.experiment.id,
            "seq_id": self.entry["id"], "accession": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(NcbiSequence.objects.count(), 0)


class RenameTestCase(_Fixture):
    def test_a_verified_verdict_survives_a_contig_rename(self):
        """The reason the verdict is keyed on the digest rather than on the name.

        A rename does not change the bases, so it cannot invalidate an answer that was about
        the bases -- and there is no rename hook here as a result.
        """
        self._verify_contig()
        entry = dict(self.reference.seq_ids[0])
        old_name = entry["id"]
        entry["id"] = "renamed_contig"
        entry["aliases"] = [old_name]
        self.reference.seq_ids = [entry]
        self.reference.save()
        Mutation.objects.filter(pk=self.mutation.pk).update(reseq_reference="renamed_contig")

        html = self._get().content.decode("utf-8")
        self.assertIn(SVIEWER_SCRIPT, html)
        self.assertIn("renamed_contig", html)


class TableLinkTestCase(_Fixture):
    """The Reference Seq column of the shared cross-sample table."""

    def _cells(self):
        from aledb_sample.views.mutation_table_builder import get_mutation_table_body
        from aledb_sample.util import get_reseq_ordered_dict
        reseq_dict = get_reseq_ordered_dict(self.experiment.id)
        calls = list(MutationCall.objects.filter(
            sample__in=reseq_dict.keys()).select_related("mutation"))
        return get_mutation_table_body(self.user, calls, reseq_dict, self.experiment)

    def test_an_unverified_contig_is_still_linked(self):
        """The bootstrapping fix. Gating this link on verification made the only page
        carrying the accession box reachable solely once it had already been used."""
        from aledb_common.constants import REFSEQ_COLUMN_IN_MUT_TABLE
        rows = self._cells()
        self.assertTrue(rows)
        for row in rows:
            cell = row[REFSEQ_COLUMN_IN_MUT_TABLE]
            self.assertIn("/mutations/ncbi?mutation_id=", cell)
            self.assertIn("not been matched", cell)

    def test_a_verified_contig_says_so_in_the_title(self):
        from aledb_common.constants import REFSEQ_COLUMN_IN_MUT_TABLE
        self._verify_contig()
        for row in self._cells():
            self.assertIn("Show this position", row[REFSEQ_COLUMN_IN_MUT_TABLE])

    def test_a_verified_contig_becomes_a_link(self):
        from aledb_common.constants import REFSEQ_COLUMN_IN_MUT_TABLE
        self._verify_contig()
        rows = self._cells()
        self.assertTrue(rows)
        for row in rows:
            cell = row[REFSEQ_COLUMN_IN_MUT_TABLE]
            self.assertIn("/mutations/ncbi?mutation_id=", cell)

    def test_the_cell_never_contains_the_literal_true(self):
        """`_contains_mutation` substring-tests the row for `true` to decide whether it
        renders at all, and `table_template.js` tests for it to colour a sample cell. A
        Reference cell carrying it would corrupt both, silently."""
        from aledb_common.constants import REFSEQ_COLUMN_IN_MUT_TABLE
        self._verify_contig()
        for row in self._cells():
            self.assertNotIn("true", row[REFSEQ_COLUMN_IN_MUT_TABLE])

    def test_linking_does_not_change_the_table_width(self):
        from aledb_common.constants import HTML_MUTATION_TABLE_HEADER
        self._verify_contig()
        rows = self._cells()
        expected = len(HTML_MUTATION_TABLE_HEADER) + len(
            {o.sample_id for o in MutationCall.objects.all()}) - 1
        for row in rows:
            self.assertEqual(len(row), len(rows[0]))
            self.assertGreaterEqual(len(row), len(HTML_MUTATION_TABLE_HEADER) - 1)


class BreseqTableLinkTestCase(_Fixture):
    """The per-sample page, which reaches the viewer through its own Reference column."""

    def _html(self):
        return self.client.get("/mutations/breseq", {
            "experiment_id": self.experiment.id,
            "sample_id": self.reseq.id}).content.decode("utf-8")

    def test_the_contig_is_linked_before_it_is_verified(self):
        self.assertIn("/mutations/ncbi?mutation_id=", self._html())

    def test_it_is_still_linked_once_verified(self):
        self._verify_contig()
        self.assertIn("/mutations/ncbi?mutation_id=", self._html())


class BrowseLinkTestCase(_Fixture):
    """The genome browser's own row links across to the annotation at the same locus."""

    def _html(self):
        return self.client.get(
            "/mutations/browse",
            {"mutation_call_id": self.call.id}).content.decode("utf-8")

    def test_the_contig_is_linked_before_it_is_verified(self):
        self.assertIn("/mutations/ncbi?mutation_id=", self._html())

    def test_it_is_still_linked_once_verified(self):
        self._verify_contig()
        self.assertIn("/mutations/ncbi?mutation_id=", self._html())


class ReferencePageTestCase(_Fixture):
    """The page that says what genome an experiment is called against.

    Nothing showed this before: the Add Data page knew only whether a reference existed, as
    a yes/no, and the contigs and their lengths were visible nowhere at all.
    """

    def _get_ref(self, **extra):
        params = {"experiment_id": self.experiment.id}
        params.update(extra)
        return self.client.get("/mutations/reference", params)

    def test_it_lists_the_contigs_and_their_lengths(self):
        response = self._get_ref()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn(self.entry["id"], html)
        self.assertIn(format(self.entry["length"], ",d"), html)

    def test_it_says_when_nothing_is_matched_yet(self):
        html = self._get_ref().content.decode("utf-8")
        self.assertIn("None has been matched to an NCBI record", html)

    def test_it_offers_the_accession_box(self):
        """The whole point of the page existing: this is where an accession is stated."""
        html = self._get_ref().content.decode("utf-8")
        self.assertIn('name="accession"', html)
        self.assertIn('name="seq_id"', html)

    def test_a_verified_contig_shows_its_accession_and_links_to_ncbi(self):
        self._verify_contig()
        html = self._get_ref().content.decode("utf-8")
        self.assertIn("NC_000913.3", html)
        self.assertIn("ncbi.nlm.nih.gov/nuccore/NC_000913.3", html)
        self.assertIn("1 confirmed against NCBI", html)
        self.assertNotIn('name="accession"', html)

    def test_previous_names_are_shown(self):
        """A renamed contig keeps its old names, and stored BAMs still carry them."""
        entry = dict(self.reference.seq_ids[0])
        entry["aliases"] = ["old_name"]
        self.reference.seq_ids = [entry]
        self.reference.save()
        self.assertIn("old_name", self._get_ref().content.decode("utf-8"))

    def test_an_experiment_with_no_reference_says_where_to_get_one(self):
        self.reference.delete()
        html = self._get_ref().content.decode("utf-8")
        self.assertIn("no reference genome", html)
        self.assertIn("/import/add/", html)

    def test_a_reference_predating_hashing_cannot_be_checked(self):
        self.reference.seq_ids = [{"id": self.entry["id"], "length": self.entry["length"]}]
        self.reference.save()
        html = self._get_ref().content.decode("utf-8")
        self.assertIn("cannot be checked", html)
        self.assertNotIn('name="accession"', html)

    def test_a_reader_is_not_offered_the_box(self):
        stranger = User.objects.create(username="reader2", is_active=True)
        self.experiment.project.is_public = True
        self.experiment.project.save()
        self.client.force_login(stranger)
        html = self._get_ref().content.decode("utf-8")
        self.assertIn(self.entry["id"], html)
        self.assertNotIn('name="accession"', html)

    def test_no_experiment_selected_is_not_a_traceback(self):
        response = self.client.get("/mutations/reference")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Select an experiment", response.content.decode("utf-8"))

    def test_the_sequence_digest_never_reaches_the_page(self):
        self._verify_contig()
        self.assertNotIn(self.entry["sha256"], self._get_ref().content.decode("utf-8"))


class BootstrapJourneyTestCase(_Fixture):
    """The path a person actually walks the first time, which used to be a dead end.

    Every link into the viewer was gated on the contig already being verified, so the only
    page carrying the accession box could not be reached until the box had been used. This
    walks the whole journey rather than asserting the states individually, which is what
    missed it.
    """

    def test_a_fresh_experiment_can_be_walked_from_a_table_to_a_verified_contig(self):
        from unittest import mock
        from aledb_import.reference import render_fasta
        from aledb_sample.tests.test_ncbi import _Response

        # 1. The Reference nav entry reaches a page that offers the box.
        page = self.client.get("/mutations/reference",
                               {"experiment_id": self.experiment.id})
        self.assertIn('name="accession"', page.content.decode("utf-8"))

        # 2. So does the mutation table's Reference column, with nothing yet verified.
        table = self.client.get("/mutations/breseq", {
            "experiment_id": self.experiment.id,
            "sample_id": self.reseq.id}).content.decode("utf-8")
        self.assertIn("/mutations/ncbi?mutation_id=", table)

        # 3. Stating an accession that really is this sequence verifies it.
        bases = "N" * self.entry["length"]
        with mock.patch("aledb_sample.ncbi.requests.get", side_effect=[
                _Response(payload={"result": {"uids": ["1"], "1": {
                    "accessionversion": "NC_000913.3",
                    "slen": self.entry["length"]}}}),
                _Response(text=render_fasta([("x", bases)]))]):
            with mock.patch("aledb_sample.ncbi.sequence_digest_stream",
                            return_value=(self.entry["sha256"], self.entry["length"])):
                response = self.client.post("/mutations/ncbi/check", {
                    "experiment_id": self.experiment.id,
                    "seq_id": self.entry["id"],
                    "accession": "NC_000913"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["verified"])

        # 4. And the viewer now draws.
        html = self._get().content.decode("utf-8")
        self.assertIn(SVIEWER_SCRIPT, html)
