"""The MutInt archive: an experiment written as one folder, and read back as one.

The round trip is the test that matters -- everything the archive claims to carry has to
come back on a fresh experiment -- and the rest pin the pieces it is built from.
"""

import io
import json
import os
import shutil
import tempfile
import zipfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_bibliome.models import Publication
from mutint_common.import_registry import run_import
from mutint_experiment.models import Experiment, Population, Project
from mutint_experiment.permissions import set_primary_owner
from mutint_import import archive, gd_import, vcf, vcf_import
from mutint_import.breseq_folder import import_samples_into
from mutint_import.gd_import import prepare_experiment_by_id
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Mutation, MutationCall, Sample

SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A)]


class ArchiveTestCase(TestCase):
    """An experiment with two breseq samples, one VCF sample, and every detail the manifest
    carries: notes, species and strain, flags, a description, curation, a publication and an
    ancestor."""

    def setUp(self):
        self.user = User.objects.create(username="tester", email="t@e.com", is_active=True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.user)
        set_primary_owner(self.project, self.user)
        self.experiment = self._experiment("Source exp")
        self._populate(self.experiment)

    def _experiment(self, name):
        from mutint_experiment.views import _create_experiment
        return _create_experiment(self.project, name, self.user)

    def _drop(self):
        drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, drop, True)
        return drop

    def _populate(self, experiment):
        drop = self._drop()
        breseq_fixture.write_sample(drop, "1-500-1-1", sequences=SEQUENCES)
        breseq_fixture.write_sample(
            drop, "1-1000-1-1", sequences=SEQUENCES,
            gd_text=("#=GENOME_DIFF\t1.0\n#=REFSEQ\ttest_ref\n#=TITLE\tlater\n"
                     "SNP\t1\t.\ttest_ref\t100\tA\tfrequency=0.42\n"
                     "INS\t2\t.\ttest_ref\t130\tGG\n"))
        import_samples_into(experiment, drop, user=self.user)

        context = prepare_experiment_by_id(experiment.id)
        text = "\n".join([
            "##fileformat=VCFv4.2",
            "##contig=<ID=test_ref,length=%d>" % len(breseq_fixture.SEQUENCE_A),
            "#" + "\t".join(list(vcf.FIXED_COLUMNS) + ["FORMAT", "Ara-2_500gen_c9"]),
            "test_ref\t100\t.\t%s\tA\t50.5\tPASS\tDP=30;AF=0.5\tGT:AD\t1:2,8"
            % breseq_fixture.SEQUENCE_A[99],
        ]) + "\n"
        document = vcf.read(io.StringIO(text), "calls.vcf")
        for name in vcf_import.sample_names_for(document, "calls.vcf"):
            vcf_import.import_sample(document, name, context, experiment, filename="calls.vcf")

        experiment.notes = "Three samples, one made up."
        experiment.save()
        population = Population.objects.get(experiment=experiment, name="1")
        population.description = "the first lineage"
        population.species = "Escherichia coli"
        population.strain = "REL606"
        population.save()
        first = Sample.objects.get(population=population, time_point=500)
        first.is_hypermutator = True
        first.description = "the starting clone"
        first.set_record(Sample.COMPONENT, Sample.CURATION,
                         {"medium_description": "DM25", "person": "somebody"})
        # The fixture folder has no summary.json; the group is what a real one records.
        first.set_record(Sample.COMPONENT, Sample.BRESEQ,
                         {"version": "0.39.0", "reads": 1000, "mean_coverage": 50.0})
        first.save()
        second = Sample.objects.get(population=population, time_point=1000)
        second.is_contaminated = True
        second.is_clonal = False
        second.save()
        Publication.objects.create(experiment=experiment,
                                   url="https://doi.org/10.1000/xyz", title="A paper")
        experiment.set_ancestor(first, self.user)

    def _archive(self, experiment=None):
        return archive.archive_bytes(experiment or self.experiment)

    def _unzip(self, payload):
        folder = self._drop()
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            zipped.extractall(folder)
        return folder

    def _manifest_from(self, payload):
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            name = next(n for n in zipped.namelist() if n.endswith(archive.MANIFEST))
            return json.loads(zipped.read(name).decode("utf-8")), zipped.namelist()

    # --- writing -------------------------------------------------------------------------

    def test_the_manifest_describes_the_experiment(self):
        manifest, names = self._manifest_from(self._archive())

        self.assertEqual(archive.FORMAT, manifest["format"])
        self.assertEqual("Source exp", manifest["experiment"]["name"])
        self.assertEqual("Three samples, one made up.", manifest["experiment"]["notes"])
        self.assertEqual([{"name": "1", "description": "the first lineage",
                           "species": "Escherichia coli", "strain": "REL606"},
                          {"name": "Ara-2", "description": "", "species": "", "strain": ""}],
                         manifest["populations"])
        self.assertEqual([{"url": "https://doi.org/10.1000/xyz", "title": "A paper"}],
                         manifest["publications"])

        by_source = {entry["source_name"]: entry for entry in manifest["samples"]}
        self.assertEqual({"1-500-1-1", "1-1000-1-1", "Ara-2_500gen_c9"}, set(by_source))
        first = by_source["1-500-1-1"]
        self.assertEqual("samples/1-500-1-1.gd", first["file"])
        self.assertEqual(("1", 500, "1-1"),
                         (first["population"], first["time_point"], first["name"]))
        self.assertTrue(first["is_hypermutator"])
        self.assertTrue(first["is_clonal"])
        self.assertEqual("the starting clone", first["description"])
        self.assertEqual({"medium_description": "DM25", "person": "somebody"},
                         first["supplemental_data"]["curation"])
        self.assertIn("breseq", first["supplemental_data"])
        self.assertEqual(first["file"], manifest["experiment"]["ancestor"])
        self.assertFalse(by_source["1-1000-1-1"]["is_clonal"])
        self.assertTrue(by_source["1-1000-1-1"]["is_contaminated"])
        self.assertEqual("samples/Ara-2_500gen_c9.vcf", by_source["Ara-2_500gen_c9"]["file"])
        self.assertEqual("vcf", by_source["Ara-2_500gen_c9"]["format"])

        root = "Source_exp/"
        self.assertIn(root + "metadata.csv", names)
        self.assertIn(root + "reference/reference.gff3", names)
        self.assertIn(root + "reference/reference.fasta", names)
        self.assertIn(root + "samples/1-500-1-1.gd", names)
        self.assertIn(root + "samples/Ara-2_500gen_c9.vcf", names)

    def test_the_reference_travels_byte_for_byte(self):
        from mutint_common import store
        folder = self._unzip(self._archive())
        for filename in (store.REFERENCE_GFF3, store.REFERENCE_FASTA):
            with open(store.experiment_reference_path(self.experiment.id, filename), "rb") as h:
                stored = h.read()
            with open(os.path.join(folder, "Source_exp", "reference", filename), "rb") as h:
                self.assertEqual(stored, h.read(), filename)

    def test_the_metadata_csv_places_every_sample(self):
        manifest, _names = self._manifest_from(self._archive())
        text = archive.metadata_csv(manifest)
        lines = text.splitlines()
        self.assertEqual("sample,population,time_point,sample_type,data", lines[0])
        self.assertIn("1-1,1,500,clone,1-500-1-1.gd", lines)
        self.assertIn("1-1,1,1000,population,1-1000-1-1.gd", lines)
        self.assertIn("c9,Ara-2,500,clone,Ara-2_500gen_c9.vcf", lines)

    def test_two_samples_with_one_source_name_get_two_files(self):
        population = Population.objects.get(experiment=self.experiment, name="1")
        Sample.objects.create(population=population, time_point=2000, name="1-1",
                              source_name="1-500-1-1")
        manifest, _names = self._manifest_from(self._archive())
        files = sorted(e["file"] for e in manifest["samples"] if e["source_name"] == "1-500-1-1")
        self.assertEqual(["samples/1-500-1-1-2.gd", "samples/1-500-1-1.gd"], files)

    def test_an_experiment_with_no_reference_has_no_archive(self):
        bare = self._experiment("bare")
        with self.assertRaises(archive.ArchiveError):
            archive.archive_bytes(bare)

    # --- the download ----------------------------------------------------------------------

    def test_the_download_is_a_zip_for_a_reader(self):
        self.client.force_login(self.user)
        response = self.client.get("/import/archive/%d/export" % self.experiment.id)
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/zip", response["Content-Type"])
        self.assertIn('filename="Source_exp.zip"', response["Content-Disposition"])
        manifest, _names = self._manifest_from(response.content)
        self.assertEqual("Source exp", manifest["experiment"]["name"])

    def test_the_download_refuses_a_stranger_and_a_missing_reference(self):
        stranger = User.objects.create(username="s", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(404, self.client.get(
            "/import/archive/%d/export" % self.experiment.id).status_code)

        self.client.force_login(self.user)
        bare = self._experiment("bare")
        self.assertEqual(404, self.client.get("/import/archive/%d/export" % bare.id).status_code)

    # --- reading -------------------------------------------------------------------------

    def _assert_rehydrated(self, target, summary):
        errors = [(r["file"], r["error"]) for r in summary["files"] if r.get("error")]
        self.assertEqual([], errors)
        target.refresh_from_db()
        self.assertEqual("Source exp", target.name)
        self.assertEqual("Three samples, one made up.", target.notes)

        population = Population.objects.get(experiment=target, name="1")
        self.assertEqual(("the first lineage", "Escherichia coli", "REL606"),
                         (population.description, population.species, population.strain))

        samples = {s.source_name: s for s in
                   Sample.objects.filter(population__experiment=target)}
        self.assertEqual({"1-500-1-1", "1-1000-1-1", "Ara-2_500gen_c9"}, set(samples))
        first = samples["1-500-1-1"]
        self.assertEqual(("1", 500, "1-1"), (first.population.name, first.time_point, first.name))
        self.assertTrue(first.is_hypermutator)
        self.assertTrue(first.is_clonal)
        self.assertEqual("the starting clone", first.description)
        self.assertEqual({"medium_description": "DM25", "person": "somebody"}, first.curation)
        self.assertEqual("0.39.0", first.breseq["version"])
        second = samples["1-1000-1-1"]
        self.assertFalse(second.is_clonal)
        self.assertTrue(second.is_contaminated)
        self.assertEqual(1000, second.time_point)
        third = samples["Ara-2_500gen_c9"]
        self.assertEqual(("Ara-2", 500, "c9"),
                         (third.population.name, third.time_point, third.name))
        self.assertTrue(third.record("vcf"))

        self.assertEqual(first.pk, target.ancestor_id)
        self.assertEqual([("https://doi.org/10.1000/xyz", "A paper")],
                         list(Publication.objects.filter(experiment=target)
                              .values_list("url", "title")))

        def calls(experiment):
            return sorted(
                (c.sample.source_name, c.mutation.mutation_type, c.mutation.start_position,
                 c.mutation.sequence_change, round(c.frequency, 6))
                for c in MutationCall.objects.filter(sample__population__experiment=experiment)
                .select_related("mutation", "sample"))
        self.assertEqual(calls(self.experiment), calls(target))
        self.assertEqual(Mutation.objects.filter(experiment=self.experiment).count(),
                         Mutation.objects.filter(experiment=target).count())

    def test_round_trip_into_a_fresh_experiment(self):
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        summary = run_import(target, folder, self.user)
        self._assert_rehydrated(target, summary)
        self.assertEqual(archive.MANIFEST,
                         next(r for r in summary["files"] if r["file"] == "1-500-1-1.gd")
                         ["named_by"])

    def test_round_trip_with_the_type_named(self):
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        summary = run_import(target, folder, self.user, import_type="mutint_archive")
        self._assert_rehydrated(target, summary)

    def test_round_trip_from_the_zip_itself(self):
        drop = self._drop()
        with open(os.path.join(drop, "Source_exp.zip"), "wb") as handle:
            handle.write(self._archive())
        target = self._experiment("empty")
        summary = run_import(target, drop, self.user)
        self._assert_rehydrated(target, summary)

    def test_importing_twice_adds_nothing(self):
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        run_import(target, folder, self.user)
        summary = run_import(target, folder, self.user)
        self._assert_rehydrated(target, summary)
        self.assertEqual(3, Sample.objects.filter(population__experiment=target).count())

    def test_the_archives_own_metadata_csv_is_not_the_drops(self):
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        summary = run_import(target, folder, self.user)
        self.assertIsNone(summary.get("metadata"))

    def test_a_locked_experiment_refuses_it(self):
        from mutint_experiment.permissions import ExperimentLocked
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        target.lock(self.user)
        with self.assertRaises(ExperimentLocked):
            run_import(target, folder, self.user)

    def test_a_different_reference_refuses_every_row(self):
        from mutint_import import reference_store
        folder = self._unzip(self._archive())
        target = self._experiment("other genome")
        other = [("test_ref", breseq_fixture.SEQUENCE_B)]
        reference_store.establish_or_check(target, breseq_fixture.gff3_text(other), other)

        summary = run_import(target, folder, self.user)
        self.assertEqual(4, len(summary["files"]))
        self.assertTrue(all(r["error"] for r in summary["files"]))
        self.assertIn("not this experiment's", summary["files"][0]["error"])
        self.assertEqual(0, Sample.objects.filter(population__experiment=target).count())
        target.refresh_from_db()
        self.assertEqual("other genome", target.name)

    def test_a_broken_manifest_reports_every_row(self):
        folder = self._unzip(self._archive())
        with open(os.path.join(folder, "Source_exp", archive.MANIFEST), "w") as handle:
            handle.write("{not json")
        target = self._experiment("empty")
        summary = run_import(target, folder, self.user)
        self.assertEqual(4, len(summary["files"]))
        self.assertTrue(all("could not be read" in r["error"] for r in summary["files"]))

    def test_a_newer_manifest_is_refused_with_its_version(self):
        folder = self._unzip(self._archive())
        path = os.path.join(folder, "Source_exp", archive.MANIFEST)
        with open(path) as handle:
            manifest = json.load(handle)
        manifest["version"] = archive.VERSION + 1
        with open(path, "w") as handle:
            json.dump(manifest, handle)
        summary = run_import(self._experiment("empty"), folder, self.user)
        self.assertIn("newer MutInt", summary["files"][0]["error"])

    def test_the_manifest_overwrites_what_the_target_had(self):
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        target.notes = "old notes"
        target.save()
        Publication.objects.create(experiment=target, url="https://x", title="old")
        run_import(target, folder, self.user)
        target.refresh_from_db()
        self.assertEqual("Three samples, one made up.", target.notes)
        self.assertEqual(["A paper"], list(Publication.objects.filter(experiment=target)
                                           .values_list("title", flat=True)))

    def test_a_manifest_saying_no_ancestor_clears_one(self):
        self.experiment.clear_ancestor()
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        run_import(target, folder, self.user)
        target.refresh_from_db()
        self.assertIsNone(target.ancestor_id)

    def test_the_cli_imports_the_folder(self):
        from mutint_import.experiments import import_paths
        folder = self._unzip(self._archive())
        target = self._experiment("empty")
        summaries = import_paths([folder], target, self.user)
        self._assert_rehydrated(target, summaries[0])

    def test_a_zip_reaching_outside_itself_is_refused(self):
        drop = self._drop()
        path = os.path.join(drop, "evil.zip")
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr("x/mutint.json", "{}")
            zipped.writestr("../escape.txt", "no")
        summary = run_import(self._experiment("empty"), drop, self.user)
        self.assertTrue(summary["files"])
        self.assertTrue(all("outside the archive" in (r["error"] or "")
                            for r in summary["files"]))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(drop), "escape.txt")))


class ArchiveDetectionTestCase(TestCase):
    """What the handler claims, and what it leaves to the others."""

    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)

    def _write(self, relative, text="x"):
        path = os.path.join(self.drop, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        return relative

    def test_everything_under_the_archive_is_claimed_and_nothing_beside_it(self):
        from mutint_common.import_registry import claim, get_import_handler, walk_files
        self._write("exp/mutint.json", "{}")
        self._write("exp/metadata.csv")
        self._write("exp/reference/reference.gff3")
        self._write("exp/reference/reference.fasta")
        self._write("exp/samples/a.gd")
        self._write("exp/samples/b.vcf")
        self._write("loose.gd")
        self._write("genome.gbk")
        paths = walk_files(self.drop)

        claimed = set(claim(get_import_handler("mutint_archive"), self.drop, paths))
        self.assertEqual({p for p in paths if p.startswith("exp/")}, claimed)

    def test_a_zip_is_claimed_only_when_it_holds_a_manifest(self):
        from mutint_common.import_registry import claim, get_import_handler
        with zipfile.ZipFile(os.path.join(self.drop, "yes.zip"), "w") as zipped:
            zipped.writestr("exp/mutint.json", "{}")
        with zipfile.ZipFile(os.path.join(self.drop, "no.zip"), "w") as zipped:
            zipped.writestr("readme.txt", "hi")
        claimed = claim(get_import_handler("mutint_archive"), self.drop, ["yes.zip", "no.zip"])
        self.assertEqual(["yes.zip"], claimed)

    def test_it_runs_first_and_is_offered_with_or_without_a_reference(self):
        from mutint_common.import_registry import get_import_handlers, get_import_types_for
        handlers = get_import_handlers()
        self.assertEqual("mutint_archive", handlers[0]["name"])
        for has_reference in (True, False):
            self.assertIn("mutint_archive",
                          [t["name"] for t in get_import_types_for(has_reference)])

    def test_a_loose_gd_never_looks_like_an_archive(self):
        from mutint_common.import_registry import identify
        self.assertEqual("genomediff", identify("batch/1-1-1-1.gd")["name"])
        self.assertEqual("mutint_archive", identify("exp/mutint.json")["name"])

    def test_the_archives_csv_stays_with_the_archive(self):
        from mutint_import.metadata import split_paths
        mine, rest = split_paths(["metadata.csv", "exp/metadata.csv", "exp/mutint.json"])
        self.assertEqual(["metadata.csv"], mine)
        self.assertEqual(["exp/metadata.csv", "exp/mutint.json"], rest)
