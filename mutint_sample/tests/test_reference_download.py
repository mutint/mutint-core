"""`/mutations/reference/<id>/download`: the Reference page's download, end to end.

The rendering itself is `mutint_import/tests/test_reference_export.py`'s. What is asserted
here is the contract around it: who may fetch, what the parameters mean, and that the whole
reference in FASTA or GFF3 is byte-for-byte the file the store holds.
"""

import os

from django.contrib.auth.models import User

from mutint_common import store
from mutint_import import annotation
from mutint_import.annotate import genbank
from mutint_import.tests import breseq_fixture
from mutint_sample.tests.test_ncbi_view import _Fixture
from mutint_experiment.models import Experiment, Project


class _Download(_Fixture):
    SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A),
                 ("plasmid_1.2", breseq_fixture.SEQUENCE_B)]

    def setUp(self):
        super().setUp()
        # Memoised per process on (experiment id, mtime); a previous test's experiment can
        # share both with this one.
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)

    def _download(self, experiment_id=None, **params):
        experiment_id = self.experiment.id if experiment_id is None else experiment_id
        return self.client.get("/mutations/reference/%s/download" % experiment_id, params)

    def _stored(self, filename):
        with open(store.experiment_reference_path(self.experiment.id, filename),
                  "r", encoding="utf-8") as handle:
            return handle.read()


class WholeReferenceTestCase(_Download):
    def test_fasta_is_the_stored_file_as_an_attachment(self):
        response = self._download(format="fasta")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode("utf-8"), self._stored(store.REFERENCE_FASTA))
        self.assertEqual(response["Content-Disposition"],
                         'attachment; filename="e_reference.fasta"')
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")

    def test_gff3_is_the_stored_file(self):
        response = self._download(format="gff3")
        self.assertEqual(response.content.decode("utf-8"), self._stored(store.REFERENCE_GFF3))
        self.assertIn('filename="e_reference.gff3"', response["Content-Disposition"])

    def test_genbank_holds_every_contig_and_reads_back(self):
        response = self._download(format="genbank")
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("LOCUS"))
        self.assertIn('filename="e_reference.gbk"', response["Content-Disposition"])
        path = os.path.join(self.drop, "download.gbk")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.assertEqual(sorted(genbank.load_genbank(path).seq_ids()),
                         ["plasmid_1.2", "test_ref"])

    def test_no_format_means_fasta(self):
        response = self._download()
        self.assertIn('filename="e_reference.fasta"', response["Content-Disposition"])

    def test_naming_every_contig_is_still_the_whole_reference(self):
        response = self._download(seq_id=["plasmid_1.2", "test_ref"])
        self.assertIn('filename="e_reference.fasta"', response["Content-Disposition"])


class SubsetTestCase(_Download):
    def test_one_contig_is_that_contig_named_for_it(self):
        response = self._download(format="fasta", seq_id="plasmid_1.2")
        text = response.content.decode("utf-8")
        self.assertIn(">plasmid_1.2\n", text)
        self.assertNotIn(">test_ref", text)
        self.assertEqual(response["Content-Disposition"],
                         'attachment; filename="e_plasmid_1.2.fasta"')

    def test_gff3_of_one_contig_carries_only_its_rows(self):
        text = self._download(format="gff3", seq_id="plasmid_1.2").content.decode("utf-8")
        self.assertIn("##sequence-region\tplasmid_1.2", text)
        self.assertNotIn("test_ref", text)

    def test_an_unknown_contig_is_a_400_naming_it(self):
        response = self._download(seq_id=["plasmid_1.2", "nope"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("nope", response.content.decode("utf-8"))

    def test_an_unknown_format_is_a_400(self):
        response = self._download(format="xlsx")
        self.assertEqual(response.status_code, 400)
        self.assertIn("genbank", response.content.decode("utf-8"))


class AccessTestCase(_Download):
    def test_a_stranger_gets_403(self):
        stranger = User.objects.create(username="stranger", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)
        self.assertEqual(self._download().status_code, 403)

    def test_anonymous_may_download_a_public_project(self):
        """ALEdb is public: whoever can read the Reference page can take its sequence."""
        Project.objects.filter(pk=self.experiment.project_id).update(is_public=True)
        self.client.logout()
        response = self._download(format="fasta")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_anonymous_is_refused_a_private_project(self):
        self.client.logout()
        self.assertEqual(self._download().status_code, 403)

    def test_an_unknown_experiment_is_a_404(self):
        self.assertEqual(self._download(experiment_id=999999).status_code, 404)

    def test_an_experiment_with_no_reference_is_a_404(self):
        bare = Experiment.objects.create(name="bare", project=self.experiment.project)
        self.assertEqual(self._download(experiment_id=bare.id).status_code, 404)

    def test_post_is_not_a_download(self):
        response = self.client.post("/mutations/reference/%s/download" % self.experiment.id)
        self.assertEqual(response.status_code, 405)
