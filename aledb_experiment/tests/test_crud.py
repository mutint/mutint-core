import os
import shutil
import tempfile
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from aledb_common import store
from aledb_experiment.models import AleExperiment, Project
from aledb_experiment.utils import get_all_user_exps, get_user_projects


class ProjectExperimentCreateTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="owner", email="o@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

    def test_create_project_alone(self):
        response = self.client.post("/ale/projects/create/", {"name": "Just a project"})

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNone(body["experiment_id"])

        project = Project.objects.get(pk=body["project_id"])
        self.assertEqual(project.user_id, self.user.id)   # created by the current user
        self.assertEqual(AleExperiment.objects.count(), 0)

    def test_create_project_with_an_experiment_in_one_step(self):
        response = self.client.post("/ale/projects/create/",
                                    {"name": "P", "experiment": "First experiment"})

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNotNone(body["experiment_id"])

        experiment = AleExperiment.objects.get(pk=body["experiment_id"])
        self.assertEqual(experiment.project_id, body["project_id"])
        self.assertEqual(experiment.person, "owner")

    def test_project_name_is_required(self):
        self.assertEqual(
            self.client.post("/ale/projects/create/", {"name": "  "}).status_code, 400)

    def test_same_named_experiments_stay_distinct(self):
        """Identity is the primary key, so a duplicate name is allowed, not merged."""
        project_id = self.client.post(
            "/ale/projects/create/", {"name": "P"}).json()["project_id"]

        first = self.client.post("/ale/experiments/create/",
                                 {"name": "Ara-1", "project": project_id}).json()
        second = self.client.post("/ale/experiments/create/",
                                  {"name": "Ara-1", "project": project_id}).json()

        self.assertNotEqual(first["experiment_id"], second["experiment_id"])
        self.assertEqual(AleExperiment.objects.filter(name="Ara-1").count(), 2)

    def test_cannot_add_an_experiment_to_someone_elses_project(self):
        project = Project.objects.create(name="Theirs", user=self.user)
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self.client.post("/ale/experiments/create/",
                                    {"name": "X", "project": project.id})
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_create(self):
        self.client.logout()
        self.assertEqual(
            self.client.post("/ale/projects/create/", {"name": "P"}).status_code, 403)


class SoftDeleteTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.project = Project.objects.create(name="P", user=self.user, is_public=True)
        from aledb_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "E", self.user)

    def test_delete_flags_rather_than_removes(self):
        response = self.client.post(
            "/ale/experiment/%d/delete/" % self.experiment.ale_id)

        self.assertEqual(response.status_code, 200, response.content)
        self.experiment.refresh_from_db()
        self.assertIsNotNone(self.experiment.deleted_at)
        self.assertEqual(self.experiment.deleted_by_id, self.user.id)
        # The row is still there -- that is the whole point.
        self.assertTrue(AleExperiment.objects.filter(pk=self.experiment.ale_id).exists())

    def test_deleted_experiment_disappears_from_the_list(self):
        self.assertIn(self.experiment, list(get_all_user_exps(self.user)))
        self.experiment.soft_delete(self.user)
        self.assertNotIn(self.experiment, list(get_all_user_exps(self.user)))

    def test_deleted_project_disappears_from_the_list(self):
        self.assertIn(self.project, list(get_user_projects(self.user)))
        self.project.soft_delete(self.user)
        self.assertNotIn(self.project, list(get_user_projects(self.user)))

    def test_project_experiments_helper_excludes_deleted(self):
        self.assertEqual(list(self.project.experiments()), [self.experiment])
        self.experiment.soft_delete(self.user)
        self.assertEqual(list(self.project.experiments()), [])

    def test_non_owner_cannot_delete(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        self.assertEqual(
            self.client.post("/ale/experiment/%d/delete/" % self.experiment.ale_id
                             ).status_code, 403)
        self.assertEqual(
            self.client.post("/ale/project/%d/delete/" % self.project.id).status_code, 403)

    def test_superuser_can_delete_anything(self):
        admin = User.objects.create(
            username="admin", email="a@e.com", is_active=True, is_superuser=True)
        admin.set_password("pw")
        admin.save()
        self.client.force_login(admin)

        self.assertEqual(
            self.client.post("/ale/project/%d/delete/" % self.project.id).status_code, 200)

    def test_delete_is_idempotent(self):
        url = "/ale/experiment/%d/delete/" % self.experiment.ale_id
        first = self.client.post(url).json()["deleted_at"]
        second = self.client.post(url).json()["deleted_at"]
        self.assertEqual(first, second)


class PurgeDeletedTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="P", user=self.user)
        from aledb_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "E", self.user)

    def _purge(self, *args):
        out = StringIO()
        call_command("purge_deleted", *args, stdout=out)
        return out.getvalue()

    def test_recently_deleted_rows_survive(self):
        self.experiment.soft_delete(self.user)
        self._purge("--older-than", "30")
        self.assertTrue(AleExperiment.objects.filter(pk=self.experiment.ale_id).exists())

    def test_expired_rows_are_removed(self):
        self.experiment.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        self._purge("--older-than", "30")
        self.assertFalse(AleExperiment.objects.filter(pk=self.experiment.ale_id).exists())

    def test_dry_run_changes_nothing(self):
        self.experiment.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        output = self._purge("--older-than", "30", "--dry-run")

        self.assertIn("Would purge", output)
        self.assertTrue(AleExperiment.objects.filter(pk=self.experiment.ale_id).exists())

    def test_purging_a_project_takes_its_experiments(self):
        """AleExperiment.project is DO_NOTHING, so the experiments must go first."""
        self.project.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        self._purge("--older-than", "30")

        self.assertFalse(Project.objects.filter(pk=self.project.id).exists())
        self.assertFalse(AleExperiment.objects.filter(pk=self.experiment.ale_id).exists())

    def test_purge_removes_the_stored_reference_files(self):
        reference_dir = store.ensure_dir(
            store.experiment_reference_dir(self.experiment.ale_id))
        marker = os.path.join(reference_dir, store.REFERENCE_FASTA)
        with open(marker, "w") as handle:
            handle.write(">x\nACGT\n")

        self.experiment.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        self._purge("--older-than", "30")

        self.assertFalse(os.path.exists(marker),
                         "stored files should not outlive the experiment")
