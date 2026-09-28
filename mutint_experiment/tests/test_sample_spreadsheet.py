"""The Edit samples page as a spreadsheet: the metadata.csv it offers, and that file read back.

Reading an upload writes nothing -- the values go into the page's boxes and Save posts them
through the same path as anything typed -- so most of what is worth pinning is what the read
*returns*, and that it left the database alone.
"""

import re

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from mutint_experiment.permissions import grant_project_access
from mutint_experiment.roles import ROLE_READ
from mutint_experiment.tests.test_sample_edit import SampleEditTestCase
from mutint_import import metadata
from mutint_sample.models import Sample


class SpreadsheetTestCase(SampleEditTestCase):

    def setUp(self):
        super().setUp()
        self.clone = self.make_sample("Ara-2", 500, "763A", source_name="Ara-2_500gen_763A")
        self.clone.description = "the clone"
        self.clone.is_hypermutator = True
        self.clone.save()
        self.mix = self.make_sample("Ara-2", 2000, "mix", source_name="mix.gd", is_mixed=True)

    def download(self):
        return self.client.get("/experiment/%d/samples/metadata.csv" % self.experiment.id)

    def upload(self, text, name="metadata.csv"):
        return self.client.post(
            "/experiment/%d/samples/metadata/" % self.experiment.id,
            {"file": SimpleUploadedFile(name, text.encode("utf-8"), "text/csv")})

    # --- the download ---------------------------------------------------------------------

    def test_the_download_is_the_tables_values_in_the_import_format(self):
        response = self.download()
        self.assertEqual(200, response.status_code)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("_metadata.csv", response["Content-Disposition"])
        parsed = metadata.parse(response.content)
        clone = parsed.lookup("Ara-2_500gen_763A")
        self.assertEqual(("763A", "Ara-2", 500, True, "the clone"),
                         (clone.sample, clone.population, clone.time_point, clone.is_clonal,
                          clone.description))
        self.assertTrue(clone.flags["is_hypermutator"])
        self.assertFalse(parsed.lookup("mix").is_clonal)

    def test_the_download_includes_the_ancestor(self):
        self.experiment.ancestor = self.mix
        self.experiment.save(update_fields=["ancestor"])
        self.assertIsNotNone(metadata.parse(self.download().content).lookup("mix"))

    def test_a_reader_cannot_download_or_upload(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.project, reader, ROLE_READ, granted_by=self.user)
        self.client.force_login(reader)
        self.assertEqual(403, self.download().status_code)
        self.assertEqual(403, self.upload("sample,population,time_point,data\n").status_code)

    # --- the upload -----------------------------------------------------------------------

    def test_the_downloaded_file_reads_back_as_what_the_table_already_shows(self):
        answer = self.upload(self.download().content.decode()).json()
        self.assertEqual({}, {k: v for k, v in answer.items() if k == "error"})
        clone = answer["samples"][str(self.clone.pk)]
        self.assertEqual({"population": "Ara-2", "time_point": 500, "name": "763A",
                          "is_mixed": False, "description": "the clone",
                          "flags": {"is_hypermutator": True, "is_contaminated": False,
                                    "is_low_coverage": False}}, clone)
        self.assertEqual([], answer["unmatched_rows"])
        self.assertEqual([], answer["unnamed_samples"])

    def test_an_edited_file_returns_new_values_and_writes_nothing(self):
        answer = self.upload(
            "sample,population,time_point,hypermutator,data\n"
            "763B,Ara-1,1000,,Ara-2_500gen_763A\n"
            "ghost,p,1,,nothing_here.gd\n").json()
        clone = answer["samples"][str(self.clone.pk)]
        self.assertEqual(("Ara-1", 1000, "763B"),
                         (clone["population"], clone["time_point"], clone["name"]))
        # Columns the file left out are not in the answer, so those boxes are left alone.
        self.assertNotIn("description", clone)
        self.assertNotIn("is_mixed", clone)
        self.assertEqual({}, clone["flags"])
        self.assertEqual(1, len(answer["unmatched_rows"]))
        self.assertIn("nothing_here.gd", answer["unmatched_rows"][0])
        self.assertEqual([self.mix.label], answer["unnamed_samples"])
        # Nothing was saved.
        self.assertEqual(("Ara-2", 500, "763A"), self.coordinate(self.clone))

    def test_an_unplaced_row_blanks_the_time_point_and_keeps_the_population(self):
        clone = self.upload("sample,population,time_point,data\n"
                            "763A,,,Ara-2_500gen_763A\n").json()["samples"][str(self.clone.pk)]
        self.assertIsNone(clone["time_point"])
        self.assertNotIn("population", clone)

    def test_a_malformed_file_is_a_400_naming_what_is_wrong(self):
        response = self.upload("sample,data\n763A,x\n")
        self.assertEqual(400, response.status_code)
        self.assertIn("population", response.json()["error"])
        response = self.upload("sample,population,time_point,data\n"
                               "a,p,1,mix\nb,p,2,mix.gd\n")
        self.assertEqual(400, response.status_code)
        self.assertIn("name it once", response.json()["error"])

    def test_no_file_is_a_400(self):
        response = self.client.post("/experiment/%d/samples/metadata/" % self.experiment.id)
        self.assertEqual(400, response.status_code)

    def test_a_locked_experiment_refuses(self):
        self.experiment.lock(self.user)
        self.assertEqual(403, self.download().status_code)
        self.assertEqual(403, self.upload("sample,population,time_point,data\n").status_code)

    # --- the page -------------------------------------------------------------------------

    def test_the_page_offers_both(self):
        response = self.client.get("/experiment/%d/samples/" % self.experiment.id)
        self.assertContains(response, "/samples/metadata.csv")
        self.assertContains(response, 'id="sb-upload-file"')

    def test_every_box_the_save_script_reads_is_one_the_page_renders(self):
        """The save script read `sb-time_point-` and `sb-name-` for the label for a while,
        after a rename, so every save blanked each time point and relabelled each sample by
        its source name. Nothing ran the script; this at least keeps its ids honest."""
        body = self.client.get("/experiment/%d/samples/" % self.experiment.id).content.decode()
        script = body[body.index('addEventListener("click", function () {\n        var rows'):]
        read = re.findall(r'(\w+): value\("([\w-]+)", id\)', script)
        self.assertEqual({"source_name": "name", "population": "population",
                          "time_point": "time-point", "name": "sample",
                          "description": "description"}, dict(read))
        for _field, prefix in read:
            self.assertIn('id="sb-%s-%d"' % (prefix, self.clone.pk), body, prefix)


class SamplesNavTestCase(SampleEditTestCase):

    def test_the_entry_leads_to_the_selected_experiments_page(self):
        response = self.client.get("/experiment/samples/?experiment_id=%d" % self.experiment.id)
        self.assertRedirects(response, "/experiment/%d/samples/" % self.experiment.id)
        self.assertEqual(404, self.client.get("/experiment/samples/").status_code)
        self.assertEqual(404, self.client.get("/experiment/samples/?experiment_id=x").status_code)

    def test_it_is_registered_first_in_the_experiment_section_for_editors(self):
        from mutint_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

        items = get_nav_items(EXPERIMENT_SECTION)
        labels = [item["label"] for item in items]
        self.assertLess(labels.index("Samples"), labels.index("Mutations"))
        self.assertTrue(items[labels.index("Samples")]["requires_edit"])
