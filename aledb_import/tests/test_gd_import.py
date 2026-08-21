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


def _uploaded_as(path, name):
    """Same fixture content under a different filename, to exercise name parsing."""
    with open(path, "rb") as handle:
        return SimpleUploadedFile(name, handle.read())


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

    def test_gd_imported_rows_have_no_report_location(self):
        """No breseq HTML exists for a bare .gd, so link-builders must get a falsy value
        rather than a location that renders as the string "None"."""
        self._import_named(self.NON_AFIR_NAMES[:1])

        self.assertFalse(ResequencingExperiment.objects.get().location)

    # --- the pages the post-import link lands on ---------------------------------------

    def test_imported_experiment_pages_render(self):
        """/stats and /mutations must render for a gd-imported experiment.

        Regression guard for three defects that all surfaced on these two pages: the
        NULL `location` rendering as the literal string "None" in an href, the
        `!= ""` guards that let that href through, and the `ale.common` NameError that
        turned /mutations into a 500 page for every experiment."""
        summary = self._import_named(self.NON_AFIR_NAMES)
        experiment_id = summary["experiment_id"]
        self.client.force_login(self.user)

        stats = self.client.get("/stats/", {"ale_experiment_id": experiment_id})
        self.assertEqual(stats.status_code, 200)
        stats_html = stats.content.decode("utf-8")
        self.assertNotIn("Noneindex.html", stats_html)
        self.assertNotIn('href="None', stats_html)
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
        self.assertNotIn('href="None', metadata.content.decode("utf-8"))

