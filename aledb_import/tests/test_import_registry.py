import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import import_registry
from aledb_import.tests import breseq_fixture
from aledb_import.tests.test_reference_upload import write_genbank
from aledb_experiment.models import AleExperiment
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
        """data/output.gd belongs to its sample; claiming it twice would double-import."""
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

    def test_the_rejection_names_the_type_the_file_belongs_to(self):
        """"Not recognised" alone leaves someone to work out which of four types to pick;
        the registry's own patterns already know, plugins included."""
        self._write("sample.gd", breseq_fixture.GD_TEXT)

        summary = self._run(import_type="reference")
        error = summary["files"][0]["error"]

        self.assertIn("not recognised as Reference genome", error)
        self.assertIn("GenomeDiff mutations", error)

    def test_a_file_no_type_claims_is_still_just_unrecognised(self):
        """There is nothing to point at, and inventing a suggestion would be worse."""
        self._write("notes.txt", "just notes\n")

        error = self._run(import_type="reference")["files"][0]["error"]

        self.assertIn("not recognised as Reference genome", error)
        self.assertNotIn("looks like", error)

    def test_a_genomediff_dropped_as_a_reference_says_what_it_is(self):
        """Past the suffix check -- a .gd renamed .gbk -- the content has to answer, or
        the user gets Biopython's "No GenBank records found" and no way forward."""
        self._write("REL606.gbk", breseq_fixture.GD_TEXT)

        error = self._run(import_type="reference")["files"][0]["error"]

        self.assertIn("GenomeDiff", error)
        self.assertIn("not a reference genome", error)

    def test_a_reference_dropped_as_a_genomediff_says_what_it_is(self):
        """The mirror image: a GenBank named .gd reaches the GenomeDiff parser."""
        self._write("ref.fasta", breseq_fixture.fasta_text(SEQUENCES))
        self._run(import_type="reference")
        os.remove(os.path.join(self.drop, "ref.fasta"))

        write_genbank(os.path.join(self.drop, "REL606.gd"))
        error = self._run(import_type="genomediff")["files"][0]["error"]

        self.assertIn("GenBank", error)
        self.assertIn("Reference genome", error)

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
        """The whole point of the narrowed option: the sequence may not move.

        Carried by a GFF3 rather than a FASTA -- the check is on the sequence, but
        replace_annotation only claims formats that actually carry annotation, so a
        FASTA never reaches it (see the test below).
        """
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        self._run(import_type="reference")
        before = ExperimentReference.objects.get()

        os.remove(os.path.join(self.drop, "REL606.gbk"))
        self._write("other.gff3", breseq_fixture.gff3_text(
            [("test_ref", breseq_fixture.SEQUENCE_B)]))
        summary = self._run(import_type="replace_annotation")

        self.assertIn("annotation can only be replaced", summary["files"][0]["error"])
        after = ExperimentReference.objects.get()
        self.assertEqual(after.fasta_sha256, before.fasta_sha256)
        self.assertEqual(after.gff3_sha256, before.gff3_sha256)

    def test_a_fasta_is_accepted_but_installs_no_annotation(self):
        """A FASTA carries no features, so there is nothing in one to install -- but this
        type is also how contigs are renamed, and a rename with no annotation change is a
        legitimate thing to arrive holding. It is claimed, and it leaves the stored
        annotation alone rather than replacing a gene table with nothing."""
        write_genbank(os.path.join(self.drop, "REL606.gbk"))
        self._run(import_type="reference")
        before = ExperimentReference.objects.get().gff3_sha256

        os.remove(os.path.join(self.drop, "REL606.gbk"))
        self._write("same.fasta", breseq_fixture.fasta_text(
            [("test_ref", breseq_fixture.SEQUENCE_A)]))
        summary = self._run(import_type="replace_annotation")

        self.assertIsNone(summary["files"][0]["error"])
        self.assertEqual(before, ExperimentReference.objects.get().gff3_sha256)


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


class PostProcessingTestCase(TestCase):
    """Importing through the registry must recompute what the new data changed.

    `run_post_processing` refreshes the experiment filter, the static stats, the dashboard
    and -- through `run_post_experiment_hooks` -- every plugin's derived table. The CLI path
    (`gd_import.import_genomediffs`) and the breseq-folder handler both called it; the
    genomediff handler did not.

    Nothing noticed, because the things it drives are all things whose absence looks like an
    empty result: a .gd dropped on the Add Data page left Fixed Mutations and Converged
    Mutations blank however good the data was, and the stats showing whatever the last CLI
    upload had left. Core cannot see a plugin, so this asserts the hook runs rather than what
    any plugin does with it.
    """

    FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "annotate", "tests", "fixtures")

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        self.staged = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.staged, True)
        shutil.copy(os.path.join(self.FIXTURES, "synthetic.gbk"),
                    os.path.join(self.staged, "synthetic.gbk"))
        with open(os.path.join(self.staged, "1-100-1-1.gd"), "w") as handle:
            handle.write("#=GENOME_DIFF\t1.0\n"
                         "#=COMMAND\tbreseq -r synthetic.gbk -o s r.fastq\n"
                         "#=REFSEQ\tsynthetic.gbk\n"
                         "SNP\t1\t.\tSYN001\t150\tT\tfrequency=1\n")

    @override_settings(ALEDB_STORE_DIR=None)
    def test_a_genomediff_import_runs_the_post_experiment_hooks(self):
        from aledb_common import plugin_registry
        from aledb_common.import_registry import run_import
        from aledb_common.rebuild_registry import unregister_rebuilder

        seen = []
        registered = plugin_registry.register_post_experiment_hook(
            lambda exp_id: seen.append(exp_id))
        self.addCleanup(unregister_rebuilder, registered)

        with tempfile.TemporaryDirectory() as store:
            with override_settings(ALEDB_STORE_DIR=store):
                run_import(self.experiment, self.staged, self.user)

        self.assertEqual([self.experiment.id], seen,
                         "the hook must fire once, after every file")

    @override_settings(ALEDB_STORE_DIR=None)
    def test_it_fires_once_rather_than_per_file(self):
        """Fixation compares an ALE's last two flasks, which is not knowable until every
        sample is in -- so a per-file rebuild would compute an answer from partial data."""
        from aledb_common import plugin_registry
        from aledb_common.import_registry import run_import
        from aledb_common.rebuild_registry import unregister_rebuilder

        with open(os.path.join(self.staged, "1-200-1-1.gd"), "w") as handle:
            handle.write("#=GENOME_DIFF\t1.0\n"
                         "#=COMMAND\tbreseq -r synthetic.gbk -o s2 r.fastq\n"
                         "#=REFSEQ\tsynthetic.gbk\n"
                         "SNP\t1\t.\tSYN001\t150\tT\tfrequency=1\n")

        seen = []
        registered = plugin_registry.register_post_experiment_hook(
            lambda exp_id: seen.append(exp_id))
        self.addCleanup(unregister_rebuilder, registered)

        with tempfile.TemporaryDirectory() as store:
            with override_settings(ALEDB_STORE_DIR=store):
                run_import(self.experiment, self.staged, self.user)

        self.assertEqual(1, len(seen), "once for the drop, not once per .gd")

    @override_settings(ALEDB_STORE_DIR=None)
    def test_an_import_that_stored_nothing_does_not_rebuild(self):
        """A reference on its own creates no mutations, so there is nothing to recompute."""
        from aledb_common import plugin_registry
        from aledb_common.import_registry import run_import
        from aledb_common.rebuild_registry import unregister_rebuilder

        os.remove(os.path.join(self.staged, "1-100-1-1.gd"))
        seen = []
        registered = plugin_registry.register_post_experiment_hook(
            lambda exp_id: seen.append(exp_id))
        self.addCleanup(unregister_rebuilder, registered)

        with tempfile.TemporaryDirectory() as store:
            with override_settings(ALEDB_STORE_DIR=store):
                run_import(self.experiment, self.staged, self.user)

        self.assertEqual([], seen)


class ReferenceResultKindTestCase(TestCase):
    """A reference genome's row says what it is rather than how many mutations it carried.

    Reported as `mutations: 0` it read as a mutation file that imported nothing -- which is
    the one thing a reference is not, and the Add page said so twice: once in the file
    table's count column and once in "Added to E (#1): 0 mutations". The count is absent
    now and `kind` carries what the file was, so the page has something to render instead of
    a number that was never about this file.
    """

    FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "annotate", "tests", "fixtures")

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        self.staged = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.staged, True)
        shutil.copy(os.path.join(self.FIXTURES, "synthetic.gbk"),
                    os.path.join(self.staged, "synthetic.gbk"))
        with open(os.path.join(self.staged, "1-100-1-1.gd"), "w") as handle:
            handle.write("#=GENOME_DIFF\t1.0\n"
                         "#=COMMAND\tbreseq -r synthetic.gbk -o s r.fastq\n"
                         "#=REFSEQ\tsynthetic.gbk\n"
                         "SNP\t1\t.\tSYN001\t150\tT\tfrequency=1\n")

    def _run(self):
        from aledb_common.import_registry import run_import

        with tempfile.TemporaryDirectory() as store:
            with override_settings(ALEDB_STORE_DIR=store):
                summary = run_import(self.experiment, self.staged, self.user)
        return {entry["file"]: entry for entry in summary["files"]}, summary

    def test_the_reference_reports_a_kind_and_no_count(self):
        results, _ = self._run()

        reference = results["synthetic.gbk"]
        self.assertEqual(import_registry.KIND_REFERENCE, reference["kind"])
        self.assertIsNone(reference["mutations"])

    def test_a_mutation_file_still_reports_a_count_and_no_kind(self):
        """The kind is what marks the exception, so every other handler must not carry one --
        a row with no `kind` is a row whose number is a mutation count."""
        results, summary = self._run()

        gd = results["1-100-1-1.gd"]
        self.assertIsNone(gd.get("kind"))
        self.assertEqual(1, gd["mutations"])
        self.assertEqual(1, summary["total_mutations"])

    def test_the_progress_snapshot_carries_the_kind_through(self):
        """The table is drawn from the poll while the import runs and from the summary
        afterwards. A kind that reached only the second would make the reference's row change
        what it said the moment the import finished."""
        from aledb_common import import_progress

        events = []
        with tempfile.TemporaryDirectory() as store:
            with override_settings(ALEDB_STORE_DIR=store):
                with import_progress.reporting(events.append):
                    self._run()

        reported = [e for e in events
                    if e.get("event") == "file" and e.get("file") == "synthetic.gbk"]
        self.assertEqual(1, len(reported), events)
        self.assertEqual(import_registry.KIND_REFERENCE, reported[0]["kind"])
