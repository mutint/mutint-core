"""metadata.csv and header placement through the real import path."""

import io
import json
import os
import shutil
import tempfile
from contextlib import redirect_stdout

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from mutint_common.import_registry import run_import
from mutint_experiment.models import Population, Project
from mutint_import import breseq_folder, metadata
from mutint_import.gd_import import UNSPECIFIED_POPULATION
from mutint_import.tests import breseq_fixture
from mutint_import.tests.test_vcf_import import vcf_text
from mutint_sample.models import Sample

HEADER = "sample,population,time_point,data\n"
VCF_ROW = "test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"


def _write(root, relative, text):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


class _DropTestCase(TestCase):

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.user = User.objects.create(username="meta", email="m@e.com", is_active=True)
        self.project = Project.objects.create(name="P", user=self.user)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "E", self.user)

    def drop(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def _mixed_drop(self, csv_text=None, gd_text=None, gd_name="loose.gd"):
        """A breseq folder (which brings the reference), a bare .gd and a VCF."""
        root = self.drop()
        breseq_fixture.write_sample(root, "ZDB16")
        _write(root, gd_name, gd_text or breseq_fixture.GD_TEXT)
        _write(root, "calls.vcf", vcf_text([VCF_ROW], samples=("colA",)))
        if csv_text is not None:
            _write(root, "metadata.csv", csv_text)
        return root

    def _run(self, root):
        return run_import(self.experiment, root, self.user)

    def _sample(self, source_name):
        return Sample.objects.get(source_name=source_name,
                                  population__experiment=self.experiment)

    def _coordinate(self, sample):
        return (sample.population.name, sample.time_point, sample.name)


class MetadataImportTestCase(_DropTestCase):

    # --- the CSV ----------------------------------------------------------------------

    def test_a_csv_places_every_kind_of_input(self):
        summary = self._run(self._mixed_drop(
            HEADER + "clone16,Ara-3,30000,ZDB16\n763A,Ara-2,500,loose\ncolumn,wt,7,calls.vcf\n"))
        self.assertEqual([], [f for f in summary["files"] if f.get("error")], summary)
        self.assertEqual(("Ara-3", 30000, "clone16"), self._coordinate(self._sample("ZDB16")))
        self.assertEqual(("Ara-2", 500, "763A"), self._coordinate(self._sample("loose")))
        self.assertEqual(("wt", 7, "column"), self._coordinate(self._sample("colA")))
        for sample in Sample.objects.all():
            self.assertEqual("", sample.description)
        self.assertEqual({metadata.BY_CSV},
                         {f["named_by"] for f in summary["files"] if "named_by" in f})
        report = summary["metadata"]
        self.assertEqual(3, report["rows"])
        self.assertEqual([], report["unmatched_rows"])
        self.assertEqual([], report["unnamed_inputs"])
        # The CSV itself is neither a unit nor an error row.
        self.assertNotIn("metadata.csv", [f["file"] for f in summary["files"]])

    def test_unmatched_rows_and_unnamed_inputs_are_reported_not_fatal(self):
        summary = self._run(self._mixed_drop(
            HEADER + "clone16,Ara-3,30000,ZDB16\nghost,Ara-9,1,nothing.gd\n"))
        self.assertEqual([], [f for f in summary["files"] if f.get("error")])
        report = summary["metadata"]
        self.assertEqual(1, len(report["unmatched_rows"]))
        self.assertIn("nothing.gd", report["unmatched_rows"][0])
        self.assertEqual(["colA", "loose.gd"], sorted(report["unnamed_inputs"]))
        # The unnamed .gd fell back to its filename: no shape, so auto-numbered.
        self.assertEqual(UNSPECIFIED_POPULATION, self._sample("loose").population.name)

    def test_a_vcf_column_named_by_the_file_counts_as_unnamed_by_its_column(self):
        summary = self._run(self._mixed_drop(HEADER + "column,wt,7,calls\n"))
        self.assertEqual(("wt", 7, "column"), self._coordinate(self._sample("colA")))
        self.assertNotIn("colA", summary["metadata"]["unnamed_inputs"])

    def test_two_rows_naming_one_input_fail_that_input_only(self):
        summary = self._run(self._mixed_drop(
            HEADER + "a,p,1,loose.gd\nb,p,2,loose\nclone16,Ara-3,30000,ZDB16\n"))
        errors = {f["file"]: f["error"] for f in summary["files"] if f.get("error")}
        self.assertEqual(["loose.gd"], list(errors))
        self.assertIn("rows 2 and 3", errors["loose.gd"])
        self.assertEqual(("Ara-3", 30000, "clone16"), self._coordinate(self._sample("ZDB16")))

    def test_a_same_csv_reimport_changes_nothing(self):
        csv = HEADER + "clone16,Ara-3,30000,ZDB16\n763A,Ara-2,500,loose\n"
        self._run(self._mixed_drop(csv))
        before = Sample.objects.count()
        self._run(self._mixed_drop(csv))
        self.assertEqual(before, Sample.objects.count())
        self.assertEqual("", self._sample("loose").description)

    def test_a_sample_imported_without_the_csv_is_moved_not_duplicated(self):
        self._run(self._mixed_drop(None))
        loose = self._sample("loose")
        self.assertEqual(UNSPECIFIED_POPULATION, loose.population.name)
        self._run(self._mixed_drop(HEADER + "763A,Ara-2,500,loose\n"))
        moved = self._sample("loose")
        self.assertEqual(loose.pk, moved.pk)
        self.assertEqual(("Ara-2", 500, "763A"), self._coordinate(moved))
        self.assertEqual("", moved.description)

    def test_moving_the_last_sample_out_of_a_population_prunes_it(self):
        root = self.drop()
        breseq_fixture.write_sample(root, "ZDB16")
        self._run(root)
        self.assertTrue(Population.objects.filter(name=UNSPECIFIED_POPULATION).exists())
        _write(root, "metadata.csv", HEADER + "clone16,Ara-3,30000,ZDB16\n")
        self._run(root)
        self.assertFalse(Population.objects.filter(name=UNSPECIFIED_POPULATION).exists())

    def test_a_coordinate_another_sample_holds_is_noted_and_left(self):
        self._run(self._mixed_drop(HEADER + "one,p,1,ZDB16\ntwo,p,2,loose\n"))
        summary = self._run(self._mixed_drop(HEADER + "one,p,1,ZDB16\none,p,1,loose\n"))
        self.assertEqual(("p", 2, "two"), self._coordinate(self._sample("loose")))
        self.assertTrue(any("already held" in w for w in summary["metadata"]["warnings"]))

    def test_sample_type_sets_is_clonal_and_blank_keeps_the_gd_rule(self):
        typed = "sample,population,time_point,sample_type,data\n"
        self._run(self._mixed_drop(
            typed + "a,p,1,population,ZDB16\nb,p,2,clone,loose\nc,p,3,,calls\n"))
        self.assertFalse(self._sample("ZDB16").is_clonal)
        self.assertTrue(self._sample("loose").is_clonal)
        # No -p in the fixture's command line, so the rule says clonal.
        self.assertTrue(self._sample("colA").is_clonal)
        # And it moves an existing sample's flag on a later import.
        self._run(self._mixed_drop(typed + "a,p,1,clone,ZDB16\n"))
        self.assertTrue(self._sample("ZDB16").is_clonal)

    def test_a_gd_header_may_say_the_sample_type(self):
        gd = ("#=GENOME_DIFF\t1.0\n#=SAMPLE\tx\n#=SAMPLE_TYPE\tmixed\n#=REFSEQ\ttest_ref\n"
              + breseq_fixture.GD_TEXT.split("\n", 2)[2])
        self._run(self._mixed_drop(None, gd_text=gd))
        self.assertFalse(self._sample("loose").is_clonal)

    def test_a_blank_coordinate_is_an_unplaced_sample_with_the_given_name(self):
        self._run(self._mixed_drop(HEADER + "mystery,,,loose\n"))
        self.assertEqual((UNSPECIFIED_POPULATION, None, "mystery"),
                         self._coordinate(self._sample("loose")))

    def test_a_broken_csv_refuses_the_whole_drop(self):
        root = self._mixed_drop("sample,population,data\nx,p,ZDB16\n")
        with self.assertRaises(metadata.MetadataError):
            self._run(root)
        self.assertEqual(0, Sample.objects.count())

    def test_two_csvs_are_refused(self):
        root = self._mixed_drop(HEADER)
        _write(root, "sub/metadata.csv", HEADER)
        with self.assertRaises(metadata.MetadataError):
            self._run(root)

    # --- the file's own header --------------------------------------------------------

    def test_a_gd_header_places_the_sample(self):
        gd = "#=GENOME_DIFF\t1.0\n#=SAMPLE\t763A\n#=POPULATION\tAra-2\n#=GENERATION\t500\n" \
             "#=REFSEQ\ttest_ref\n" + breseq_fixture.GD_TEXT.split("\n", 2)[2]
        summary = self._run(self._mixed_drop(None, gd_text=gd))
        self.assertEqual([], [f for f in summary["files"] if f.get("error")], summary)
        self.assertEqual(("Ara-2", 500, "763A"), self._coordinate(self._sample("loose")))
        self.assertEqual(metadata.BY_HEADER,
                         [f for f in summary["files"] if f["file"] == "loose.gd"][0]["named_by"])

    def test_a_breseq_folder_header_places_it_too(self):
        root = self.drop()
        gd = "#=GENOME_DIFF\t1.0\n#=NAME\tclone16\n#=TREATMENT\tAra-3\n#=TIME\t30000\n" \
             "#=REFSEQ\ttest_ref\n" + breseq_fixture.GD_TEXT.split("\n", 2)[2]
        breseq_fixture.write_sample(root, "ZDB16", gd_text=gd)
        self._run(root)
        self.assertEqual(("Ara-3", 30000, "clone16"), self._coordinate(self._sample("ZDB16")))

    def test_a_header_overrides_a_parseable_filename_field_by_field(self):
        gd = "#=GENOME_DIFF\t1.0\n#=POPULATION\tAra-3\n#=REFSEQ\ttest_ref\n" \
             + breseq_fixture.GD_TEXT.split("\n", 2)[2]
        self._run(self._mixed_drop(None, gd_text=gd, gd_name="3-30000-1-1.gd"))
        self.assertEqual(("Ara-3", 30000, "1-1"), self._coordinate(self._sample("3-30000-1-1")))

    def test_a_csv_row_outranks_the_header(self):
        gd = "#=GENOME_DIFF\t1.0\n#=SAMPLE\tx\n#=POPULATION\th\n#=TIME\t1\n#=REFSEQ\ttest_ref\n" \
             + breseq_fixture.GD_TEXT.split("\n", 2)[2]
        self._run(self._mixed_drop(HEADER + "csv,c,9,loose\n", gd_text=gd))
        self.assertEqual(("c", 9, "csv"), self._coordinate(self._sample("loose")))

    def test_vcf_headers_place_the_file_and_structured_lines_place_a_column(self):
        root = self.drop()
        breseq_fixture.write_sample(root, "ZDB16")
        header = ["##fileformat=VCFv4.2", "##population=wt", "##generation=12",
                  "##SAMPLE=<ID=colB,sample=bee,population=mut,generation=3>",
                  "##contig=<ID=test_ref,length=160>"]
        _write(root, "two.vcf", vcf_text([VCF_ROW + "\t1"], samples=("colA", "colB"),
                                         header=header))
        summary = self._run(root)
        self.assertEqual([], [f for f in summary["files"] if f.get("error")], summary)
        self.assertEqual(("wt", 12, "colA"), self._coordinate(self._sample("colA")))
        self.assertEqual(("mut", 3, "bee"), self._coordinate(self._sample("colB")))

    def test_a_header_with_a_population_and_no_time_point_fails_that_file(self):
        gd = "#=GENOME_DIFF\t1.0\n#=SAMPLE\tx\n#=POPULATION\th\n#=REFSEQ\ttest_ref\n" \
             + breseq_fixture.GD_TEXT.split("\n", 2)[2]
        summary = self._run(self._mixed_drop(None, gd_text=gd))
        errors = {f["file"]: f["error"] for f in summary["files"] if f.get("error")}
        self.assertIn("loose.gd", errors)
        self.assertIn("time point", errors["loose.gd"])


class MetadataSurfacesTestCase(_DropTestCase):
    """The CLI prints the report; a broken file is a 400 at finalize."""

    def test_the_cli_prints_the_report(self):
        from mutint_import.experiments import import_paths

        root = self._mixed_drop(HEADER + "clone16,Ara-3,30000,ZDB16\nghost,g,1,nope.gd\n")
        out = io.StringIO()
        with redirect_stdout(out):
            import_paths([root], self.experiment, self.user)
        text = out.getvalue()
        self.assertIn("metadata.csv: 2 row(s); placed 1 input(s)", text)
        self.assertIn("nope.gd", text)
        self.assertIn("not named by the file", text)

    def test_a_broken_csv_is_a_400_at_finalize(self):
        self.client.force_login(self.user)
        payload = b"sample,population,data\nx,p,y\n"
        created = self.client.post(
            "/import/uploads/",
            data=json.dumps({"experiment_id": self.experiment.id,
                             "import_type": "breseq_folder",
                             "files": [{"path": "metadata.csv", "size": len(payload)}]}),
            content_type="application/json")
        self.assertEqual(200, created.status_code, created.content)
        upload_id = created.json()["upload_id"]
        chunked = self.client.post("/import/uploads/%s/chunk" % upload_id,
                                   {"path": "metadata.csv", "offset": "0",
                                    "chunk": SimpleUploadedFile("chunk", payload)})
        self.assertEqual(200, chunked.status_code, chunked.content)
        finalized = self.client.post("/import/uploads/%s/finalize" % upload_id)
        self.assertEqual(400, finalized.status_code, finalized.content)
        self.assertIn("time_point", finalized.json()["error"])

    def test_the_page_links_the_templates(self):
        self.client.force_login(self.user)
        html = self.client.get("/import/?experiment_id=%d" % self.experiment.id).content.decode()
        self.assertIn("mutint_import/metadata-template.csv", html)
        self.assertIn("mutint_import/metadata-example.csv", html)
        self.assertIn("isMetadata", html)
