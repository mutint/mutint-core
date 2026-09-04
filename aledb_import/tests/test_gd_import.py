import os
import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from aledb_experiment.models import (
    Experiment, Population,
)
from aledb_import import gd_import, reference_store
from aledb_import.tests import breseq_fixture
from aledb_sample.models import (
    Mutation, MutationCall, Sample, UncalledRegion,
)

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


def ensure_reference(experiment="gd exp", project="gd project"):
    """A bare .gd carries no reference, so the experiment must already have one.

    This is what dropping a GenBank, GFF3 or FASTA on the Add page does for a real user.

    Module level rather than a method, so a test case that wants the scaffolding does not
    have to subclass `GdImportTestCase` and re-run every one of its tests to get it.
    """
    context = gd_import._prepare_experiment(project, experiment, "tester", False)
    # Named for the contig the fixture .gd calls: a .gd may only be imported into
    # an experiment whose reference has the same seq_ids, matched exactly.
    sequences = [("REL606", breseq_fixture.SEQUENCE_A)]
    gff3_text = breseq_fixture.gff3_text(sequences)
    reference_store.establish_or_check(context["experiment"], gff3_text, sequences)
    return context["experiment"]


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
        return ensure_reference(experiment, project)

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
        self.assertEqual(MutationCall.objects.count(), len(expected))

        # Experiment chain synthesized from the filename 3-30000-1-1.
        self.assertEqual(Experiment.objects.count(), 1)
        ale_id = Population.objects.get()
        self.assertEqual(ale_id.name, "3")
        self.assertEqual({30000}, set(Sample.objects.values_list('time_point', flat=True)))
        # `1-1`, not `1`: the replicate field is part of the label, kept even when it is
        # 1 so that `3-30000-1-1` and `3-30000-1-2` are siblings rather than a bare `1`
        # beside a `1-2`.
        self.assertEqual(Sample.objects.get().name, "1-1")

        # gd_data captured on every row; REFSEQ propagated to the sample.
        self.assertFalse(Mutation.objects.filter(gd_data__isnull=True).exists())
        self.assertTrue(Sample.objects.get().reference_genome)

    def test_round_trip_is_apply_compatible(self):
        """Import -> export .gd -> re-parse yields the original mutations."""
        original = self._expected_mutations(CLEAN_GD)
        self._import(CLEAN_GD)

        seq_experiment = Sample.objects.get()
        gd_text = gd_import.export_gd_text(seq_experiment)
        reparsed = GenomeDiff.read(iter(gd_text.splitlines())).mutations

        self.assertEqual(_canonical(original), _canonical(reparsed))

    def test_each_experiment_owns_its_mutations(self):
        """Mutations are deduplicated within an experiment, not across them.

        They used to be shared globally, which meant re-annotating one experiment
        against a new reference would rewrite another experiment's annotation."""
        self._import(CLEAN_GD, experiment="exp one")
        per_experiment = Mutation.objects.count()

        self._import(CLEAN_GD, experiment="exp two")

        self.assertEqual(Experiment.objects.count(), 2)
        self.assertEqual(Sample.objects.count(), 2)
        self.assertEqual(MutationCall.objects.count(), per_experiment * 2)

        # Each experiment has its own copies, and no row is shared.
        self.assertEqual(Mutation.objects.count(), per_experiment * 2)
        for experiment in Experiment.objects.all():
            self.assertEqual(
                per_experiment,
                Mutation.objects.filter(experiment=experiment).count())

    def test_mutations_are_still_deduplicated_within_an_experiment(self):
        self._import(CLEAN_GD, experiment="exp one")
        per_experiment = Mutation.objects.count()

        # The same file again, as a second sample of the same experiment.
        self._import_named(["4-30000-1-1.gd"], experiment="exp one")

        self.assertEqual(Mutation.objects.count(), per_experiment)
        self.assertEqual(MutationCall.objects.count(), per_experiment * 2)

    def test_reimport_same_sample_is_idempotent(self):
        self._import(CLEAN_GD)
        self._import(CLEAN_GD)  # same experiment + filename

        self.assertEqual(Sample.objects.count(), 1)
        self.assertEqual(MutationCall.objects.count(), Mutation.objects.count())

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
            data=json.dumps({"experiment_id": experiment.id,
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

        reseq = Sample.objects.get()
        export = self.client.get("/import/gd/%d/export" % reseq.id)
        self.assertEqual(export.status_code, 200)
        self.assertIn("#=GENOME_DIFF", export.content.decode("utf-8"))

    # --- sample identity from the filename -------------------------------------------

    # Real-world names, and the second shape `sample_names` reads: one ALE sampled at three
    # time points. They used to run through parse_ale_name, whose bare `except: return 1`
    # mapped every one onto ALE 1 / flask 1 / isolate 1 -- a 22-file upload collapsing into
    # one sample -- and then, for a while, onto an auto-numbered isolate apiece under ALE 1,
    # which kept them addressable but still said nothing about when each was taken.
    TRIPLE_NAMES = [
        "Ara-1_500gen_762B.gd",
        "Ara-1_1000gen_964C.gd",
        "Ara-1_50000gen_11331.gd",
    ]

    #: Neither shape: two fields, so there is no telling which one is missing.
    UNPARSEABLE_NAMES = ["REL606_clone.gd", "everything.gd"]

    def _import_named(self, names, experiment="gd exp"):
        self._ensure_reference(experiment)
        return gd_import.import_gd_files(
            [_uploaded_as(CLEAN_GD, name) for name in names],
            project_name="gd project", experiment_name=experiment, person="tester")

    def test_underscore_triple_names_are_read_as_a_time_series(self):
        summary = self._import_named(self.TRIPLE_NAMES)

        self.assertEqual(len(summary["files"]), len(self.TRIPLE_NAMES))
        self.assertIsNone(summary["files"][0]["error"])

        # One sample per file, each individually addressable.
        self.assertEqual(Sample.objects.count(), len(self.TRIPLE_NAMES))
        self.assertEqual(
            sorted(Sample.objects.values_list("source_name", flat=True)),
            sorted(name[:-3] for name in self.TRIPLE_NAMES))

        # One ALE, named rather than numbered, sampled at three time points -- which is what
        # makes an experiment like this fixation-shaped at all: an ALE of one flask can
        # never fix anything, and these three used to be one flask.
        self.assertEqual(Population.objects.count(), 1)
        self.assertEqual(Population.objects.get().name, "Ara-1")
        self.assertEqual(
            sorted(set(Sample.objects.values_list("time_point", flat=True))),
            [500, 1000, 50000])
        self.assertEqual(
            sorted(Sample.objects.values_list("name", flat=True)),
            sorted(["762B", "964C", "11331"]))

    def test_the_trailer_comes_off_the_time_point_only(self):
        """`500gen` is flask 500; the ALE and the isolate keep every character."""
        self._import_named(["Ara+3_500gen_763A.gd"])

        self.assertEqual(Population.objects.get().name, "Ara+3")
        self.assertEqual({500}, set(Sample.objects.values_list('time_point', flat=True)))
        self.assertEqual(Sample.objects.get().name, "763A")

    def test_two_lineages_differing_only_in_sign_stay_apart(self):
        """The case that makes these columns text: `Ara-1` and `Ara+1` both end in 1."""
        self._import_named(["Ara-1_500gen_762B.gd", "Ara+1_500gen_763A.gd"])

        self.assertEqual(
            sorted(Population.objects.values_list("name", flat=True)), ["Ara+1", "Ara-1"])
        self.assertEqual(2, Sample.objects.values("population", "time_point").distinct().count(),
                         "one time point 500 per ALE")

    def test_two_clones_from_one_flask_are_two_samples(self):
        """`763A` and `763B` differ only in the trailer, which is why it is not stripped."""
        self._import_named(["Ara-1_500gen_763A.gd", "Ara-1_500gen_763B.gd"])

        self.assertEqual(Population.objects.count(), 1)
        self.assertEqual(1, Sample.objects.values("population", "time_point").distinct().count())
        self.assertEqual(
            sorted(Sample.objects.values_list("name", flat=True)),
            ["763A", "763B"])

    def test_a_read_name_displays_as_itself(self):
        """`label` prefers the sample's description, so the label survives."""
        self._import_named(["Ara-1_500gen_762B.gd"])

        reseq = Sample.objects.get()
        self.assertEqual(reseq.label, "Ara-1_500gen_762B")

    def test_an_afir_name_still_displays_as_its_coordinate(self):
        """It says exactly what the coordinate says, so storing it as the description
        would relabel every table column with a filename."""
        self._import_named(["3-30000-1-1.gd"])

        reseq = Sample.objects.get()
        self.assertEqual(reseq.label, "3 / 30000 / 1-1")

    def test_underscore_triple_reimport_is_idempotent(self):
        self._import_named(self.TRIPLE_NAMES)
        self._import_named(self.TRIPLE_NAMES)

        self.assertEqual(Sample.objects.count(), len(self.TRIPLE_NAMES))
        self.assertEqual(MutationCall.objects.count(),
                         Mutation.objects.count() * len(self.TRIPLE_NAMES))

    def test_a_name_of_neither_shape_is_still_auto_numbered(self):
        summary = self._import_named(self.UNPARSEABLE_NAMES)
        self.assertIsNone(summary["files"][0]["error"])

        # One sample per file, each its own label, all under ALE 1 / flask 1.
        self.assertEqual(Sample.objects.count(), len(self.UNPARSEABLE_NAMES))
        self.assertEqual(Population.objects.count(), 1)
        self.assertEqual(Population.objects.get().name, "1")
        self.assertEqual(1, Sample.objects.values("population", "time_point").distinct().count())
        self.assertEqual({1}, set(Sample.objects.values_list('time_point', flat=True)))
        self.assertEqual(
            sorted(Sample.objects.values_list("name", flat=True)),
            ["1", "2"])

    def test_auto_numbering_counts_past_nine(self):
        """`Max()` over a text column answers "9" for a flask already holding 1 to 10."""
        self._import_named(["sample%d.gd" % index for index in range(1, 12)])

        numbers = sorted(int(value) for value
                         in Sample.objects.values_list("name",
                                                                      flat=True))
        self.assertEqual(numbers, list(range(1, 12)))

    def test_auto_numbering_reimport_is_idempotent(self):
        self._import_named(self.UNPARSEABLE_NAMES)
        self._import_named(self.UNPARSEABLE_NAMES)

        # Auto-numbering must reuse the existing chain, not allocate a second sample.
        self.assertEqual(Sample.objects.count(), len(self.UNPARSEABLE_NAMES))

    def test_afir_filename_still_uses_filename_numbering(self):
        """The strict parser must not regress names that genuinely are A-F-I-R."""
        self._import_named(["3-30000-1-1.gd"])

        self.assertEqual(Population.objects.get().name, "3")
        self.assertEqual({30000}, set(Sample.objects.values_list('time_point', flat=True)))
        self.assertEqual(Sample.objects.get().name, "1-1")

    def test_summary_reports_the_real_experiment_pk(self):
        """The post-import "View mutations" link is built from this id."""
        summary = self._import_named(self.TRIPLE_NAMES[:1])

        experiment = Experiment.objects.get()
        self.assertEqual(summary["experiment_id"], experiment.id)
        self.assertEqual(summary["experiment"], experiment.name)

    # --- the pages the post-import link lands on ---------------------------------------

    def test_imported_experiment_pages_render(self):
        """/stats and /mutations must render for a gd-imported experiment.

        Regression guard for the `ale.common` NameError that turned /mutations into a 500
        page for every experiment. This also used to guard a NULL `location` rendering as
        the literal string "None" in an href; there are no report links left to get that
        wrong."""
        summary = self._import_named(self.TRIPLE_NAMES)
        experiment_id = summary["experiment_id"]
        self.client.force_login(self.user)

        stats = self.client.get("/stats/", {"experiment_id": experiment_id})
        self.assertEqual(stats.status_code, 200)
        stats_html = stats.content.decode("utf-8")
        # Every sample is listed, as plain text rather than a dead report link.
        for name in self.TRIPLE_NAMES:
            self.assertIn(name[:-3], stats_html)

        # The per-sample table, not the cross-sample one: Compare is the aledb-compare
        # plugin's now, and core's suite cannot reach a plugin.
        mutations = self.client.get("/mutations/breseq", {"experiment_id": experiment_id})
        self.assertEqual(mutations.status_code, 200)
        mutations_html = mutations.content.decode("utf-8")
        self.assertNotIn("Page not available", mutations_html)
        self.assertNotIn("name 'ale' is not defined", mutations_html)



class PolymorphismModeTestCase(TestCase):
    """breseq's `-p` is the only thing that makes an imported sample mixed.

    `#=COMMAND` is where a GenomeDiff records the command line that produced it, and `-p`
    (polymorphism mode) is what says the reads came from a whole population rather than a
    clone. This is the single place in the suite that turns a file into a polarity, so it is
    asserted in both directions -- the column it writes was `is_population` and is now its
    negation, and a test that only checked one side would pass under a re-inversion.
    """

    def setUp(self):
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _import_with_command(self, command, name="1-500-1-1.gd"):
        with open(CLEAN_GD, "rb") as handle:
            raw = handle.read().decode()
        lines = raw.splitlines()
        # After #=GENOME_DIFF, which must stay first.
        lines.insert(1, "#=COMMAND\t%s" % command)
        ensure_reference("polymorphism exp")
        gd_import.import_gd_files(
            [SimpleUploadedFile(name, "\n".join(lines).encode())],
            project_name="gd project", experiment_name="polymorphism exp",
            person="tester")
        return Sample.objects.get(source_name=name[:-3])

    def test_a_polymorphism_run_imports_as_mixed(self):
        sample = self._import_with_command("breseq -p -r synthetic.gbk -o s r.fastq")
        self.assertTrue(sample.is_mixed)
        self.assertFalse(sample.is_clonal)

    def test_a_consensus_run_imports_as_clonal(self):
        sample = self._import_with_command("breseq -r synthetic.gbk -o s r.fastq")
        self.assertTrue(sample.is_clonal)
        self.assertFalse(sample.is_mixed)

    def test_a_file_with_no_command_line_at_all_is_clonal(self):
        """The fixtures have no `#=COMMAND`, and neither do plenty of real files. A sample
        is a clone unless something says otherwise."""
        ensure_reference("polymorphism exp")
        gd_import.import_gd_files(
            [_uploaded_as(CLEAN_GD, "1-500-1-1.gd")], project_name="gd project",
            experiment_name="polymorphism exp", person="tester")
        self.assertTrue(Sample.objects.get().is_clonal)

    def test_the_flag_is_matched_as_a_word_not_a_substring(self):
        """` -p` with the leading space, so `--polymorphism-frequency-cutoff` and an output
        directory called `-prefix` do not read as polymorphism mode. This is the existing
        rule, pinned because the line that implements it was inverted in this commit."""
        sample = self._import_with_command(
            "breseq -r synthetic.gbk -o sample-p r.fastq")
        self.assertTrue(sample.is_clonal)


class UncalledRegionTestCase(TestCase):
    """An MC evidence record becomes an `UncalledRegion` row, with integer bounds.

    `start` and `end` were `CharField`s, so nothing here was ever exercised against a real
    integer column -- and none of the four example datasets carries an MC record, so loading
    them proves nothing about this path either. The rows matter beyond the count on /stats:
    aledb-phylogeny reads them to mark a site *ambiguous* rather than ancestral.
    """

    def setUp(self):
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _import_with_evidence(self, *lines):
        with open(CLEAN_GD, "rb") as handle:
            raw = handle.read().decode()
        ensure_reference("uncalled exp")
        gd_import.import_gd_files(
            [SimpleUploadedFile("1-500-1-1.gd",
                                "\n".join(raw.splitlines() + list(lines)).encode())],
            project_name="gd project", experiment_name="uncalled exp", person="tester")
        return Sample.objects.get()

    def test_an_mc_record_is_stored_with_integer_bounds(self):
        sample = self._import_with_evidence("MC\t900\t.\tREL606\t100\t200\t0\t0")

        region = UncalledRegion.objects.get(sample=sample)
        self.assertEqual("REL606", region.seq_id)
        self.assertEqual((100, 200), (region.start, region.end))
        self.assertIsInstance(region.start, int)
        self.assertIsInstance(region.end, int)

    def test_the_bounds_compare_as_numbers(self):
        """The reason the columns are integers. As text `"1000"` sorts before `"9"`, so a
        region 9..1000 would have looked like it covered nothing between them."""
        self._import_with_evidence("MC\t900\t.\tREL606\t9\t1000\t0\t0")

        self.assertTrue(
            UncalledRegion.objects.filter(start__lte=500, end__gte=500).exists())

    def test_a_reimport_replaces_the_regions_rather_than_adding(self):
        self._import_with_evidence("MC\t900\t.\tREL606\t100\t200\t0\t0")
        self._import_with_evidence("MC\t900\t.\tREL606\t300\t400\t0\t0")

        self.assertEqual([(300, 400)],
                         list(UncalledRegion.objects.values_list("start", "end")))

    def test_an_unreadable_bound_is_skipped_and_the_import_survives(self):
        """genomediff leaves a field it cannot parse as the raw string, and an integer
        column would raise on it. Losing one region is the same bargain the coverage
        derivation makes; losing the sample's mutations would not be."""
        with self.assertLogs("aledb_import.gd_import", level="WARNING"):
            sample = self._import_with_evidence("MC\t900\t.\tREL606\tnot-a-number\t200\t0\t0")

        self.assertEqual(0, UncalledRegion.objects.count())
        self.assertTrue(MutationCall.objects.filter(sample=sample).exists(),
                        "the sample still has its mutations")


class SeqIdMustMatchTheReferenceTestCase(TestCase):
    """A .gd may only go into an experiment whose reference has the same contigs.

    Nothing checked this, which is how an experiment came to hold mutations on
    REL606 while its own stored reference was REL606.6. The annotator skips a
    seq_id it cannot resolve, so it surfaced as annotation that never appeared
    rather than as a refused import.

    Matched exactly. breseq trims the version suffix when resolving a GD seq_id;
    ALEdb holds many experiments side by side and two may be against different
    versions of one accession, so conflating them would annotate against the wrong
    genome.
    """

    def setUp(self):
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _experiment_with_reference(self, seq_id):
        context = gd_import._prepare_experiment("p", "e", "tester", False)
        sequences = [(seq_id, breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            context["experiment"], breseq_fixture.gff3_text(sequences), sequences)
        return context["experiment"]

    def _import(self):
        return gd_import.import_gd_files(
            [_uploaded(CLEAN_GD)], project_name="p", experiment_name="e", person="tester")

    def test_matching_seq_ids_import(self):
        self._experiment_with_reference("REL606")
        self.assertGreater(self._import()["total_mutations"], 0)

    def test_a_different_contig_is_refused(self):
        self._experiment_with_reference("SOMETHING_ELSE")
        summary = self._import()

        self.assertEqual(0, summary["total_mutations"])
        self.assertEqual(0, Mutation.objects.count())
        self.assertIn("REL606", summary["files"][0]["error"])
        self.assertIn("SOMETHING_ELSE", summary["files"][0]["error"])

    def test_a_version_suffix_is_not_trimmed_away(self):
        """The case that started this: REL606.6 and REL606 are different names, and
        ALEdb will not quietly treat them as one reference."""
        self._experiment_with_reference("REL606.6")
        summary = self._import()

        self.assertEqual(0, summary["total_mutations"])
        self.assertIn("REL606", summary["files"][0]["error"])


class NewParserBehaviourTestCase(GdImportTestCase):
    """What changed when the genomediff pin moved from 82cfd6d to 43a0f72.

    None of this is exercised by the tests above, which is the point: the suite passed
    unchanged across the bump, so the only way these behaviours get pinned is deliberately.
    """

    HEADER = "#=GENOME_DIFF\t1.0\n#=REFSEQ\tREL606\n"

    def _upload(self, body, name="1-1-1-1.gd"):
        return SimpleUploadedFile(name, (self.HEADER + body).encode("utf-8"))

    def _import_text(self, body, experiment="gd exp"):
        self._ensure_reference(experiment)
        return gd_import.import_gd_files(
            [self._upload(body)], project_name="gd project",
            experiment_name=experiment, person="tester")

    # --- a bad line no longer costs the file ------------------------------------------------

    def test_a_truncated_line_is_reported_but_the_file_still_imports(self):
        """It used to raise out of the parser and fail the whole file.

        `strict=True` would restore that, and is deliberately not used: it also raises on the
        field-guard violations breseq's own output contains, so it would start refusing files
        that import cleanly today.
        """
        summary = self._import_text(
            "SNP\t1\t.\tREL606\t100\tA\n"
            "DEL\t2\t.\tREL606\n")            # missing position and size

        row = summary["files"][0]
        self.assertIsNone(row["error"], "a bad line does not fail the file")
        self.assertEqual(1, row["mutations"], "the good mutation still landed")
        self.assertTrue(row["warnings"], "and the bad one is reported")
        self.assertIn("missing", row["warnings"][0])

    def test_a_clean_file_carries_no_warnings(self):
        summary = self._import_text("SNP\t1\t.\tREL606\t100\tA\n")
        self.assertEqual([], summary["files"][0]["warnings"])

    def test_a_failed_file_still_has_the_warnings_key(self):
        """The summary shape is one contract; a consumer should not have to test for it."""
        summary = gd_import.import_gd_files(
            [SimpleUploadedFile("bad.gd", b"not a genome diff at all")],
            project_name="gd project", experiment_name="gd exp", person="tester",
            require_reference=False)
        self.assertIn("warnings", summary["files"][0])

    # --- an entry with no id -----------------------------------------------------------------

    def test_an_entry_whose_id_is_a_dot_imports(self):
        """breseq writes '.' for an entry with no id. That used to fail the whole file --
        18 of breseq's own 306 test files could not be read at all."""
        summary = self._import_text("SNP\t.\t.\tREL606\t100\tA\n")

        self.assertEqual(1, summary["total_mutations"])
        self.assertIsNone(Mutation.objects.get(position=100).gd_data["id"])

    def test_it_writes_the_dot_back_rather_than_the_string_None(self):
        """The old version emitted `None` in the id column, producing a file breseq cannot
        read. Fixed by the bump, and worth pinning so it cannot come back."""
        self._import_text("SNP\t.\t.\tREL606\t100\tA\n")
        line = Mutation.objects.get(position=100).to_gd_line().split("\t")

        self.assertEqual(".", line[1])
        self.assertNotIn("None", Mutation.objects.get(position=100).to_gd_line())

    # --- numbers ------------------------------------------------------------------------------

    def test_scientific_notation_survives_as_a_value(self):
        """The notation itself does not survive the database -- JSON has one number type --
        but the value must."""
        self._import_text("SNP\t1\t.\tREL606\t100\tA\tfrequency=8.39314286e-01\n")

        stored = Mutation.objects.get(position=100).gd_data["frequency"]
        self.assertAlmostEqual(0.839314286, stored)
        self.assertIsInstance(stored, float)
        self.assertEqual(
            0.8393, float(MutationCall.objects.get().frequency),
            "and it reaches the call's Decimal column")

    def test_the_same_mutation_spelled_two_ways_is_one_row(self):
        """The sharpest consequence of the bump, and a silent one.

        Parsed numbers can come back as `PreservedInt`, whose `str()` is the *source text*.
        `synthesize_sequence_change` formats those, and its output is one of the seven fields
        `get_or_create` dedups on -- so `size=0042` and `size=42` would fork one mutation into
        two rows depending only on how each file happened to write the number.
        """
        self._import_text("DEL\t1\t.\tREL606\t100\t42\n", experiment="gd exp")
        self._import_text("DEL\t1\t.\tREL606\t100\t0042\n", experiment="gd exp")

        rows = Mutation.objects.filter(position=100, mutation_type="DEL")
        self.assertEqual(1, rows.count(), "one mutation, however the size was written")
        self.assertEqual("del 42 bp", rows.get().sequence_change)
