import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import import_registry
from aledb_import.tests import breseq_fixture
from aledb_import.tests.test_reference_upload import write_genbank
from aledb_seq.models import ExperimentReference, ResequencingExperiment

SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A)]


class ImportRegistryRoutingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        from aledb_import.gd_import import _prepare_experiment
        self.experiment = _prepare_experiment(
            "reg project", "reg exp", "tester", False)["experiment"]

    def _write(self, relative, text):
        path = os.path.join(self.drop, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def _run(self, import_type=None):
        return import_registry.run_import(
            self.experiment, self.drop, self.user, import_type=import_type)

    # --- the three core types are registered ------------------------------------------

    def test_core_types_are_registered_in_priority_order(self):
        names = [t["name"] for t in import_registry.get_import_types()]
        self.assertEqual(
            names[:4], ["reference", "replace_annotation", "breseq_folder", "genomediff"])
        # Reference must run before anything that is checked against it.
        handlers = import_registry.get_import_handlers()
        by_name = {h["name"]: h["priority"] for h in handlers}
        self.assertLess(by_name["reference"], by_name["breseq_folder"])
        self.assertLess(by_name["reference"], by_name["genomediff"])

    # --- auto-detect ------------------------------------------------------------------

    def test_auto_detect_routes_a_mixed_drop(self):
        """Reference + breseq folder + bare .gd in one drop, each to its own handler."""
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        breseq_fixture.write_sample(self.drop, "s1")
        self._write("Ara-1_500gen_762B.gd", breseq_fixture.GD_TEXT)

        summary = self._run()
        results = {r["file"]: r for r in summary["files"]}

        self.assertIsNone(results["REL606.gbk"]["error"])
        self.assertIsNone(results["s1"]["error"])
        self.assertIsNone(results["Ara-1_500gen_762B.gd"]["error"])
        self.assertEqual(ExperimentReference.objects.count(), 1)
        self.assertEqual(ResequencingExperiment.objects.count(), 2)

    def test_reference_is_established_before_a_bare_gd_even_though_listed_later(self):
        """Priority, not drop order, decides. A .gd alone would otherwise be rejected."""
        self._write("zzz_sample.gd", breseq_fixture.GD_TEXT)
        write_genbank(os.path.join(self.drop, "REL606.gbk"))

        summary = self._run()
        results = {r["file"]: r for r in summary["files"]}
        self.assertIsNone(results["zzz_sample.gd"]["error"])
        self.assertGreater(summary["total_mutations"], 0)

    def test_bare_gd_without_a_reference_is_reported_not_imported(self):
        self._write("sample.gd", breseq_fixture.GD_TEXT)
        summary = self._run()

        self.assertIn("no reference genome", summary["files"][0]["error"])
        self.assertEqual(ResequencingExperiment.objects.count(), 0)

    def test_breseq_sample_gd_is_not_also_claimed_as_a_bare_gd(self):
        """output/annotated.gd belongs to its sample; claiming it twice would double-import."""
        breseq_fixture.write_sample(self.drop, "s1")
        summary = self._run()

        self.assertEqual(len(summary["files"]), 1)
        self.assertEqual(summary["files"][0]["file"], "s1")
        self.assertEqual(ResequencingExperiment.objects.count(), 1)

    def test_unrecognised_files_are_reported(self):
        self._write("notes.txt", "just notes\n")
        summary = self._run()
        self.assertIn("not recognised", summary["files"][0]["error"])

    # --- explicit type selection ------------------------------------------------------

    def test_choosing_a_type_forces_it(self):
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        summary = self._run(import_type="reference")

        self.assertIsNone(summary["files"][0]["error"])
        self.assertEqual(ExperimentReference.objects.count(), 1)

    def test_choosing_a_type_reports_files_it_does_not_claim(self):
        """The point of choosing explicitly: nothing gets quietly routed elsewhere."""
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        self._write("sample.gd", breseq_fixture.GD_TEXT)

        summary = self._run(import_type="reference")
        results = {r["file"]: r for r in summary["files"]}

        self.assertIsNone(results["REL606.gbk"]["error"])
        self.assertIn("not recognised as", results["sample.gd"]["error"])
        self.assertEqual(ResequencingExperiment.objects.count(), 0)

    def test_unknown_type_is_an_error(self):
        with self.assertRaises(ValueError):
            self._run(import_type="no_such_type")

    # --- replace_annotation -----------------------------------------------------------

    def test_replace_annotation_never_fires_on_auto_detect(self):
        """It claims the same files as `reference`, so only its lower priority keeps the
        two apart. Auto-detect must establish the reference, not replace an annotation."""
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        summary = self._run()

        self.assertIsNone(summary["files"][0]["error"])
        self.assertEqual(ExperimentReference.objects.count(), 1)

    def test_replace_annotation_refuses_when_there_is_no_reference_yet(self):
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        summary = self._run(import_type="replace_annotation")

        self.assertIn("no reference genome yet", summary["files"][0]["error"])
        self.assertEqual(ExperimentReference.objects.count(), 0)

    def test_replace_annotation_refreshes_the_annotation_of_the_same_genome(self):
        self._write("ref.fasta", breseq_fixture.fasta_text(SEQUENCES))
        self._run(import_type="reference")
        before = ExperimentReference.objects.get()

        os.remove(os.path.join(self.drop, "ref.fasta"))
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        summary = self._run(import_type="replace_annotation")

        self.assertIsNone(summary["files"][0]["error"])
        after = ExperimentReference.objects.get()
        self.assertEqual(after.pk, before.pk)
        self.assertEqual(after.fasta_sha256, before.fasta_sha256)      # same genome
        self.assertNotEqual(after.gff3_sha256, before.gff3_sha256)     # new annotation

    def test_replace_annotation_refuses_a_different_genome(self):
        """The whole point of the narrowed option: the sequence may not move."""
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        self._run(import_type="reference")
        before = ExperimentReference.objects.get()

        os.remove(os.path.join(self.drop, "REL606.gbk"))
        self._write("other.fasta", breseq_fixture.fasta_text(
            [("test_ref", breseq_fixture.SEQUENCE_B)]))
        summary = self._run(import_type="replace_annotation")

        self.assertIn("annotation can only be replaced", summary["files"][0]["error"])
        after = ExperimentReference.objects.get()
        self.assertEqual(after.fasta_sha256, before.fasta_sha256)
        self.assertEqual(after.gff3_sha256, before.gff3_sha256)


class PluggableImportTypeTestCase(TestCase):
    """A third-party app must be able to add an import type without touching core."""

    def setUp(self):
        self.calls = []

        def handle(experiment, staged_root, paths, user):
            self.calls.append(sorted(paths))
            return {"files": [{"file": p, "mutations": 7, "error": None} for p in paths],
                    "total_mutations": 7 * len(paths)}

        import_registry.register_import_handler(
            name="test_plugin_type",
            label="Test plugin measurements (.tsv)",
            patterns=[".tsv"],
            handle=handle)
        self.addCleanup(self._unregister, "test_plugin_type")

        # try_creating_project -> find_user prompts on stdin when the name matches no user,
        # so the user must exist before an experiment is prepared by name.
        User.objects.create(username="tester", email="t@e.com", is_active=True)

        self.drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)

        from aledb_import.gd_import import _prepare_experiment
        self.experiment = _prepare_experiment(
            "plug project", "plug exp", "tester", False)["experiment"]

    @staticmethod
    def _unregister(name):
        import_registry._import_handlers[:] = [
            h for h in import_registry._import_handlers if h["name"] != name]

    def test_plugin_type_appears_in_the_dropdown(self):
        labels = {t["name"]: t["label"] for t in import_registry.get_import_types()}
        self.assertEqual(labels["test_plugin_type"], "Test plugin measurements (.tsv)")

    def test_plugin_type_receives_its_files(self):
        with open(os.path.join(self.drop, "readings.tsv"), "w") as handle:
            handle.write("a\tb\n")

        summary = import_registry.run_import(self.experiment, self.drop, None)

        self.assertEqual(self.calls, [["readings.tsv"]])
        self.assertEqual(summary["total_mutations"], 7)
        self.assertIsNone(summary["files"][0]["error"])

    def test_duplicate_registration_is_refused(self):
        with self.assertRaises(ValueError):
            import_registry.register_import_handler(
                name="test_plugin_type", label="dupe", patterns=[".x"], handle=lambda *a: {})
