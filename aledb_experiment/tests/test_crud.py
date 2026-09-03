import os
import shutil
import tempfile
from io import StringIO

from django.contrib.auth.models import AnonymousUser, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from aledb_common import store
from aledb_experiment.models import AleExperiment, Project
from aledb_experiment.permissions import grant_project_access, set_primary_owner
from aledb_experiment.roles import ROLE_READ
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
            "/ale/experiment/%d/delete/" % self.experiment.id)

        self.assertEqual(response.status_code, 200, response.content)
        self.experiment.refresh_from_db()
        self.assertIsNotNone(self.experiment.deleted_at)
        self.assertEqual(self.experiment.deleted_by_id, self.user.id)
        # The row is still there -- that is the whole point.
        self.assertTrue(AleExperiment.objects.filter(pk=self.experiment.id).exists())

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
            self.client.post("/ale/experiment/%d/delete/" % self.experiment.id
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
        url = "/ale/experiment/%d/delete/" % self.experiment.id
        first = self.client.post(url).json()["deleted_at"]
        second = self.client.post(url).json()["deleted_at"]
        self.assertEqual(first, second)

    def test_owner_can_delete_their_own_project(self):
        response = self.client.post("/ale/project/%d/delete/" % self.project.id)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["project_id"], self.project.id)
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.deleted_at)
        self.assertEqual(self.project.deleted_by_id, self.user.id)
        # Soft: the row survives for `purge_deleted` to remove later.
        self.assertTrue(Project.objects.filter(pk=self.project.id).exists())

    def test_project_delete_is_idempotent(self):
        url = "/ale/project/%d/delete/" % self.project.id
        first = self.client.post(url).json()["deleted_at"]
        second = self.client.post(url).json()["deleted_at"]
        self.assertEqual(first, second)


class DeleteControlsTestCase(TestCase):
    """Where the delete controls appear, and which confirm dialog they use.

    Deleting an experiment or a project destroys data; revoking somebody's access or
    removing them from a group does not. The two are behind different dialogs -- the
    first makes you type DELETE -- and this is what keeps them from converging back
    onto one, which is how a guard stops being noticed.
    """

    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        self.experiment = AleExperiment.objects.get(project=self.project)

    def _html(self, url):
        return self.client.get(url).content.decode()

    def _project_page(self):
        return self._html("/ale/project/%d/" % self.project.id)

    # --- the new control on the project page ---------------------------------------

    def test_the_project_page_offers_delete_selected(self):
        self.assertIn('id="delete-selected"', self._project_page())

    def test_a_reader_is_not_offered_it(self):
        """Read access opens the page; it does not offer a way to empty it.

        The endpoint refuses them anyway, so this is not the check that protects the
        data -- it is what stops the page advertising an action that can only 403.
        """
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.project, reader, ROLE_READ, granted_by=self.owner)
        self.client.force_login(reader)
        html = self._project_page()
        self.assertNotIn('id="delete-selected"', html)
        self.assertEqual(200, self.client.get("/ale/project/%d/" % self.project.id).status_code)

    def test_the_handler_survives_the_button_being_gone(self):
        """The script is unconditional and the button is not, so the guard is what
        stands between a reader and a page whose JavaScript throws on load.

        Asserted for a reader rather than signed out: a private project answers 403 to
        an anonymous request, so there would be no script on the page to guard and the
        check would pass by finding nothing.
        """
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.project, reader, ROLE_READ, granted_by=self.owner)
        self.client.force_login(reader)
        html = self._project_page()
        self.assertIn('document.getElementById("delete-selected").addEventListener', html)
        self.assertNotIn('id="delete-selected"', html)
        self.assertIn('if (!document.getElementById("delete-selected")) { return; }', html)

    # --- which dialog each control uses --------------------------------------------

    def test_the_data_deletes_make_you_type_it(self):
        """All four, and each assertion first proves it found its subject.

        A check that looks for a call in a page is one renamed id away from passing
        because it matched nothing at all, which this repo has been bitten by before.

        The three bulk controls reach the typed dialog through aledbDeleteSelected
        rather than naming it, so the last assertion below is what makes that route
        mean what the other three say.
        """
        for url, control, call in (
                ("/ale/projects/", 'id="delete-selected"', "aledbDeleteSelected"),
                ("/ale/experiments/", 'id="delete-selected"', "aledbDeleteSelected"),
                ("/ale/project/%d/" % self.project.id,
                 'id="delete-selected"', "aledbDeleteSelected"),
                ("/stats/?ale_experiment_id=%d" % self.experiment.id,
                 'id="delete-experiment"', "aledbConfirmTypedDelete")):
            with self.subTest(url=url):
                html = self._html(url)
                self.assertIn(control, html)
                self.assertIn(call, html)

        from django.contrib.staticfiles import finders

        with open(finders.find("js/aledb_crud.js"), encoding="utf-8") as handle:
            source = handle.read()
        gather = source[source.index("window.aledbDeleteSelected"):]
        self.assertIn("aledbConfirmTypedDelete", gather)

    def test_revoking_access_stays_a_plain_confirm(self):
        """Removing a grant destroys nothing, and should not feel like it does."""
        html = self._html("/ale/project/%d/access/" % self.project.id)
        self.assertIn("aledbConfirmDelete(", html)
        self.assertNotIn("aledbConfirmTypedDelete", html)

    # --- where a single delete lands -----------------------------------------------

    def test_deleting_the_experiment_you_are_looking_at_returns_to_its_project(self):
        html = self._html("/stats/?ale_experiment_id=%d" % self.experiment.id)
        self.assertIn('data-after-delete="/ale/project/%d/"' % self.project.id, html)

    def test_an_experiment_with_no_project_has_no_delete_button_to_aim(self):
        """Which is why the button's fallback destination is a guard, not a live path.

        `AleExperiment.project` is nullable, so the template has an else-branch pointing
        at the flat list. Nothing can reach it: `effective_role` answers None for a null
        project before it reaches its superuser branch, so a projectless experiment is
        viewable by nobody and this page does not render for one -- superuser included.
        Pinned here so the else-branch is not read later as a case somebody tested.
        """
        self.experiment.project = None
        self.experiment.save()
        self.owner.is_superuser = True
        self.owner.save()
        html = self._html("/stats/?ale_experiment_id=%d" % self.experiment.id)
        self.assertNotIn('id="delete-experiment"', html)
        self.assertIn("permission", html)


class ProjectDetailIsReadOnlyTestCase(TestCase):
    """The project page shows a project; it has never been able to change one.

    The summary was four editable <input>s inside a <form> that posted nowhere, so
    anything typed was silently discarded on reload -- and two of them even shared
    name="user".
    """

    def setUp(self):
        # Via the view because the page under test is the one the view lands on. Direct
        # creation is fine for access now -- `set_primary_owner` is what `project_create`
        # calls, and `Project.user` alone already reads as owner.
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])

    def _html(self):
        return self.client.get("/ale/project/%d/" % self.project.id).content.decode()

    def test_the_summary_has_no_inputs(self):
        summary = self._html().split('id="exp_table"')[0]
        for field in ('id="name"', 'id="date"', 'id="user"', 'id="public"'):
            self.assertNotIn(field, summary)

    def test_it_still_shows_the_values(self):
        html = self._html()
        self.assertIn("Project Name", html)
        self.assertIn("Created Date", html)
        self.assertIn("Owner Name", html)
        self.assertIn("Is public?", html)
        self.assertIn("P", html)

    def test_is_public_reads_as_words_not_a_python_bool(self):
        self.assertIn("<dd>No</dd>", self._html())


class NewExperimentControlTestCase(TestCase):
    """Creating an experiment under a project that already exists.

    `experiment_create` existed and was permission-checked from the start, but nothing in
    the UI called it: the experiments page's "+ New experiment" was a link to /ale/projects/,
    which can only make an experiment alongside a *new* project. There was no way to add a
    second experiment to an existing one.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="owner", email="o@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])

    def test_the_experiments_page_links_to_the_create_page(self):
        """The control is a link to a page of its own, not a dialog over the table."""
        html = self.client.get("/ale/experiments/").content.decode("utf-8")

        self.assertIn('href="/ale/experiments/new/"', html)
        # No longer a link that dead-ends on the project list.
        self.assertNotIn('href="/ale/projects/">+ New experiment', html)

    def test_the_create_page_offers_a_project_to_create_under(self):
        html = self.client.get("/ale/experiments/new/").content.decode("utf-8")

        self.assertIn('id="ne-project"', html)
        self.assertIn("P", html)

    def test_the_project_page_links_to_the_create_page_with_itself_fixed(self):
        html = self.client.get(
            "/ale/project/%d/" % self.project.id).content.decode("utf-8")

        self.assertIn('href="/ale/experiments/new/?project=%d"' % self.project.id, html)

    def test_the_create_page_fixes_the_project_when_given_one(self):
        """Arrived at from a project, the project is settled -- no picker to get wrong."""
        html = self.client.get(
            "/ale/experiments/new/", {"project": self.project.id}).content.decode("utf-8")

        self.assertIn('id="ne-name"', html)
        self.assertIn("P", html)
        self.assertNotIn("<select", html)

    def test_creating_one_returns_it_and_it_shows_up(self):
        response = self.client.post(
            "/ale/experiments/create/", {"project": self.project.id, "name": "second"})

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["experiment"], "second")
        self.assertEqual(body["project_id"], self.project.id)

        experiment = AleExperiment.objects.get(pk=body["experiment_id"])
        self.assertEqual(experiment.project_id, self.project.id)
        self.assertEqual(experiment.person, "owner")
        self.assertIn(experiment, list(self.project.experiments()))

    def test_a_second_experiment_may_share_a_name(self):
        """Experiments are identified by pk, so a duplicate name is allowed."""
        for _ in range(2):
            response = self.client.post(
                "/ale/experiments/create/", {"project": self.project.id, "name": "dup"})
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            AleExperiment.objects.filter(name="dup").count(), 2)

    def test_a_nameless_experiment_is_refused(self):
        response = self.client.post(
            "/ale/experiments/create/", {"project": self.project.id, "name": "   "})
        self.assertEqual(response.status_code, 400)

    def test_a_stranger_cannot_create_under_someone_elses_project(self):
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self.client.post(
            "/ale/experiments/create/", {"project": self.project.id, "name": "sneaky"})
        self.assertEqual(response.status_code, 403)

    def test_a_project_you_cannot_edit_is_not_offered(self):
        """The picker lists only what the POST would accept."""
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True, is_staff=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        # Staff may *view* every project, but may not create under this one, so the
        # experiments page sends them to make one instead.
        html = self.client.get("/ale/experiments/").content.decode("utf-8")
        self.assertNotIn('href="/ale/experiments/new/"', html)
        self.assertIn("+ New project", html)

    def test_the_create_page_refuses_a_project_you_cannot_edit(self):
        """Reachable by URL, so the page checks rather than trusting the link."""
        stranger = User.objects.create(
            username="stranger", email="s2@e.com", is_active=True, is_staff=True)
        self.client.force_login(stranger)

        response = self.client.get("/ale/experiments/new/", {"project": self.project.id})
        self.assertEqual(403, response.status_code)


class ProjectVisibilityTestCase(TestCase):
    """Who sees which projects.

    Every one of these passed vacuously before: the object-permission queries filtered on
    `content_type__app_label='ale'` while the label is `aledb_experiment`, so they matched
    nothing and `get_user_projects` returned every project to everybody.
    """

    def setUp(self):
        self.owner = User.objects.create(
            username="owner", email="o@e.com", is_active=True)
        self.stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True)

        self.private = Project.objects.create(
            name="private", user=self.owner, is_public=False)
        set_primary_owner(self.private, self.owner)
        self.public = Project.objects.create(
            name="public", user=self.owner, is_public=True)

    def test_the_owner_sees_their_granted_project(self):
        self.assertIn(self.private, list(get_user_projects(self.owner)))

    def test_a_stranger_does_not_see_a_private_project(self):
        visible = list(get_user_projects(self.stranger))
        self.assertNotIn(self.private, visible)
        self.assertIn(self.public, visible)

    def test_an_anonymous_user_sees_only_public_projects(self):
        self.assertEqual(list(get_user_projects(AnonymousUser())), [self.public])

    def test_a_superuser_sees_everything(self):
        admin = User.objects.create(
            username="admin", email="a@e.com", is_active=True, is_superuser=True)
        visible = list(get_user_projects(admin))
        self.assertIn(self.private, visible)
        self.assertIn(self.public, visible)

    def test_staff_have_no_blanket_access(self):
        """The inverse of what this asserted before, and the point of explicit roles.

        `can_view_project` used to end `return bool(user.is_staff)`, and `load_projects`
        creates every imported user with `is_staff=True` -- so nearly everyone could read
        nearly everything and every role below admin was decorative.
        """
        staff = User.objects.create(
            username="staff", email="st@e.com", is_active=True, is_staff=True)
        visible = list(get_user_projects(staff))
        self.assertNotIn(self.private, visible)
        self.assertIn(self.public, visible)

    def test_a_project_with_no_access_row_is_still_visible_to_the_user_it_names(self):
        """The old trap, gone: `Project.objects.create()` used to leave the owner locked out.

        `can_view_project` consulted only the guardian grant, never `Project.user`, so a
        project could name an owner who could not open it. `effective_role` reads
        `Project.user` directly now, so a missing grant row is a display bug at worst.
        """
        orphan = Project.objects.create(
            name="ungranted", user=self.owner, is_public=False)
        self.assertIn(orphan, list(get_user_projects(self.owner)))
        self.assertNotIn(orphan, list(get_user_projects(self.stranger)))


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
        self.assertTrue(AleExperiment.objects.filter(pk=self.experiment.id).exists())

    def test_expired_rows_are_removed(self):
        self.experiment.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        self._purge("--older-than", "30")
        self.assertFalse(AleExperiment.objects.filter(pk=self.experiment.id).exists())

    def test_dry_run_changes_nothing(self):
        self.experiment.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        output = self._purge("--older-than", "30", "--dry-run")

        self.assertIn("Would purge", output)
        self.assertTrue(AleExperiment.objects.filter(pk=self.experiment.id).exists())

    def test_purging_a_project_takes_its_experiments(self):
        """AleExperiment.project is DO_NOTHING, so the experiments must go first."""
        self.project.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        self._purge("--older-than", "30")

        self.assertFalse(Project.objects.filter(pk=self.project.id).exists())
        self.assertFalse(AleExperiment.objects.filter(pk=self.experiment.id).exists())

    def test_purge_removes_the_stored_reference_files(self):
        reference_dir = store.ensure_dir(
            store.experiment_reference_dir(self.experiment.id))
        marker = os.path.join(reference_dir, store.REFERENCE_FASTA)
        with open(marker, "w") as handle:
            handle.write(">x\nACGT\n")

        self.experiment.soft_delete(
            self.user, when=timezone.now() - timezone.timedelta(days=40))
        self._purge("--older-than", "30")

        self.assertFalse(os.path.exists(marker),
                         "stored files should not outlive the experiment")


class SignedOutControlsTestCase(TestCase):
    """Signed out, the create/delete controls are not offered at all.

    They were rendered to everyone. Nothing unsafe followed -- every endpoint
    refuses an anonymous caller -- but clicking them could only ever produce a
    403 in the error line, which is a dead end dressed up as an action.
    """

    def setUp(self):
        owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(owner)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        self.client.logout()

    def _html(self, url):
        return self.client.get(url).content.decode()

    def test_the_project_list_offers_nothing(self):
        html = self._html("/ale/projects/")
        self.assertNotIn("+ New project", html)
        self.assertNotIn('id="delete-selected"', html)
        self.assertNotIn('id="new-project-modal"', html)

    def test_the_experiment_list_offers_no_delete(self):
        self.assertNotIn('id="delete-selected"', self._html("/ale/experiments/"))

    def test_the_experiment_list_offers_no_create(self):
        """Neither the create button nor the fallback that used to stand in for it.

        With no editable projects the page offered "+ New project" instead, linking
        to the project list -- which, signed out, no longer offers one either. A
        link to a button that is not there is worse than no link.
        """
        html = self._html("/ale/experiments/")
        self.assertNotIn('data-target="#new-experiment-modal"', html)
        self.assertNotIn("+ New project", html)

    def test_the_pages_still_render(self):
        """Gating hides controls; it must not take the page with it. The delete
        handlers call getElementById(...).addEventListener, which throws on null."""
        for url in ("/ale/projects/", "/ale/experiments/"):
            with self.subTest(url=url):
                self.assertEqual(200, self.client.get(url).status_code)

    def test_every_handler_guards_its_missing_control(self):
        for url in ("/ale/projects/", "/ale/experiments/"):
            with self.subTest(url=url):
                html = self._html(url)
                for call in ('document.getElementById("delete-selected").addEventListener',
                             'document.getElementById("np-save").addEventListener',
                             'document.getElementById("ne-save").addEventListener'):
                    if call in html:
                        control = call.split('"')[1]
                        self.assertIn('if (!document.getElementById("%s")) { return; }' % control,
                                      html)

    def test_signed_in_they_come_back(self):
        self.client.force_login(User.objects.get(username="owner"))
        html = self._html("/ale/projects/")
        self.assertIn("+ New project", html)
        self.assertIn('id="delete-selected"', html)

    def test_the_endpoints_refuse_anonymous_regardless(self):
        """The gating is cosmetic; these are the checks that matter."""
        for url, data in (("/ale/projects/create/", {"name": "x"}),
                          ("/ale/experiments/create/",
                           {"project": self.project.id, "name": "x"}),
                          ("/ale/project/%d/delete/" % self.project.id, {})):
            with self.subTest(url=url):
                self.assertEqual(403, self.client.post(url, data).status_code)


class CreatePagesTestCase(TestCase):
    """Creating is a page now, not a dialog.

    The forms used to be modals over the list tables. A create form wants a
    heading, room to explain its fields and a URL you can link someone to, and
    none of that survives in a dialog over a DataTable -- which is also where the
    overlap and double-toggle bugs came from.
    """

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])

    # --- they exist and render ------------------------------------------------

    def test_the_project_page_renders(self):
        response = self.client.get("/ale/projects/new/")
        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'id="np-name"')
        self.assertContains(response, 'id="np-save"')

    def test_the_experiment_page_renders(self):
        response = self.client.get("/ale/experiments/new/")
        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'id="ne-name"')
        self.assertContains(response, 'id="ne-save"')

    def test_neither_is_a_modal(self):
        for url in ("/ale/projects/new/", "/ale/experiments/new/"):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "data-toggle=\"modal\"")

    def test_both_offer_a_way_back(self):
        for url in ("/ale/projects/new/", "/ale/experiments/new/"):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "Cancel")

    # --- signed out -----------------------------------------------------------

    def test_signed_out_they_are_forbidden(self):
        """Reachable by URL, so each page checks rather than relying on the button
        being hidden."""
        self.client.logout()
        for url in ("/ale/projects/new/", "/ale/experiments/new/"):
            with self.subTest(url=url):
                self.assertEqual(403, self.client.get(url).status_code)

    # --- the experiment page needs somewhere to put it ------------------------

    def test_with_no_editable_project_it_sends_you_to_make_one(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)

        response = self.client.get("/ale/experiments/new/")
        self.assertEqual(302, response.status_code)
        self.assertEqual("/ale/projects/new/", response["Location"])

    def test_creating_still_goes_through_the_same_endpoint(self):
        """The pages are presentation; the permission checks stay on the POST."""
        response = self.client.post(
            "/ale/experiments/create/", {"project": self.project.id, "name": "second"})
        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual("second", response.json()["experiment"])


class ProjectEditTestCase(TestCase):
    """Changing a project, on a page of its own.

    The project summary carried four editable inputs once, inside a form that posted
    nowhere, so everything typed into it was discarded on reload. It is a read-only <dl>
    now -- `ProjectDetailIsReadOnlyTestCase` pins that -- and this is where editing went
    instead.
    """

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])

    def test_the_page_renders_with_the_current_values(self):
        response = self.client.get("/ale/project/%d/edit/" % self.project.id)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'id="pe-name"')
        self.assertContains(response, 'id="pe-save"')
        self.assertContains(response, 'value="P"')

    def test_it_offers_every_status(self):
        """project_create hardcodes "in progress" and never exposes the choices."""
        response = self.client.get("/ale/project/%d/edit/" % self.project.id)
        for value, _label in Project.PROJECT_STATUS:
            self.assertContains(response, 'value="%s"' % value)

    def test_the_detail_page_links_to_it_only_for_someone_who_may_edit(self):
        html = self.client.get("/ale/project/%d/" % self.project.id).content.decode()
        self.assertIn('href="/ale/project/%d/edit/"' % self.project.id, html)

        self.client.logout()
        html = self.client.get("/ale/project/%d/" % self.project.id).content.decode()
        self.assertNotIn('href="/ale/project/%d/edit/"' % self.project.id, html)

    def test_the_summary_shows_what_the_edit_page_can_change(self):
        """Description and status were editable-but-never-displayed before. A save that
        changes one has to be visible somewhere, or it reads as having done nothing."""
        self.client.post("/ale/project/%d/update/" % self.project.id,
                         {"name": "P", "description": "words about it",
                          "status": "completed"})

        html = self.client.get("/ale/project/%d/" % self.project.id).content.decode()
        summary = html.split('id="exp_table"')[0]
        self.assertIn("words about it", summary)
        self.assertIn("Completed", summary)

    def test_saving_changes_the_project(self):
        response = self.client.post(
            "/ale/project/%d/update/" % self.project.id,
            {"name": "Renamed", "description": "now with words",
             "status": "completed", "is_public": "1"})

        self.assertEqual(200, response.status_code, response.content)
        self.project.refresh_from_db()
        self.assertEqual("Renamed", self.project.name)
        self.assertEqual("now with words", self.project.description)
        self.assertEqual("completed", self.project.status)
        self.assertTrue(self.project.is_public)

    def test_unchecking_public_makes_it_private_again(self):
        self.client.post("/ale/project/%d/update/" % self.project.id,
                         {"name": "P", "is_public": "1"})
        self.client.post("/ale/project/%d/update/" % self.project.id, {"name": "P"})

        self.project.refresh_from_db()
        self.assertFalse(self.project.is_public)

    def test_a_blank_name_is_refused(self):
        self.assertEqual(400, self.client.post(
            "/ale/project/%d/update/" % self.project.id, {"name": "   "}).status_code)

    def test_an_unknown_status_is_refused(self):
        self.assertEqual(400, self.client.post(
            "/ale/project/%d/update/" % self.project.id,
            {"name": "P", "status": "abandoned"}).status_code)

    def test_an_over_long_name_is_refused_rather_than_truncated(self):
        """CharField(max_length=50). SQLite would accept it, so the check has to be ours."""
        self.assertEqual(400, self.client.post(
            "/ale/project/%d/update/" % self.project.id,
            {"name": "x" * 51}).status_code)

    def test_signed_out_the_page_and_the_endpoint_both_refuse(self):
        self.client.logout()
        self.assertEqual(403, self.client.get(
            "/ale/project/%d/edit/" % self.project.id).status_code)
        self.assertEqual(403, self.client.post(
            "/ale/project/%d/update/" % self.project.id, {"name": "x"}).status_code)

    def test_staff_may_view_but_not_edit(self):
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True, is_staff=True)
        self.client.force_login(stranger)

        self.assertEqual(403, self.client.get(
            "/ale/project/%d/edit/" % self.project.id).status_code)
        self.assertEqual(403, self.client.post(
            "/ale/project/%d/update/" % self.project.id, {"name": "x"}).status_code)


class ExperimentEditTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "first"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

    def test_the_page_renders_with_the_current_values(self):
        response = self.client.get("/ale/experiment/%d/edit/" % self.experiment.id)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'id="ee-name"')
        self.assertContains(response, 'id="ee-notes"')
        self.assertContains(response, 'id="ee-save"')

    def test_saving_changes_the_experiment(self):
        response = self.client.post(
            "/ale/experiment/%d/update/" % self.experiment.id,
            {"name": "Ara-1", "notes": "ran hot",
             "doi": "10.1/abc 10.1/def", "project": self.project.id})

        self.assertEqual(200, response.status_code, response.content)
        self.experiment.refresh_from_db()
        self.assertEqual("Ara-1", self.experiment.name)
        self.assertEqual("ran hot", self.experiment.notes)
        self.assertEqual(["10.1/abc", "10.1/def"], self.experiment.doi_as_list())

    def test_the_form_neither_offers_nor_accepts_a_person(self):
        """Changing an owner is its own workflow. A details form that carried the field
        would rewrite it on every save, and one that dropped it would blank it."""
        self.assertNotContains(
            self.client.get("/ale/experiment/%d/edit/" % self.experiment.id),
            'id="ee-person"')

        self.client.post("/ale/experiment/%d/update/" % self.experiment.id,
                         {"name": "first", "person": "impostor"})

        self.experiment.refresh_from_db()
        self.assertEqual("owner", self.experiment.person)

    def test_it_moves_between_projects_you_own(self):
        other = Project.objects.get(pk=self.client.post(
            "/ale/projects/create/", {"name": "Q"}).json()["project_id"])

        response = self.client.post(
            "/ale/experiment/%d/update/" % self.experiment.id,
            {"name": "first", "project": other.id})

        self.assertEqual(200, response.status_code, response.content)
        self.experiment.refresh_from_db()
        self.assertEqual(other.id, self.experiment.project_id)
        self.assertNotIn(self.experiment, list(self.project.experiments()))
        self.assertIn(self.experiment, list(other.experiments()))

    def test_it_cannot_be_pushed_into_a_project_you_do_not_own(self):
        """Checking only the source would let you put your experiment in someone
        else's project."""
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True)
        theirs = Project.objects.create(name="Theirs", user=stranger)

        response = self.client.post(
            "/ale/experiment/%d/update/" % self.experiment.id,
            {"name": "first", "project": theirs.id})

        self.assertEqual(403, response.status_code)
        self.experiment.refresh_from_db()
        self.assertEqual(self.project.id, self.experiment.project_id)

    def test_the_picker_offers_only_projects_you_may_edit(self):
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True)
        Project.objects.create(name="NotYours", user=stranger)

        html = self.client.get(
            "/ale/experiment/%d/edit/" % self.experiment.id).content.decode()

        self.assertIn("P", html)
        self.assertNotIn("NotYours", html)

    def test_a_blank_name_is_refused(self):
        self.assertEqual(400, self.client.post(
            "/ale/experiment/%d/update/" % self.experiment.id,
            {"name": "  "}).status_code)

    def test_signed_out_the_page_and_the_endpoint_both_refuse(self):
        self.client.logout()
        self.assertEqual(403, self.client.get(
            "/ale/experiment/%d/edit/" % self.experiment.id).status_code)
        self.assertEqual(403, self.client.post(
            "/ale/experiment/%d/update/" % self.experiment.id,
            {"name": "x"}).status_code)

    def test_the_experiment_page_offers_the_controls_only_to_an_owner(self):
        """The action block used to render for everyone. Every endpoint behind it refuses
        a caller who may not edit, so nothing unsafe followed -- but clicking one could
        only ever produce a 403, which is a dead end dressed up as an action."""
        # follow=True: /stats has no trailing slash, so APPEND_SLASH redirects first and
        # an unfollowed GET returns an empty 301 body.
        url = "/stats?ale_experiment_id=%d" % self.experiment.id
        html = self.client.get(url, follow=True).content.decode()
        self.assertIn('href="/ale/experiment/%d/edit/"' % self.experiment.id, html)
        self.assertIn('href="/ale/experiment/%d/samples/"' % self.experiment.id, html)
        self.assertIn('id="delete-experiment"', html)

        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True, is_staff=True)
        self.client.force_login(stranger)
        html = self.client.get(url, follow=True).content.decode()
        self.assertNotIn('id="delete-experiment"', html)
        self.assertNotIn("/edit/", html)
