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

from mutint_experiment.models import Experiment
from mutint_import import breseq_folder, reference_export
from mutint_import.tests import breseq_fixture
from mutint_sample.models import (ReferenceSequences, Mutation, DatabaseSequenceLink, MutationCall,
                              Sample)

SVIEWER_SCRIPT = "sviewer/js/sviewer.js"


class _Fixture(TestCase):
    """A real imported breseq sample, so the reference and its per-contig digests are real."""

    #: The reference the sample is written against; a subclass overrides it to get
    #: several contigs. None means `breseq_fixture`'s one-contig default.
    SEQUENCES = None

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
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "s1", sequences=self.SEQUENCES)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")

        self.sample = Sample.objects.get()
        self.call = MutationCall.objects.filter(
            sample=self.sample).first()
        self.mutation = self.call.mutation
        self.experiment = self.sample.experiment
        self.reference = ReferenceSequences.objects.get(experiment=self.experiment)
        self.entry = self.reference.seq_ids[0]

    def _verify_contig(self, accession="NC_000913.3"):
        """Mark this experiment's contig verified, as a successful check would."""
        return DatabaseSequenceLink.objects.create(
            sha256=self.entry["sha256"], length=self.entry["length"],
            accession=accession, status=DatabaseSequenceLink.VERIFIED,
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
        self.assertEqual(DatabaseSequenceLink.objects.count(), 0)
        html = self._get().content.decode("utf-8")
        # The contig is named in the page, but the box that would carry an accession is
        # empty -- matched on the input itself rather than on a bare value="" anywhere.
        match = re.search(r'id="ncbi-accession"[^>]*', html)
        self.assertIsNotNone(match)
        self.assertIn('value=""', match.group(0))
        self.assertNotIn(self.entry["id"], match.group(0))

    def test_a_mismatch_says_so_and_draws_nothing(self):
        DatabaseSequenceLink.objects.create(
            sha256=self.entry["sha256"], length=self.entry["length"],
            accession="NC_000913.3", status=DatabaseSequenceLink.MISMATCH,
            detail="NC_000913.3 is 4,641,652 bases; this contig is 200.",
            checked_at=timezone.now())
        html = self._get().content.decode("utf-8")
        self.assertNotIn(SVIEWER_SCRIPT, html)
        self.assertIn("this contig is 200", html)

    def test_an_error_draws_nothing(self):
        DatabaseSequenceLink.objects.create(
            sha256=self.entry["sha256"], length=self.entry["length"],
            accession="NC_000913.3", status=DatabaseSequenceLink.ERROR,
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
        self.assertEqual(DatabaseSequenceLink.objects.count(), 0)

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
        self.assertEqual(DatabaseSequenceLink.objects.count(), 0)

    def test_an_empty_accession_is_refused_without_asking_ncbi(self):
        response = self.client.post("/mutations/ncbi/check", {
            "experiment_id": self.experiment.id,
            "seq_id": self.entry["id"], "accession": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DatabaseSequenceLink.objects.count(), 0)


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
        Mutation.objects.filter(pk=self.mutation.pk).update(seq_id="renamed_contig")

        html = self._get().content.decode("utf-8")
        self.assertIn(SVIEWER_SCRIPT, html)
        self.assertIn("renamed_contig", html)


class TableLinkTestCase(_Fixture):
    """The Reference column of the cross-sample table -- the mutation matrix."""

    def _rows(self):
        from mutint_sample.mutation_matrix import build_matrix
        from mutint_sample.util import get_ordered_sample_dict
        sample_dict = get_ordered_sample_dict(self.experiment.id)
        calls = list(MutationCall.objects.filter(
            sample__in=sample_dict.keys()).select_related("mutation"))
        return build_matrix(calls, sample_dict, experiment=self.experiment).rows

    def test_an_unverified_contig_is_still_linked(self):
        """The bootstrapping fix. Gating this link on verification made the only page
        carrying the accession box reachable solely once it had already been used."""
        rows = self._rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("/mutations/ncbi?mutation_id=", row["seq_id_url"])
            self.assertIn("not been matched", row["seq_id_title"])

    def test_a_verified_contig_says_so_in_the_title(self):
        self._verify_contig()
        for row in self._rows():
            self.assertIn("Show this position", row["seq_id_title"])

    def test_a_verified_contig_stays_linked(self):
        self._verify_contig()
        rows = self._rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("/mutations/ncbi?mutation_id=", row["seq_id_url"])

    def test_linking_does_not_change_the_row_shape(self):
        self._verify_contig()
        rows = self._rows()
        samples = {o.sample_id for o in MutationCall.objects.all()}
        for row in rows:
            self.assertEqual(len(row["samples"]), len(rows[0]["samples"]))
            self.assertGreaterEqual(len(row["samples"]), 1)
            self.assertLessEqual(len(row["samples"]), len(samples))


class BreseqTableLinkTestCase(_Fixture):
    """The per-sample page, which reaches the viewer through its own Reference column."""

    def _html(self):
        return self.client.get("/mutations/breseq", {
            "experiment_id": self.experiment.id,
            "sample_id": self.sample.id}).content.decode("utf-8")

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

    def test_it_offers_a_download_of_the_selected_sequences(self):
        """A form beside the table, not around it: each row carries the NCBI check's own
        form, and a form inside a form is dropped by the parser."""
        html = self._get_ref().content.decode("utf-8")
        self.assertIn('<form id="reference-download"', html)
        self.assertIn('method="get"', html)
        self.assertIn('action="/mutations/reference/%d/download"' % self.experiment.id, html)
        self.assertLess(html.index("</form>"), html.index("<table"))
        self.assertIn('name="seq_id" value="%s"' % self.entry["id"], html)
        self.assertIn('form="reference-download" checked', html)
        self.assertEqual(html.count('form="reference-download" checked'),
                         len(self.reference.seq_ids))

    def test_the_format_menu_is_the_endpoint_s_table(self):
        html = self._get_ref().content.decode("utf-8")
        self.assertIn('<select id="reference-format" name="format"', html)
        offered = re.findall(r'<option value="([a-z0-9]+)"', html)
        self.assertEqual(offered, list(reference_export.FORMATS))
        self.assertIn('value="%s" selected' % reference_export.DEFAULT_FORMAT, html)

    def test_no_reference_means_no_download(self):
        bare = Experiment.objects.create(name="bare", project=self.experiment.project)
        html = self._get_ref(experiment_id=bare.id).content.decode("utf-8")
        self.assertNotIn("reference-download", html)

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
        self.assertIn("/import/", html)

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
        from mutint_import.reference import render_fasta
        from mutint_sample.tests.test_ncbi import _Response

        # 1. The Reference nav entry reaches a page that offers the box.
        page = self.client.get("/mutations/reference",
                               {"experiment_id": self.experiment.id})
        self.assertIn('name="accession"', page.content.decode("utf-8"))

        # 2. So does the mutation table's Reference column, with nothing yet verified.
        table = self.client.get("/mutations/breseq", {
            "experiment_id": self.experiment.id,
            "sample_id": self.sample.id}).content.decode("utf-8")
        self.assertIn("/mutations/ncbi?mutation_id=", table)

        # 3. Stating an accession that really is this sequence verifies it.
        bases = "N" * self.entry["length"]
        with mock.patch("mutint_sample.ncbi.requests.get", side_effect=[
                _Response(payload={"result": {"uids": ["1"], "1": {
                    "accessionversion": "NC_000913.3",
                    "slen": self.entry["length"]}}}),
                _Response(text=render_fasta([("x", bases)]))]):
            with mock.patch("mutint_sample.ncbi.sequence_digest_stream",
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


class StartAnExperimentFromThisReferenceTestCase(_Fixture):
    """The shortcut button: the other thing you can do with a reference besides download it."""

    def _get_ref(self, **extra):
        params = {"experiment_id": self.experiment.id}
        params.update(extra)
        return self.client.get("/mutations/reference", params)

    def test_the_button_links_to_the_create_page_with_this_reference_chosen(self):
        html = self._get_ref().content.decode("utf-8")
        self.assertIn('href="/experiment/new/?reference=%d"' % self.experiment.id, html)
        self.assertIn("Create new", html)

    def test_an_anonymous_reader_is_offered_nothing(self):
        """A public project is readable signed out, and `/experiment/new/` 403s -- so the
        button would be a dead end dressed up as an action."""
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.logout()

        html = self._get_ref().content.decode("utf-8")
        self.assertEqual(self.client.get("/experiment/new/").status_code, 403)
        self.assertNotIn("/experiment/new/?reference=", html)

    def test_a_reader_with_nowhere_to_create_is_offered_nothing(self):
        """Signed in is not enough: the question is whether any project can be written to."""
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.force_login(reader)

        html = self._get_ref().content.decode("utf-8")
        self.assertNotIn("/experiment/new/?reference=", html)
