import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import import_registry
from mutint_experiment.models import Project


class AddPageTestCase(TestCase):
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
            "/import/add/", {"experiment_id": self.experiment.id})

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("E", html)
        # No project/experiment/person boxes: the target and the user are both implicit.
        self.assertNotIn('id="gd-person"', html)
        self.assertNotIn('id="ref-person"', html)

    def test_dropdown_lists_the_registered_types_this_experiment_can_use(self):
        response = self.client.get(
            "/import/add/", {"experiment_id": self.experiment.id})
        html = response.content.decode("utf-8")

        # No Auto-detect: the type is chosen, never guessed.
        self.assertNotIn("Auto-detect", html)
        offered = html.split('id="add-type"')[1].split("</select>")[0]
        for label in ("Reference genome", "breseq data folders"):
            self.assertIn(label, offered)
        # The two that need a reference are absent until there is one -- see
        # ImportTypesOfferedTestCase, which covers both sides of that.

    def test_a_plugin_type_reaches_the_dropdown(self):
        import_registry.register_import_handler(
            name="page_test_type", label="Plugin readings (.tsv)",
            patterns=[".tsv"], handle=lambda *a: {"files": [], "total_mutations": 0})
        self.addCleanup(lambda: import_registry._import_handlers.__setitem__(
            slice(None),
            [h for h in import_registry._import_handlers if h["name"] != "page_test_type"]))

        html = self.client.get(
            "/import/add/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")
        self.assertIn("Plugin readings (.tsv)",
                      html.split('id="add-type"')[1].split("</select>")[0])

    def test_types_endpoint_returns_the_registry(self):
        body = self.client.get("/import/types/").json()
        names = [t["name"] for t in body["types"]]
        self.assertEqual(
            names[:4], ["reference", "replace_annotation", "breseq_folder", "genomediff"])
        self.assertIn(".gbk", body["types"][0]["patterns"])
        # Unfiltered here: this endpoint has no experiment to scope by.
        self.assertTrue(body["types"][1]["requires_reference"])

    def test_the_dropdown_leads_with_mutations_and_ends_with_replace_annotation(self):
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

    def test_the_menu_order_is_what_the_page_renders(self):
        """Asserted on the rendered <select>, not only on the registry, because the
        template could always have re-sorted them back."""
        from mutint_import import reference_store
        from mutint_import.tests import breseq_fixture
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(sequences), sequences)

        html = self.client.get(
            "/import/add/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")
        select = html.split('id="add-type"')[1].split("</select>")[0]

        self.assertLess(select.index('value="genomediff"'),
                        select.index('value="breseq_folder"'))
        self.assertLess(select.index('value="breseq_folder"'),
                        select.index('value="replace_annotation"'))

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
            "/import/add/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")

        self.assertIn("TERMINAL_STATES", html)
        self.assertIn("concludeFromSnapshot", html)

    def test_the_page_polls_for_import_progress(self):
        html = self.client.get(
            "/import/add/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")
        self.assertIn("/progress", html)
        # A completed import must forget what was dropped, or pressing Add again
        # re-imports the samples that already landed.
        self.assertIn("clearSelection", html)

    def test_replace_annotation_is_offered_only_once_a_reference_exists(self):
        """It cannot do anything before there is a sequence to hold fixed, so offering it
        would just be a way to get an error message."""
        def dropdown():
            html = self.client.get(
                "/import/add/", {"experiment_id": self.experiment.id}
            ).content.decode("utf-8")
            # The <select> alone: the page also embeds the unscoped registry for naming
            # stray files, so every label appears somewhere regardless.
            return html.split('id="add-type"')[1].split("</select>")[0]

        self.assertNotIn("Replace annotation", dropdown())

        from mutint_import import reference_store
        from mutint_import.tests import breseq_fixture
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(sequences), sequences)

        self.assertIn("Replace annotation", dropdown())

    def test_missing_experiment_is_a_404(self):
        """The page is only ever scoped to one experiment; there is no unscoped form."""
        self.assertEqual(self.client.get("/import/add/").status_code, 404)
        self.assertEqual(
            self.client.get("/import/add/", {"experiment_id": 999999}).status_code, 404)
        self.assertEqual(
            self.client.get("/import/add/", {"experiment_id": "nonsense"}).status_code, 404)

    def test_no_add_data_entry_in_the_sidebar(self):
        """It could only ever have led to /import/add/ with nothing to add to."""
        from mutint_common.nav_registry import get_nav_items

        labels = [item["label"] for item in get_nav_items()]
        self.assertNotIn("Add data", labels)

    def test_someone_elses_experiment_is_forbidden(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self.client.get(
            "/import/add/", {"experiment_id": self.experiment.id})
        self.assertEqual(response.status_code, 403)

    def test_experiment_page_offers_add_and_delete(self):
        response = self.client.get("/stats/", {"experiment_id": self.experiment.id})
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/import/add/?experiment_id=%d" % self.experiment.id, html)
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
    """Which import types the Add page offers, and in what state.

    Three different rules, because the reasons differ:

      reference           establishing one is a one-time act, so it stops being
                          offered once the experiment has one
      replace_annotation  meaningless before there is a genome to hold fixed, so it
                          is absent until then
      genomediff          needs a reference, but is a thing people arrive holding --
                          so it is shown grayed with the reason, not hidden
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

    def _html(self):
        return self.client.get(
            "/import/add/", {"experiment_id": self.experiment.id}
        ).content.decode("utf-8")

    def _dropdown(self):
        """Just the <select>. The page also embeds the *unscoped* registry, for naming the
        type a stray file belongs to, so every label appears somewhere in the HTML whether
        or not it is offered -- searching the whole page would pass either way."""
        return self._html().split('id="add-type"')[1].split("</select>")[0]

    # --- with no reference yet -----------------------------------------------

    def test_reference_is_offered(self):
        self.assertIn("Reference genome", self._dropdown())

    def test_replace_annotation_is_absent(self):
        self.assertNotIn("Replace annotation", self._dropdown())

    def test_genomediff_is_absent(self):
        """Not offered grayed out: an entry you can see and cannot pick is a dead end."""
        self.assertNotIn('value="genomediff"', self._dropdown())

    def test_the_page_says_why_the_menu_is_short(self):
        """The banner is where the answer lives once the entry itself is gone."""
        self.assertIn("not offered below", self._html())

    def test_the_page_says_a_reference_is_needed(self):
        self.assertIn("no reference genome yet", self._html())

    def test_breseq_folders_stay_available(self):
        """A breseq folder carries its own reference, so it is never blocked."""
        self.assertIn('value="breseq_folder"', self._dropdown())

    # --- once a reference exists ---------------------------------------------

    def test_reference_stops_being_offered(self):
        self._establish_reference()
        self.assertNotIn("Reference genome", self._dropdown())

    def test_replace_annotation_appears(self):
        self._establish_reference()
        self.assertIn("Replace annotation", self._dropdown())

    def test_replace_annotation_names_the_formats_it_takes(self):
        """The label names renaming too: this type is the only route to it, and a FASTA is
        accepted for exactly that reason even though it installs no annotation."""
        self._establish_reference()
        self.assertIn("Replace annotation or rename contigs (GenBank / GFF3 / FASTA)",
                      self._dropdown())

    def test_genomediff_becomes_selectable(self):
        self._establish_reference()
        self.assertIn('value="genomediff"', self._dropdown())
        self.assertNotIn("no reference genome yet", self._html())

    def test_the_unscoped_types_endpoint_stays_unfiltered(self):
        """It has no experiment to scope by, and the handlers enforce the rules anyway."""
        names = [t["name"] for t in self.client.get("/import/types/").json()["types"]]
        self.assertIn("reference", names)
        self.assertIn("replace_annotation", names)
        self.assertIn("genomediff", names)
