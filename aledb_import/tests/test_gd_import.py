import os
import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_import import gd_import, reference_store
from aledb_import.tests import breseq_fixture
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


def _uploaded_as(path, name):
    """Same fixture content under a different filename, to exercise name parsing."""
    with open(path, "rb") as handle:
        return SimpleUploadedFile(name, handle.read())


class GdImportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User",
            email="t@e.com", is_active=True, is_staff=True, date_joined=datetime.now())

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _ensure_reference(self, experiment="gd exp", project="gd project"):
        """A bare .gd carries no reference, so the experiment must already have one.

        This is what dropping a GenBank, GFF3 or FASTA on the Add page does for a real user.
        """
        context = gd_import._prepare_experiment(project, experiment, "tester", False)
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        gff3_text = breseq_fixture.gff3_text(sequences)
        reference_store.establish_or_check(context["experiment"], gff3_text, sequences)
        return context["experiment"]

    def _import(self, path, experiment="gd exp"):
        self._ensure_reference(experiment)
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

    def test_web_upload_through_the_chunked_session(self):
        """The route a .gd actually takes now that /import/ is gone: create, chunk, finalize.

        Session ownership is by primary key, so the experiment is built the way the Add page
        reaches it rather than by name.
        """
        import json

        experiment = self._ensure_reference("web exp", project="web project")
        experiment.project.user = self.user
        experiment.project.save(update_fields=["user"])
        self.client.force_login(self.user)

        with open(CLEAN_GD, "rb") as handle:
            payload = handle.read()

        created = self.client.post(
            "/import/uploads/",
            data=json.dumps({"ale_experiment_id": experiment.ale_id,
                             "import_type": "genomediff",
                             "files": [{"path": "3-30000-1-1.gd", "size": len(payload)}]}),
            content_type="application/json")
        self.assertEqual(created.status_code, 200, created.content)
        upload_id = created.json()["upload_id"]

        chunked = self.client.post(
            "/import/uploads/%s/chunk" % upload_id,
            {"path": "3-30000-1-1.gd", "offset": "0",
             "chunk": SimpleUploadedFile("chunk", payload)})
        self.assertEqual(chunked.status_code, 200, chunked.content)

        finalized = self.client.post("/import/uploads/%s/finalize" % upload_id)
        self.assertEqual(finalized.status_code, 200, finalized.content)
        summary = finalized.json()
        self.assertGreater(summary["total_mutations"], 0)
        self.assertEqual(Mutation.objects.count(), summary["total_mutations"])

    def test_gd_export_endpoint(self):
        """Outlived /import/: the export download is still routed."""
        experiment = self._ensure_reference("export exp", project="export project")
        gd_import.import_gd_files(
            [_uploaded(CLEAN_GD)], project_name="export project",
            experiment_name="export exp", person="tester")

        reseq = ResequencingExperiment.objects.get()
        export = self.client.get("/import/gd/%d/export" % reseq.id)
        self.assertEqual(export.status_code, 200)
        self.assertIn("#=GENOME_DIFF", export.content.decode("utf-8"))

    # --- sample identity from the filename -------------------------------------------

    # Real-world names like "Ara-1_500gen_762B" carry no A-F-I-R identity. They used to run
    # through parse_ale_name, whose bare `except: return 1` mapped every one of them onto
    # ALE 1 / flask 1 / isolate 1 / rep 1 -- so a 22-file upload collapsed into one sample.
    NON_AFIR_NAMES = [
        "Ara-1_500gen_762B.gd",
        "Ara-1_1000gen_964C.gd",
        "Ara-1_50000gen_11331.gd",
    ]

    def _import_named(self, names, experiment="gd exp"):
        self._ensure_reference(experiment)
        return gd_import.import_gd_files(
            [_uploaded_as(CLEAN_GD, name) for name in names],
            project_name="gd project", experiment_name=experiment, person="tester")

    def test_non_afir_filenames_get_distinct_isolates(self):
        summary = self._import_named(self.NON_AFIR_NAMES)

        self.assertEqual(len(summary["files"]), len(self.NON_AFIR_NAMES))
        self.assertIsNone(summary["files"][0]["error"])

        # One sample per file, each individually addressable.
        self.assertEqual(ResequencingExperiment.objects.count(), len(self.NON_AFIR_NAMES))
        self.assertEqual(
            sorted(ResequencingExperiment.objects.values_list("sample_name", flat=True)),
            sorted(name[:-3] for name in self.NON_AFIR_NAMES))

        isolates = Isolate.objects.all()
        self.assertEqual(isolates.count(), len(self.NON_AFIR_NAMES))
        self.assertEqual(
            sorted(isolates.values_list("isolate_number", flat=True)),
            list(range(1, len(self.NON_AFIR_NAMES) + 1)))

        # ...all still hanging off a single ALE 1 / flask 1.
        self.assertEqual(AleId.objects.count(), 1)
        self.assertEqual(AleId.objects.get().ale_id, 1)
        self.assertEqual(Flask.objects.count(), 1)
        self.assertEqual(Flask.objects.get().flask_number, 1)

    def test_non_afir_reimport_is_idempotent(self):
        self._import_named(self.NON_AFIR_NAMES)
        self._import_named(self.NON_AFIR_NAMES)

        # Auto-numbering must reuse the existing chain, not allocate a second isolate.
        self.assertEqual(ResequencingExperiment.objects.count(), len(self.NON_AFIR_NAMES))
        self.assertEqual(Isolate.objects.count(), len(self.NON_AFIR_NAMES))
        self.assertEqual(ObservedMutation.objects.count(),
                         Mutation.objects.count() * len(self.NON_AFIR_NAMES))

    def test_afir_filename_still_uses_filename_numbering(self):
        """The strict parser must not regress names that genuinely are A-F-I-R."""
        self._import_named(["3-30000-1-1.gd"])

        self.assertEqual(AleId.objects.get().ale_id, 3)
        self.assertEqual(Flask.objects.get().flask_number, 30000)
        self.assertEqual(Isolate.objects.get().isolate_number, 1)
        self.assertEqual(TechnicalReplicate.objects.get().tech_rep_number, 1)

    def test_summary_reports_the_real_experiment_pk(self):
        """The post-import "View mutations" link is built from this id."""
        summary = self._import_named(self.NON_AFIR_NAMES[:1])

        experiment = AleExperiment.objects.get()
        self.assertEqual(summary["experiment_id"], experiment.ale_id)
        self.assertEqual(summary["experiment"], experiment.name)

    # --- the pages the post-import link lands on ---------------------------------------

    def test_imported_experiment_pages_render(self):
        """/stats and /mutations must render for a gd-imported experiment.

        Regression guard for the `ale.common` NameError that turned /mutations into a 500
        page for every experiment. This also used to guard a NULL `location` rendering as
        the literal string "None" in an href; there are no report links left to get that
        wrong."""
        summary = self._import_named(self.NON_AFIR_NAMES)
        experiment_id = summary["experiment_id"]
        self.client.force_login(self.user)

        stats = self.client.get("/stats/", {"ale_experiment_id": experiment_id})
        self.assertEqual(stats.status_code, 200)
        stats_html = stats.content.decode("utf-8")
        # Every sample is listed, as plain text rather than a dead report link.
        for name in self.NON_AFIR_NAMES:
            self.assertIn(name[:-3], stats_html)

        mutations = self.client.get("/mutations/", {"ale_experiment_id": experiment_id})
        self.assertEqual(mutations.status_code, 200)
        mutations_html = mutations.content.decode("utf-8")
        self.assertNotIn("Page not available", mutations_html)
        self.assertNotIn("name 'ale' is not defined", mutations_html)

        metadata = self.client.get("/metadata/", {"ale_experiment_id": experiment_id})
        self.assertEqual(metadata.status_code, 200)

