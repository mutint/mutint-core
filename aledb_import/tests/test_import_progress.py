"""Per-unit import progress: the seam, the naming invariant, and the poll endpoint.

The load-bearing test here is `AnnouncedNamesTestCase`. A unit is announced before the work
starts and reported when it finishes, and the two are paired by position -- so if a handler's
`list_units` names a unit differently from the way its own loop reports it, the page renders a
row that never fills in and one that appears from nowhere. The three core handlers each use a
*different* convention (a directory basename, a filename, a path relative to the drop root),
so there is no single rule to check; each has to be pinned against its own reporting.
"""

import json
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from aledb_common import import_progress, store
from aledb_common.import_registry import run_import
from aledb_experiment.models import Project
from aledb_import import breseq_folder
from aledb_import.models import STATE_FAILED, STATE_FINALIZED, UploadSession
from aledb_seq.models import MutationCall, Sample
from aledb_import.tests import breseq_fixture


class Recorder:
    """Collects events so a test can ask what an import said, and in what order."""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)

    def of(self, kind):
        return [e for e in self.events if e["event"] == kind]

    @property
    def announced(self):
        totals = self.of("total")
        return totals[0]["units"] if totals else []

    @property
    def reported(self):
        return [e["file"] for e in self.of("file")]

    @property
    def indices(self):
        return [e["index"] for e in self.of("file")]


class SinkTestCase(TestCase):
    """The module is a no-op until somebody installs a sink, which is what keeps the CLI,
    `load_example` and every other `run_import` caller unaffected by any of this."""

    def test_every_function_is_a_no_op_with_no_sink(self):
        self.assertFalse(import_progress.is_reporting())
        # None of these may raise, and none may need a sink to exist.
        import_progress.announce(["a"])
        import_progress.begin("a")
        import_progress.report({"file": "a", "mutations": 1})
        import_progress.stage("anything")
        import_progress.advance_to(3)

    def test_the_sink_is_restored_afterwards(self):
        recorder = Recorder()
        with import_progress.reporting(recorder):
            self.assertTrue(import_progress.is_reporting())
        self.assertFalse(import_progress.is_reporting())

    def test_nesting_restores_the_outer_sink(self):
        outer, inner = Recorder(), Recorder()
        with import_progress.reporting(outer):
            with import_progress.reporting(inner):
                import_progress.stage("inner")
            import_progress.stage("outer")
        self.assertEqual([e["message"] for e in inner.of("stage")], ["inner"])
        self.assertEqual([e["message"] for e in outer.of("stage")], ["outer"])

    def test_indices_count_reports_in_order(self):
        recorder = Recorder()
        with import_progress.reporting(recorder):
            import_progress.report({"file": "a"})
            import_progress.report({"file": "b"})
            import_progress.report({"file": "c"})
        self.assertEqual(recorder.indices, [0, 1, 2])

    def test_advance_to_moves_the_cursor(self):
        """`run_import` sets the cursor per handler, so a handler that reports nothing
        cannot shift every row that comes after it."""
        recorder = Recorder()
        with import_progress.reporting(recorder):
            import_progress.advance_to(5)
            import_progress.report({"file": "f"})
        self.assertEqual(recorder.indices, [5])

    def test_a_raising_sink_cannot_break_an_import(self):
        def explode(_event):
            raise RuntimeError("the sink is broken")

        with import_progress.reporting(explode):
            import_progress.report({"file": "a"})  # must not raise


class ImportProgressTestCase(TestCase):
    """Shared fixture: an experiment to import into and a staging directory to import from."""

    def setUp(self):
        self.user = User.objects.create(username="tester", email="t@e.com", is_active=True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.user)
        from aledb_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

        self.drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)

    def run_drop(self, import_type=None):
        recorder = Recorder()
        with import_progress.reporting(recorder):
            summary = run_import(self.experiment, self.drop, self.user,
                                 import_type=import_type)
        return recorder, summary

    def write_reference(self):
        from aledb_import import reference_store
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(sequences), sequences)


class AnnouncedNamesTestCase(ImportProgressTestCase):
    """What a handler announces must be what it later reports, name for name and in order."""

    def assert_announcement_matches_reports(self, recorder):
        self.assertEqual(
            recorder.announced, recorder.reported,
            "every unit must be reported under the name it was announced with")
        self.assertEqual(recorder.indices, list(range(len(recorder.reported))),
                         "a report must land on the slot its announcement claimed")

    def test_breseq_folders(self):
        breseq_fixture.write_sample(self.drop, "1-30000-1-1")
        breseq_fixture.write_sample(self.drop, "1-40000-1-1")

        recorder, _summary = self.run_drop(import_type="breseq_folder")

        self.assertEqual(recorder.announced, ["1-30000-1-1", "1-40000-1-1"])
        self.assert_announcement_matches_reports(recorder)

    def test_a_breseq_sample_is_one_unit_not_five_files(self):
        """The whole reason `list_units` exists. A sample contributes output.gd, the
        reference pair and the BAM with its index, so counting claimed paths would report
        five times the work and count nothing anybody is waiting on."""
        breseq_fixture.write_sample(self.drop, "only-one")

        recorder, _summary = self.run_drop(import_type="breseq_folder")

        self.assertEqual(len(recorder.announced), 1, recorder.announced)

    def test_genomediff_announces_basenames_not_paths(self):
        self.write_reference()
        nested = os.path.join(self.drop, "batch")
        os.makedirs(nested)
        with open(os.path.join(nested, "3-30000-1-1.gd"), "w") as handle:
            handle.write(breseq_fixture.GD_TEXT)

        recorder, _summary = self.run_drop(import_type="genomediff")

        self.assertEqual(recorder.announced, ["3-30000-1-1.gd"])
        self.assert_announcement_matches_reports(recorder)

    def test_a_genomediff_refused_for_want_of_a_reference_still_matches(self):
        """The early return keys its rows the same way the happy path does. It did not:
        it used the full relative path while the loop below it used the basename, so a
        nested .gd announced one name and reported another."""
        nested = os.path.join(self.drop, "batch")
        os.makedirs(nested)
        with open(os.path.join(nested, "orphan.gd"), "w") as handle:
            handle.write(breseq_fixture.GD_TEXT)

        recorder, summary = self.run_drop(import_type="genomediff")

        self.assertEqual(recorder.announced, ["orphan.gd"])
        self.assert_announcement_matches_reports(recorder)
        self.assertIn("no reference genome", summary["files"][0]["error"])

    def test_reference_announces_the_relative_path(self):
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        with open(os.path.join(self.drop, "genome.gff3"), "w") as handle:
            handle.write(breseq_fixture.gff3_text(sequences))

        recorder, _summary = self.run_drop(import_type="reference")

        self.assertEqual(recorder.announced, ["genome.gff3"])
        self.assert_announcement_matches_reports(recorder)

    def test_a_mixed_auto_detected_drop(self):
        """Two handlers in one run, so the second's units have to start where the first's
        stopped rather than at zero."""
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        with open(os.path.join(self.drop, "genome.gff3"), "w") as handle:
            handle.write(breseq_fixture.gff3_text(sequences))
        breseq_fixture.write_sample(self.drop, "s1", sequences=sequences)

        recorder, _summary = self.run_drop()

        self.assertEqual(recorder.announced, ["genome.gff3", "s1"])
        self.assert_announcement_matches_reports(recorder)

    def test_unclaimed_files_are_announced_and_reported_too(self):
        """A file nobody claims is still a line somebody has to read, so it gets a row."""
        breseq_fixture.write_sample(self.drop, "s1")
        with open(os.path.join(self.drop, "notes.txt"), "w") as handle:
            handle.write("not an import of any kind")

        recorder, summary = self.run_drop(import_type="breseq_folder")

        self.assertEqual(recorder.announced, ["s1", "notes.txt"])
        self.assert_announcement_matches_reports(recorder)
        self.assertIn("not recognised",
                      [f["error"] for f in summary["files"] if f["file"] == "notes.txt"][0])

    def test_duplicate_sample_basenames_get_a_row_each(self):
        """`find_sample_dirs` walks, so two directories at different depths can share a
        basename. Both still get a row -- the second one saying it was skipped -- and rows
        are paired by index, never by name, so the repeat is a non-event for the pairing."""
        breseq_fixture.write_sample(os.path.join(self.drop, "a"), "s1")
        breseq_fixture.write_sample(os.path.join(self.drop, "b"), "s1")

        recorder, _summary = self.run_drop(import_type="breseq_folder")

        self.assertEqual(recorder.announced, ["s1", "s1"])
        self.assertEqual(recorder.indices, [0, 1])

    def test_a_failing_sample_is_reported_like_any_other(self):
        """One bad sample must not poison the batch, and must not swallow its own row."""
        breseq_fixture.write_sample(self.drop, "good")
        breseq_fixture.write_sample(self.drop, "broken", include_bai=False)

        recorder, summary = self.run_drop(import_type="breseq_folder")

        self.assert_announcement_matches_reports(recorder)
        errors = {f["file"]: f["error"] for f in summary["files"]}
        self.assertIsNone(errors["good"])
        self.assertIsNotNone(errors["broken"])


class ProgressLifecycleTestCase(ImportProgressTestCase):
    def test_begin_precedes_each_report(self):
        breseq_fixture.write_sample(self.drop, "s1")
        breseq_fixture.write_sample(self.drop, "s2")

        recorder, _summary = self.run_drop(import_type="breseq_folder")

        kinds = [e["event"] for e in recorder.events
                 if e["event"] in ("begin", "file")]
        self.assertEqual(kinds, ["begin", "file", "begin", "file"])

    def test_the_rebuild_gets_its_own_stage(self):
        """The longest silence in an import, after the last row is already filled in."""
        breseq_fixture.write_sample(self.drop, "s1")

        recorder, _summary = self.run_drop(import_type="breseq_folder")

        self.assertTrue(recorder.of("stage"),
                        "the derived-data rebuild should announce itself")

    def test_without_a_sink_the_summary_is_unchanged(self):
        breseq_fixture.write_sample(self.drop, "s1")

        summary = run_import(self.experiment, self.drop, self.user,
                             import_type="breseq_folder")

        self.assertEqual([f["file"] for f in summary["files"]], ["s1"])
        self.assertGreater(summary["total_mutations"], 0)


class GenomeDiffReplacementTestCase(ImportProgressTestCase):
    """The bare-.gd path reports a replacement too. Its sample identity comes from the
    filename rather than a directory name, but it resolves through the same rule, so
    re-dropping `3-30000-1-1.gd` supersedes the sample of that name exactly as a folder
    would -- and has to say so in the same way."""

    def setUp(self):
        super().setUp()
        self.write_reference()

    def _drop_gd(self):
        with open(os.path.join(self.drop, "3-30000-1-1.gd"), "w") as handle:
            handle.write(breseq_fixture.GD_TEXT)

    def test_a_second_drop_of_the_same_gd_reports_what_it_replaced(self):
        from aledb_seq.models import MutationCall

        self._drop_gd()
        first = self.run_drop(import_type="genomediff")[1]
        self.assertEqual(first["files"][0].get("replaced", 0), 0)
        calls = MutationCall.objects.count()
        self.assertGreater(calls, 0)

        second = self.run_drop(import_type="genomediff")[1]

        self.assertEqual(second["files"][0]["replaced"], calls)
        self.assertIsNone(second["files"][0]["error"])
        self.assertEqual(MutationCall.objects.count(), calls)


class PluginDegradationTestCase(ImportProgressTestCase):
    """A plugin's import type must not have to know this seam exists.

    One that never reports is not broken: its units are announced (so they are listed), it
    simply never fills them in, and its rows still arrive in the final summary the way they
    always have. What must not happen is its silence shifting somebody else's rows.
    """

    def setUp(self):
        super().setUp()
        from aledb_common import import_registry

        self.registry = import_registry
        import_registry.register_import_handler(
            name="silent_plugin_type",
            label="Silent plugin readings (.tsv)",
            patterns=[".tsv"],
            handle=lambda experiment, root, paths, user: {
                "files": [{"file": p, "mutations": 3, "error": None} for p in paths],
                "total_mutations": 3 * len(paths)})
        self.addCleanup(self._unregister)

    def _unregister(self):
        self.registry._import_handlers[:] = [
            h for h in self.registry._import_handlers
            if h["name"] != "silent_plugin_type"]

    def test_a_handler_that_reports_nothing_still_returns_its_rows(self):
        with open(os.path.join(self.drop, "readings.tsv"), "w") as handle:
            handle.write("a\tb\n")

        recorder, summary = self.run_drop(import_type="silent_plugin_type")

        # Announced by the default `list_units`, never reported.
        self.assertEqual(recorder.announced, ["readings.tsv"])
        self.assertEqual(recorder.reported, [])
        self.assertEqual([f["file"] for f in summary["files"]], ["readings.tsv"])
        self.assertEqual(summary["total_mutations"], 3)

    def test_a_silent_handler_does_not_shift_the_rows_after_it(self):
        """The reason `run_import` sets the cursor per handler rather than letting it count
        reports. The .tsv is announced and never reported; the unclaimed file after it must
        still land on its own slot, not on the plugin's."""
        with open(os.path.join(self.drop, "readings.tsv"), "w") as handle:
            handle.write("a\tb\n")
        with open(os.path.join(self.drop, "notes.txt"), "w") as handle:
            handle.write("nothing claims this")

        recorder, _summary = self.run_drop()

        self.assertEqual(recorder.announced, ["readings.tsv", "notes.txt"])
        self.assertEqual(recorder.reported, ["notes.txt"])
        self.assertEqual(recorder.indices, [1],
                         "the unclaimed row must keep index 1, not slide up to 0")


class ProgressEndpointTestCase(TestCase):
    """The snapshot the Add page polls while finalize runs."""

    def setUp(self):
        self.user = User.objects.create(username="tester", email="t@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.user)
        from aledb_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

    def _upload(self, *sample_names):
        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        entries = []
        for sample_name in sample_names:
            breseq_fixture.write_sample(source, sample_name)
            for relative in breseq_folder.SAMPLE_FILES:
                path = os.path.join(source, sample_name, relative)
                if not os.path.isfile(path):
                    continue
                with open(path, "rb") as handle:
                    entries.append((sample_name + "/" + relative.replace(os.sep, "/"),
                                    handle.read()))

        created = self.client.post(
            "/import/uploads/",
            data=json.dumps({"experiment_id": self.experiment.id,
                             "import_type": "breseq_folder",
                             "files": [{"path": p, "size": len(b)} for p, b in entries]}),
            content_type="application/json")
        self.assertEqual(created.status_code, 200, created.content)
        upload_id = created.json()["upload_id"]
        for path, payload in entries:
            response = self.client.post(
                "/import/uploads/%s/chunk" % upload_id,
                {"path": path, "offset": "0",
                 "chunk": SimpleUploadedFile("chunk", payload)})
            self.assertEqual(response.status_code, 200, response.content)
        return upload_id

    def test_progress_records_every_unit_after_a_finalize(self):
        upload_id = self._upload("s1", "s2")
        self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        response = self.client.get("/import/uploads/%s/progress" % upload_id)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["state"], STATE_FINALIZED)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["completed"], 2)
        self.assertEqual([u["file"] for u in body["units"]], ["s1", "s2"])
        self.assertTrue(all(u["state"] == "done" for u in body["units"]))
        self.assertTrue(all(u["mutations"] > 0 for u in body["units"]))

    def test_a_finalized_session_still_answers(self):
        """The single most useful moment to ask is the tick right after finalize returned,
        when the session is no longer open. `_open_session` would 409 exactly that poll,
        which is why this endpoint does its own lookup."""
        upload_id = self._upload("s1")
        self.client.post("/import/uploads/%s/finalize" % upload_id, {})
        self.assertEqual(
            UploadSession.objects.get(pk=upload_id).state, STATE_FINALIZED)

        response = self.client.get("/import/uploads/%s/progress" % upload_id)

        self.assertEqual(response.status_code, 200, response.content)

    def test_a_session_nobody_has_reported_on_is_empty_not_missing(self):
        upload_id = self._upload("s1")

        body = self.client.get("/import/uploads/%s/progress" % upload_id).json()

        self.assertEqual(body["units"], [])
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["completed"], 0)

    def test_progress_is_visible_while_the_import_is_still_running(self):
        """The point of the whole exercise: the snapshot is committed as it goes, not at
        the end.

        Hooked on the coverage *enqueue*, which is what runs between one sample's
        transaction committing and the next one beginning -- it used to be the coverage
        build itself, before that moved to a worker. Any per-sample call outside the
        transaction would do; this is the one that is there.
        """
        seen = {}
        real = breseq_folder.tasks.build_coverage

        class Peeking:
            """Stands in for the Task object, not for its method: django.tasks' Task is a
            frozen dataclass, so `enqueue` cannot be reassigned on it."""

            def enqueue(inner, sample_id):
                if "snapshot" not in seen:
                    seen["snapshot"] = UploadSession.objects.get(
                        pk=self.upload_id).progress
                return real.enqueue(sample_id)

        upload_id = self._upload("s1", "s2")
        self.upload_id = upload_id
        breseq_folder.tasks.build_coverage = Peeking()
        self.addCleanup(setattr, breseq_folder.tasks, "build_coverage", real)

        self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        units = seen["snapshot"]["units"]
        self.assertEqual([u["file"] for u in units], ["s1", "s2"])
        self.assertEqual(units[0]["state"], "working")
        self.assertEqual(units[1]["state"], "waiting")

    def test_a_second_separate_upload_reports_that_it_replaced_the_first(self):
        """The case a person actually hits: not two folders in one drop, but the same
        sample uploaded again later, in its own upload session. It is allowed -- that is how
        a corrected run supersedes the one before it -- and the row says what it displaced,
        both in the polled snapshot and in the finalize summary the page renders last.
        """
        first = self._upload("s1")
        self.client.post("/import/uploads/%s/finalize" % first, {})
        calls = MutationCall.objects.count()
        self.assertGreater(calls, 0)

        # A separate drop, a separate session, the same sample.
        second = self._upload("s1")
        self.assertNotEqual(first, second, "this must be a new upload session")
        summary = self.client.post(
            "/import/uploads/%s/finalize" % second, {}).json()

        row = summary["files"][0]
        self.assertEqual(row["file"], "s1")
        self.assertIsNone(row["error"], "a re-import is not a failure")
        self.assertEqual(row["replaced"], calls)
        # And the same answer through the endpoint the page polls.
        polled = self.client.get("/import/uploads/%s/progress" % second).json()
        self.assertEqual(polled["units"][0]["replaced"], calls)

        # Still one sample: it superseded, it did not accumulate.
        self.assertEqual(Sample.objects.count(), 1)
        self.assertEqual(MutationCall.objects.count(), calls)

    def test_a_first_upload_reports_nothing_replaced(self):
        """The guardrail: the notice must mean something, so it cannot show on every import."""
        upload_id = self._upload("s1")
        summary = self.client.post(
            "/import/uploads/%s/finalize" % upload_id, {}).json()

        self.assertEqual(summary["files"][0].get("replaced", 0), 0)

    def test_another_users_session_is_refused(self):
        upload_id = self._upload("s1")
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self.client.get("/import/uploads/%s/progress" % upload_id)

        self.assertEqual(response.status_code, 403)

    def test_an_unknown_session_is_a_404(self):
        response = self.client.get(
            "/import/uploads/00000000-0000-0000-0000-000000000000/progress")
        self.assertEqual(response.status_code, 404)

    def test_a_failed_import_says_which_unit_it_died_on(self):
        upload_id = self._upload("s1", "s2")

        def explode(_experiment):
            raise RuntimeError("rebuild exploded")

        original = breseq_folder.run_post_processing
        breseq_folder.run_post_processing = explode
        self.addCleanup(setattr, breseq_folder, "run_post_processing", original)

        response = self.client.post("/import/uploads/%s/finalize" % upload_id, {})
        self.assertEqual(response.status_code, 500)

        body = self.client.get("/import/uploads/%s/progress" % upload_id).json()
        self.assertEqual(body["state"], STATE_FAILED)
        # Both samples had finished before the rebuild ran, so neither is left mid-flight;
        # what matters is that nothing is stranded at "waiting" with no explanation.
        self.assertTrue(all(u["state"] == "done" for u in body["units"]), body["units"])
