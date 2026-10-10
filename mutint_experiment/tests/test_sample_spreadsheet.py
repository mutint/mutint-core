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
        self.clone.treatment = "glucose"
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
        self.assertEqual(("763A", "Ara-2", 500, True, "the clone", "glucose"),
                         (clone.sample, clone.population, clone.time_point, clone.is_clonal,
                          clone.description, clone.treatment))
        self.assertTrue(clone.flags["is_hypermutator"])
        self.assertFalse(parsed.lookup("mix").is_clonal)
        # A sample with no treatment writes a blank cell, which reads back as "" and not None
        # -- the file has the column, so it has an opinion.
        self.assertEqual("", parsed.lookup("mix").treatment)

    def test_the_download_includes_the_ancestor(self):
        self.experiment.ancestor = self.mix
        self.experiment.save(update_fields=["ancestor"])
        self.assertIsNotNone(metadata.parse(self.download().content).lookup("mix"))

    def test_a_reader_may_download_and_not_upload(self):
        """The file is the table a reader can already see; reading one back is editing."""
        self.login_reader()
        self.assertEqual(200, self.download().status_code)
        self.assertEqual(403, self.upload("sample,population,time_point,data\n").status_code)

    def login_reader(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.project, reader, ROLE_READ, granted_by=self.user)
        self.client.force_login(reader)
        return reader

    # --- the upload -----------------------------------------------------------------------

    def test_the_downloaded_file_reads_back_as_what_the_table_already_shows(self):
        answer = self.upload(self.download().content.decode()).json()
        self.assertEqual({}, {k: v for k, v in answer.items() if k == "error"})
        clone = answer["samples"][str(self.clone.pk)]
        self.assertEqual({"population": "Ara-2", "time_point": 500, "name": "763A",
                          "is_mixed": False, "description": "the clone",
                          "treatment": "glucose",
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
        self.assertNotIn("treatment", clone)
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

    def test_a_locked_experiment_refuses_the_upload_and_not_the_download(self):
        self.experiment.lock(self.user)
        self.assertEqual(200, self.download().status_code)
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
        self.assertEqual({"population": "population",
                          "time_point": "time-point", "name": "sample",
                          "treatment": "treatment",
                          "description": "description"}, dict(read))
        for _field, prefix in read:
            self.assertIn('id="sb-%s-%d"' % (prefix, self.clone.pk), body, prefix)

    def test_the_sample_name_is_a_link_to_its_own_page_and_not_a_box(self):
        """The name is what a re-import finds a sample by, so it is changed on the sample's
        own page only. The table shows it as a link there, and the save leaves it alone."""
        body = self.client.get("/experiment/%d/samples/" % self.experiment.id).content.decode()
        self.assertNotIn('id="sb-name-', body)
        self.assertIn('<a href="/sample/%d/edit/"' % self.clone.pk, body)
        self.assertIn(">Ara-2_500gen_763A</a>", body)
        row = self.row(self.clone, time_point=600)
        row.pop("source_name")
        self.assertEqual(200, self.bulk([row]).status_code)
        self.clone.refresh_from_db()
        self.assertEqual(("Ara-2_500gen_763A", 600),
                         (self.clone.source_name, self.clone.time_point))

    def test_the_treatment_boxes_offer_the_experiments_treatments(self):
        """The list of treatments is the samples' own values, offered back as suggestions
        through one datalist every box points at -- so a person filing the fortieth sample
        picks the word the first thirty-nine used rather than retyping it."""
        self.mix.treatment = "lactose"
        self.mix.save(update_fields=["treatment"])
        body = self.client.get("/experiment/%d/samples/" % self.experiment.id).content.decode()
        self.assertIn('<datalist id="sb-treatments">', body)
        self.assertIn('<option value="glucose">', body)
        self.assertIn('<option value="lactose">', body)
        tag = body[body.index('id="sb-treatment-%d"' % self.clone.pk) - 200:]
        tag = tag[tag.rindex("<input", 0, 200):]
        self.assertIn('list="sb-treatments"', tag[:tag.index(">")])
        # An uploaded file with a blank treatment cell blanks the box -- the file said so.
        answer = self.upload("sample,population,time_point,treatment,data\n"
                             "763A,Ara-2,500,,Ara-2_500gen_763A\n").json()
        self.assertEqual("", answer["samples"][str(self.clone.pk)]["treatment"])

    def test_no_box_in_the_table_is_restored_by_the_browser_on_reload(self):
        """Save reloads the page, which comes back sorted by the new coordinates, and a
        browser restores typed values by position rather than by id -- so a renumber put each
        row's old values into another sample's row, and the next Save wrote them."""
        body = self.client.get("/experiment/%d/samples/" % self.experiment.id).content.decode()
        table = body[body.index("<tbody>"):body.index("</tbody>")]
        inputs = re.findall(r"<input\b[^>]*>", table)
        self.assertTrue(inputs)
        for tag in inputs:
            self.assertIn('autocomplete="off"', tag, tag)


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
        # Not `requires_edit`: the page opens read-only for any reader and gates its own
        # Edit button.
        self.assertFalse(items[labels.index("Samples")]["requires_edit"])


class ViewModeTestCase(SpreadsheetTestCase):
    """The page opens read-only and Edit opens the boxes.

    Both halves of every cell are server-rendered -- the value as text and as the box the
    save script reads -- and a class the Edit button sets decides which shows. Nothing runs
    the script here; what is pinned is that each mode has what it needs and that a reader
    gets no control whose endpoint would refuse them.
    """

    def page(self):
        return self.client.get("/experiment/%d/samples/" % self.experiment.id)

    def test_an_editor_gets_the_edit_button_and_the_boxes(self):
        body = self.page().content.decode()
        self.assertIn('id="sb-edit"', body)
        self.assertIn('id="sb-save"', body)
        self.assertIn('id="sb-cancel"', body)
        self.assertIn('id="sb-page" class="sb-can-edit"', body)
        # The value as text, beside its box.
        self.assertIn('<span class="sb-view">the clone</span>', body)
        self.assertIn('<span class="sb-view">glucose</span>', body)
        self.assertIn('<span class="sb-view">500</span>', body)
        # A flag that is on is a tick; the clone's hypermutator flag is on and the mix's is not.
        clone_row = body[body.index('data-sample-id="%d"' % self.clone.pk):]
        clone_row = clone_row[:clone_row.index("</tr>")]
        self.assertIn("&#10003;", clone_row)
        self.assertIn('id="sb-hypermutator-%d"' % self.clone.pk, clone_row)

    def test_the_boxes_and_the_select_column_are_edit_only(self):
        body = self.page().content.decode()
        table = body[body.index("<tbody>"):body.index("</tbody>")]
        for tag in re.findall(r"<input\b[^>]*>", table):
            # The row's select box sits in a cell that is edit-only as a whole.
            if 'class="sb-select"' in tag:
                continue
            self.assertIn("sb-edit", tag, tag)
        self.assertIn('<td class="sb-edit" style="text-align: center;">\n'
                      '                        <input autocomplete="off" type="checkbox" class="sb-select"',
                      table)

    def test_a_reader_gets_the_table_and_nothing_to_press(self):
        self.login_reader()
        response = self.page()
        self.assertEqual(200, response.status_code)
        body = response.content.decode()
        for sample in (self.clone, self.mix):
            self.assertIn('data-sample-id="%d"' % sample.pk, body)
        self.assertIn("/samples/metadata.csv", body)
        self.assertNotIn('id="sb-edit"', body)
        self.assertIn('id="sb-page" class=""', body)
        for control in ("sb-save", "sb-cancel", "sb-upload", "sb-delete", "sb-ancestor",
                        "sb-upload-file"):
            self.assertNotIn('id="%s"' % control, body, control)
        self.assertNotIn("Cells with changes will be highlighted", body)

    def test_the_sizing_runs_for_a_reader_too(self):
        self.login_reader()
        self.assertContains(self.page(), 'if (!document.getElementById("sb-page")) { return; }')
