import os
from datetime import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_import import gd_import
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment

from genomediff import GenomeDiff

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "gdparse", "test_gdparse")
CLEAN_GD = os.path.join(FIXTURE_DIR, "3-30000-1-1.gd")  # headers + SNP/INS/DEL/MOB/CON


def _canonical(mutations):
    """Order-independent, comparable view of a list of genomediff Records."""
    return sorted(
        (m.type, tuple(sorted((k, str(v)) for k, v in m.attributes.items())))
        for m in mutations)


def _uploaded(path):
    with open(path, "rb") as handle:
        return SimpleUploadedFile(os.path.basename(path), handle.read())


class GdImportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User",
            email="t@e.com", is_active=True, is_staff=True, date_joined=datetime.now())

    def _import(self, path, experiment="gd exp"):
        return gd_import.import_gd_files(
            [_uploaded(path)], project_name="gd project",
            experiment_name=experiment, person="tester")

    def _expected_mutations(self, path):
        with open(path, encoding="utf-8") as handle:
            return GenomeDiff.read(handle).mutations

    def test_import_creates_chain_and_mutations(self):
        expected = self._expected_mutations(CLEAN_GD)
        summary = self._import(CLEAN_GD)

        self.assertEqual(summary["total_mutations"], len(expected))
        self.assertEqual(Mutation.objects.count(), len(expected))
        self.assertEqual(ObservedMutation.objects.count(), len(expected))

        # Experiment chain synthesized from the filename 3-30000-1-1.
        self.assertEqual(AleExperiment.objects.count(), 1)
        ale_id = AleId.objects.get()
        self.assertEqual(ale_id.ale_id, 3)
        self.assertEqual(Flask.objects.get().flask_number, 30000)
        self.assertEqual(Isolate.objects.get().isolate_number, 1)
        self.assertEqual(TechnicalReplicate.objects.get().tech_rep_number, 1)

        # gd_data captured on every row; REFSEQ propagated to the isolate.
        self.assertFalse(Mutation.objects.filter(gd_data__isnull=True).exists())
        self.assertTrue(Isolate.objects.get().reseq_reference)

    def test_round_trip_is_apply_compatible(self):
        """Import -> export .gd -> re-parse yields the original mutations."""
        original = self._expected_mutations(CLEAN_GD)
        self._import(CLEAN_GD)

        seq_experiment = ResequencingExperiment.objects.get()
        gd_text = gd_import.export_gd_text(seq_experiment)
        reparsed = GenomeDiff.read(iter(gd_text.splitlines())).mutations

        self.assertEqual(_canonical(original), _canonical(reparsed))

    def test_mutations_are_deduplicated_across_experiments(self):
        self._import(CLEAN_GD, experiment="exp one")
        mutation_count = Mutation.objects.count()

        self._import(CLEAN_GD, experiment="exp two")

        # Same mutations, shared; a second observation set under a second experiment.
        self.assertEqual(Mutation.objects.count(), mutation_count)
        self.assertEqual(AleExperiment.objects.count(), 2)
        self.assertEqual(ResequencingExperiment.objects.count(), 2)
        self.assertEqual(ObservedMutation.objects.count(), mutation_count * 2)

    def test_reimport_same_sample_is_idempotent(self):
        self._import(CLEAN_GD)
        self._import(CLEAN_GD)  # same experiment + filename

        self.assertEqual(ResequencingExperiment.objects.count(), 1)
        self.assertEqual(ObservedMutation.objects.count(), Mutation.objects.count())

    def test_web_upload_endpoint(self):
        self.client.force_login(self.user)
        response = self.client.post("/import/", {
            "project": "web project",
            "experiment": "web exp",
            "person": "tester",
            "gd_files": _uploaded(CLEAN_GD),
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["total_mutations"], 0)
        self.assertEqual(Mutation.objects.count(), payload["total_mutations"])

        # Export endpoint returns a downloadable .gd.
        reseq = ResequencingExperiment.objects.get()
        export = self.client.get("/import/gd/%d/export" % reseq.id)
        self.assertEqual(export.status_code, 200)
        self.assertIn("#=GENOME_DIFF", export.content.decode("utf-8"))
