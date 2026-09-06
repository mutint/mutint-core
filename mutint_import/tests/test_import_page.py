import re
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import import_registry
from mutint_experiment.models import Project


def _tabs(html):
    """The strip alone: `[(href, label)]`, in order."""
    strip = html.split('class="nav nav-tabs import-tabs"')[1].split("</ul>")[0]
    return re.findall(r'<a href="([^"]+)">([^<]+)</a>', strip)


class ImportPageTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        # Go through the real creation path: it issues the guardian grant, without which
        # can_view_project would refuse the owner their own project (it consults the grant,
        # never Project.user).
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        from mutint_experiment.models import Experiment
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

    def test_page_renders_scoped_to_the_experiment(self):
        response = self.client.get(
            "/import/", {"experiment_id": self.experiment.id})

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("E", html)
        # No project/experiment/person boxes: the target and the user are both implicit.
        self.assertNotIn('id="gd-person"', html)
        self.assertNotIn('id="ref-person"', html)

    def test_the_tabs_are_the_registered_ways_in_in_order(self):
        """One tab per registered type, in registration order, each landing on this page
        with that type -- and the first is the page's default. Asserted against the registry
        rather than a list of core's own four, because an assembled project's plugins add
        tabs of their own (mutint-breseq's Run breseq) and this test runs there too."""
        html = self.client.get("/import/", {"experiment_id": self.experiment.id}
                               ).content.decode("utf-8")

        # No Auto-detect and no dropdown: the type is the tab, never guessed.
        self.assertNotIn("Auto-detect", html)
        self.assertNotIn('<select class="form-control" id="import-type"', html)
        tabs = _tabs(html)
        from mutint_common.import_tab_registry import get_import_tabs
        self.assertEqual([tab["label"] for tab in get_import_tabs(self.experiment.id)],
                         [t[1] for t in tabs])
        self.assertEqual(["Reference Sequence", "Genome Diff", "Variant Call Format",
                          "Results Folder"], [t[1] for t in tabs][:4])
        self.assertEqual("/import/?experiment_id=%d&amp;tab=genomediff" % self.experiment.id,
                         tabs[1][0])
        self.assertIn('id="import-type" value="reference"', html)
        active = html.split('<li class="active">')[1].split("</li>")[0]
        self.assertIn("tab=reference", active)

    def test_a_tab_chooses_its_type(self):
        html = self.client.get("/import/", {"experiment_id": self.experiment.id,
                                            "tab": "breseq_folder"}).content.decode("utf-8")
        self.assertIn('id="import-type" value="breseq_folder"', html)
        self.assertIn("breseq data folders", html)
        active = html.split('<li class="active">')[1].split("</li>")[0]
        self.assertIn("Results Folder", active)

    def test_an_unknown_tab_is_a_404(self):
        self.assertEqual(404, self.client.get(
            "/import/", {"experiment_id": self.experiment.id, "tab": "nonsense"}).status_code)

    def test_a_plugin_tab_reaches_the_strip_and_a_page_of_its_own_is_linked(self):
        """A plugin registers a tab: for a type of its own, landing here; or for a page of
        its own, which is what mutint-breseq's Run breseq is."""
        from mutint_common.import_tab_registry import (
            register_import_tab, unregister_import_tab)
        import_registry.register_import_handler(
            name="page_test_type", label="Plugin readings (.tsv)",
            patterns=[".tsv"], handle=lambda *a: {"files": [], "total_mutations": 0})
        self.addCleanup(lambda: import_registry._import_handlers.__setitem__(
            slice(None),
            [h for h in import_registry._import_handlers if h["name"] != "page_test_type"]))
        register_import_tab("page_test", "Readings", import_type="page_test_type")
        register_import_tab("page_test_page", "Elsewhere", url_name="reference_view")
        register_import_tab("page_test_dead", "Dead", url_name="no_such_route_anywhere")
        for key in ("page_test", "page_test_page", "page_test_dead"):
            self.addCleanup(unregister_import_tab, key)

        html = self.client.get("/import/", {"experiment_id": self.experiment.id,
                                            "tab": "page_test"}).content.decode("utf-8")
        tabs = dict((label, href) for href, label in _tabs(html))
        self.assertEqual("/import/?experiment_id=%d&amp;tab=page_test" % self.experiment.id,
                         tabs["Readings"])
        self.assertEqual("/mutations/reference?experiment_id=%d" % self.experiment.id,
                         tabs["Elsewhere"])
        self.assertNotIn("Dead", tabs, "a tab whose route will not reverse is skipped")
        self.assertIn("Plugin readings (.tsv)", html)

    def test_types_endpoint_returns_the_registry(self):
        body = self.client.get("/import/types/").json()
        names = [t["name"] for t in body["types"]]
        self.assertEqual(
            names[:4], ["reference", "replace_annotation", "breseq_folder", "genomediff"])
        self.assertIn(".gbk", body["types"][0]["patterns"])
        # Unfiltered here: this endpoint has no experiment to scope by.
        self.assertTrue(body["types"][1]["requires_reference"])

    def test_the_offered_list_leads_with_mutations_and_ends_with_replace_annotation(self):
        """Menu order is not run order, and this is the case that separates them.

        `genomediff` runs *last* -- a bare .gd is hash-checked against a reference that has
        to be established first -- and is the thing people most often come to this page to
        do. `replace_annotation` rewrites the genome every sample is checked against and is
        the rarest and least reversible entry, so it goes at the bottom. Neither position is
        expressible in `priority` without changing what a mixed drop does.
        """
        from mutint_import import reference_store
        from mutint_import.tests import breseq_fixture
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(sequences), sequences)

        offered = [t["name"] for t
                   in import_registry.get_import_types_for(has_reference=True)]

        self.assertEqual(offered[0], "genomediff")
        self.assertEqual(offered[-1], "replace_annotation")


    def test_run_order_is_unchanged_by_the_menu_order(self):
        """The guardrail for the above: `reference` must still run before `genomediff`,
        or a mixed drop imports mutations against a genome that is not there yet."""
        names = [h["name"] for h in import_registry.get_import_handlers()]
        self.assertLess(names.index("reference"), names.index("genomediff"))
        self.assertLess(names.index("reference"), names.index("breseq_folder"))

    def test_the_page_stops_polling_once_the_session_is_terminal(self):
        """A finalize response that never arrives -- a dropped connection, a restarted
        server -- used to leave the page polling a finished import for as long as it was
        open. The snapshot's own state is what ends it."""
        html = self.client.get(
            "/import/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")

        self.assertIn("TERMINAL_STATES", html)
        self.assertIn("concludeFromSnapshot", html)

    def test_the_page_polls_for_import_progress(self):
        html = self.client.get(
            "/import/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")
        self.assertIn("/progress", html)
        # A completed import must forget what was dropped, or pressing Add again
        # re-imports the samples that already landed.
        self.assertIn("clearSelection", html)


    def test_missing_experiment_is_a_404(self):
        """The page is only ever scoped to one experiment; there is no unscoped form."""
        self.assertEqual(self.client.get("/import/").status_code, 404)
        self.assertEqual(
            self.client.get("/import/", {"experiment_id": 999999}).status_code, 404)
        self.assertEqual(
            self.client.get("/import/", {"experiment_id": "nonsense"}).status_code, 404)

    def test_no_import_data_entry_in_the_sidebar(self):
        """It could only ever have led to /import/ with nothing to add to."""
        from mutint_common.nav_registry import get_nav_items

        labels = [item["label"] for item in get_nav_items()]
        self.assertNotIn("Add data", labels)
        self.assertNotIn("Import data", labels)

    def test_someone_elses_experiment_is_forbidden(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self.client.get(
            "/import/", {"experiment_id": self.experiment.id})
        self.assertEqual(response.status_code, 403)

    def test_experiment_page_offers_import_and_delete(self):
        response = self.client.get("/stats/", {"experiment_id": self.experiment.id})
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/import/?experiment_id=%d" % self.experiment.id, html)
        self.assertIn("+ Import data", html)
        self.assertNotIn("Add data", html)
        self.assertIn("delete-experiment", html)
        # The dialog copy used to be inlined here, and this asserted the literal
        # "This is permanent." Deleting an experiment is one of the four controls behind
        # the typed gate now, and the wording lives in mutint_crud.js with it.
        self.assertIn("mutintConfirmTypedDelete", html)

    def test_list_pages_offer_create_and_delete(self):
        projects = self.client.get("/project/").content.decode("utf-8")
        self.assertIn("New project", projects)
        self.assertIn("delete-selected", projects)
        # The form moved to a page of its own; the list only links to it.
        self.assertIn('href="/project/new/"', projects)
        new_project = self.client.get("/project/new/").content.decode("utf-8")
        self.assertIn("First experiment", new_project)   # optional, in the same step
        # mutintConfirmDelete calls swal(), which base.html does not load.
        self.assertIn("sweetalert", projects)

        experiments = self.client.get("/experiment/").content.decode("utf-8")
        self.assertIn("delete-selected", experiments)

    def test_both_list_pages_load_the_shared_crud_helpers(self):
        """mutintPost / mutintConfirmDelete moved out of the templates into one static
        file; a page that lost the script would fail silently on click.

        mutintTogglePanel was the third, and is gone: the create forms are modals now,
        opened declaratively by Bootstrap, so nothing toggles an inline panel."""
        from django.contrib.staticfiles import finders

        path = finders.find("js/mutint_crud.js")
        self.assertIsNotNone(path)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for helper in ("mutintPost", "mutintConfirmDelete", "mutintConfirmTypedDelete",
                       "mutintDeleteSelected"):
            self.assertIn(helper, source)
        self.assertNotIn("mutintTogglePanel", source)
        # mutintConfirmDelete keeps its wording; it is the plain confirm the group and
        # sharing pages use. If this line fails, the wrong helper was edited.
        self.assertIn("This is permanent.", source)

        for url in ("/project/", "/experiment/"):
            html = self.client.get(url).content.decode("utf-8")
            self.assertIn("js/mutint_crud.js", html, url)
            # And no longer inline, in two byte-identical copies.
            self.assertNotIn("window.mutintPost = function", html)


class ImportTypesOfferedTestCase(TestCase):
    """Which tabs can run, and what the others say.

    Every tab is always drawn; the dropdown this page used to have left a type out, and a tab
    you can see that says what it is waiting for is the better way to say the same thing.
    Three different rules, because the reasons differ:

      Reference Sequence  `reference` until the experiment has one, then
                          `replace_annotation` -- one tab for one question at two moments
      genomediff, vcf     need a reference, and are things people arrive holding -- so
                          the tab says to get one in first
    """

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        from mutint_experiment.models import Experiment
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

    def _establish_reference(self):
        from mutint_import import reference_store
        from mutint_import.tests import breseq_fixture
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(sequences), sequences)

    def _html(self, tab):
        return self.client.get(
            "/import/", {"experiment_id": self.experiment.id, "tab": tab}
        ).content.decode("utf-8")

    def _offers_a_form(self, tab):
        html = self._html(tab)
        return 'id="import-form"' in html and 'id="import-not-offered"' not in html

    def _type_chosen(self, tab):
        return self._html(tab).split('id="import-type" value="')[1].split('"')[0]

    # --- with no reference yet -----------------------------------------------

    def test_the_reference_tab_establishes_one(self):
        self.assertTrue(self._offers_a_form("reference"))
        self.assertEqual("reference", self._type_chosen("reference"))
        # The page's own words, not the embedded unscoped registry, which names every type.
        self.assertIn("Sets the reference every sample", self._html("reference"))

    def test_genomediff_and_vcf_say_they_need_a_reference(self):
        for name in ("genomediff", "vcf"):
            with self.subTest(type=name):
                self.assertFalse(self._offers_a_form(name))
                self.assertIn("Reference Sequence tab", self._html(name))

    def test_breseq_folders_stay_available(self):
        """A breseq folder carries its own reference, so it is never blocked."""
        self.assertTrue(self._offers_a_form("breseq_folder"))

    # --- once a reference exists ---------------------------------------------

    def test_the_reference_tab_becomes_replace_annotation(self):
        """The same tab, now the other handler: what it imports as says so, and the form is
        still there."""
        self._establish_reference()
        self.assertTrue(self._offers_a_form("reference"))
        self.assertEqual("replace_annotation", self._type_chosen("reference"))
        self.assertIn("Replace annotation or rename contigs (GenBank / GFF3 / FASTA)",
                      self._html("reference"))
        self.assertNotIn("already has a reference genome", self._html("reference"))

    def test_genomediff_becomes_available(self):
        self._establish_reference()
        self.assertTrue(self._offers_a_form("genomediff"))
        self.assertNotIn('id="import-not-offered"', self._html("genomediff"))

    def test_the_unscoped_types_endpoint_stays_unfiltered(self):
        """It has no experiment to scope by, and the handlers enforce the rules anyway."""
        names = [t["name"] for t in self.client.get("/import/types/").json()["types"]]
        self.assertIn("reference", names)
        self.assertIn("replace_annotation", names)
        self.assertIn("genomediff", names)
