import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_experiment.models import Project


class AddPageTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        # Go through the real creation path: it issues the guardian grant, without which
        # can_view_project would refuse the owner their own project (it consults the grant,
        # never Project.user).
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        from aledb_experiment.models import AleExperiment
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

    def test_page_renders_scoped_to_the_experiment(self):
        response = self.client.get(
            "/import/add/", {"ale_experiment_id": self.experiment.ale_id})

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("E", html)
        # No project/experiment/person boxes: the target and the user are both implicit.
        self.assertNotIn('id="gd-person"', html)
        self.assertNotIn('id="ref-person"', html)

    def test_dropdown_lists_every_registered_type(self):
        response = self.client.get(
            "/import/add/", {"ale_experiment_id": self.experiment.ale_id})
        html = response.content.decode("utf-8")

        self.assertIn("Auto-detect", html)
        for label in ("Reference genome", "breseq result folder", "GenomeDiff mutations"):
            self.assertIn(label, html)

    def test_a_plugin_type_reaches_the_dropdown(self):
        from aledb_common import import_registry

        import_registry.register_import_handler(
            name="page_test_type", label="Plugin readings (.tsv)",
            patterns=[".tsv"], handle=lambda *a: {"files": [], "total_mutations": 0})
        self.addCleanup(lambda: import_registry._import_handlers.__setitem__(
            slice(None),
            [h for h in import_registry._import_handlers if h["name"] != "page_test_type"]))

        response = self.client.get(
            "/import/add/", {"ale_experiment_id": self.experiment.ale_id})
        self.assertIn("Plugin readings (.tsv)", response.content.decode("utf-8"))

    def test_types_endpoint_returns_the_registry(self):
        body = self.client.get("/import/types/").json()
        names = [t["name"] for t in body["types"]]
        self.assertEqual(
            names[:4], ["reference", "replace_annotation", "breseq_folder", "genomediff"])
        self.assertIn(".gbk", body["types"][0]["patterns"])
        # Unfiltered here: this endpoint has no experiment to scope by.
        self.assertTrue(body["types"][1]["requires_reference"])

    def test_replace_annotation_is_offered_only_once_a_reference_exists(self):
        """It cannot do anything before there is a sequence to hold fixed, so offering it
        would just be a way to get an error message."""
        def dropdown():
            return self.client.get(
                "/import/add/", {"ale_experiment_id": self.experiment.ale_id}
            ).content.decode("utf-8")

        self.assertNotIn("Replace annotation", dropdown())

        from aledb_import import reference_store
        from aledb_import.tests import breseq_fixture
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(sequences), sequences)

        self.assertIn("Replace annotation", dropdown())

    def test_missing_experiment_explains_rather_than_500s(self):
        self.assertEqual(self.client.get("/import/add/").status_code, 404)
        self.assertEqual(
            self.client.get("/import/add/", {"ale_experiment_id": 999999}).status_code, 404)

    def test_someone_elses_experiment_is_forbidden(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self.client.get(
            "/import/add/", {"ale_experiment_id": self.experiment.ale_id})
        self.assertEqual(response.status_code, 403)

    def test_experiment_page_offers_add_and_delete(self):
        response = self.client.get("/stats/", {"ale_experiment_id": self.experiment.ale_id})
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/import/add/?ale_experiment_id=%d" % self.experiment.ale_id, html)
        self.assertIn("delete-experiment", html)
        self.assertIn("This is permanent.", html)

    def test_list_pages_offer_create_and_delete(self):
        projects = self.client.get("/ale/projects/").content.decode("utf-8")
        self.assertIn("New project", projects)
        self.assertIn("First experiment", projects)   # optional experiment in the same step
        self.assertIn("delete-selected", projects)
        self.assertIn("This is permanent.", projects)
        # aledbConfirmDelete calls swal(), which base.html does not load.
        self.assertIn("sweetalert", projects)

        experiments = self.client.get("/ale/experiments/").content.decode("utf-8")
        self.assertIn("delete-selected", experiments)
        self.assertIn("This is permanent.", experiments)
