"""The sharing page and its two endpoints.

The end-to-end test at the bottom is the one that proves the feature: a stranger granted
`write` can actually create an experiment under the project and open its page.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import AleGroup, AleGroupMembership, Project, ProjectAccess
from aledb_experiment.permissions import grant_project_access, set_primary_owner
from aledb_experiment.roles import ROLE_ADMIN, ROLE_OWNER, ROLE_READ, ROLE_WRITE


def make_user(username, **kwargs):
    user = User.objects.create(username=username, email="%s@e.com" % username,
                               is_active=True, **kwargs)
    user.set_password("pw")
    user.save()
    return user


class AccessTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.admin = make_user("admin")
        self.writer = make_user("writer")
        self.reader = make_user("reader")
        self.stranger = make_user("stranger")

        self.project = Project.objects.create(name="P", user=self.owner, is_public=False)
        set_primary_owner(self.project, self.owner)
        grant_project_access(self.project, self.admin, ROLE_ADMIN)
        grant_project_access(self.project, self.writer, ROLE_WRITE)
        grant_project_access(self.project, self.reader, ROLE_READ)

        self.page = "/ale/project/%s/access/" % self.project.id
        self.grant_url = self.page + "grant/"
        self.revoke_url = self.page + "revoke/"

    def grant(self, **data):
        return self.client.post(self.grant_url, data)

    def entry_for(self, user):
        return ProjectAccess.objects.get(project=self.project, user=user)


class PageTestCase(AccessTestCase):
    def test_signed_out_is_403(self):
        self.assertEqual(self.client.get(self.page).status_code, 403)

    def test_a_reader_and_a_writer_are_403(self):
        for user in (self.reader, self.writer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.page).status_code, 403)

    def test_an_admin_and_an_owner_get_the_page(self):
        for user in (self.admin, self.owner):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.page).status_code, 200)

    def test_it_lists_the_existing_grants(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.page).content.decode("utf-8")
        for name in ("owner", "admin", "writer", "reader"):
            self.assertIn(name, html)

    def test_an_admin_is_not_offered_owner_in_the_add_dropdown(self):
        """The page must not show a control whose every use the server would refuse."""
        self.client.force_login(self.admin)
        html = self.client.get(self.page).content.decode("utf-8")
        add_block = html.split('id="pa-user-role"')[1].split("</select>")[0]
        self.assertNotIn('value="owner"', add_block)

    def test_an_owner_is(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.page).content.decode("utf-8")
        add_block = html.split('id="pa-user-role"')[1].split("</select>")[0]
        self.assertIn('value="owner"', add_block)

    def test_a_group_is_never_offered_owner(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.page).content.decode("utf-8")
        group_block = html.split('id="pa-group-role"')[1].split("</select>")[0]
        self.assertNotIn('value="owner"', group_block)

    def test_an_admin_sees_the_owner_row_as_text_not_a_dropdown(self):
        self.client.force_login(self.admin)
        html = self.client.get(self.page).content.decode("utf-8")
        self.assertNotIn('id="pa-role-%s"' % self.entry_for(self.owner).id, html)
        self.assertIn('id="pa-role-%s"' % self.entry_for(self.reader).id, html)

    def test_a_public_project_says_so(self):
        self.project.is_public = True
        self.project.save(update_fields=["is_public"])
        self.client.force_login(self.owner)
        html = self.client.get(self.page).content.decode("utf-8")
        self.assertIn("public", html)

    def test_the_script_guards_on_its_own_element(self):
        """House style, and what keeps base.html's shared JS from firing on other pages."""
        self.client.force_login(self.owner)
        html = self.client.get(self.page).content.decode("utf-8")
        self.assertIn('if (!document.getElementById("pa-add-user")) { return; }', html)

    def test_the_sharing_button_is_on_the_project_page_for_an_admin_only(self):
        for user, expected in ((self.admin, True), (self.writer, False)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get(
                    "/ale/project/%s/" % self.project.id).content.decode("utf-8")
                self.assertEqual(self.page in html, expected)


class GrantTestCase(AccessTestCase):
    def test_signed_out_is_403_json(self):
        response = self.grant(username="stranger", role=ROLE_READ)
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_a_writer_cannot_grant(self):
        self.client.force_login(self.writer)
        self.assertEqual(self.grant(username="stranger", role=ROLE_READ).status_code, 403)

    def test_an_admin_can_grant_read(self):
        self.client.force_login(self.admin)
        response = self.grant(username="stranger", role=ROLE_READ)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subject"], "stranger")
        self.assertEqual(self.entry_for(self.stranger).role, ROLE_READ)

    def test_an_admin_cannot_grant_owner(self):
        self.client.force_login(self.admin)
        response = self.grant(username="stranger", role=ROLE_OWNER)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectAccess.objects.filter(project=self.project,
                                                      user=self.stranger).exists())

    def test_an_owner_can(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.grant(username="stranger", role=ROLE_OWNER).status_code, 200)
        self.assertEqual(self.entry_for(self.stranger).role, ROLE_OWNER)

    def test_an_unknown_username_is_400_and_says_the_name(self):
        self.client.force_login(self.owner)
        response = self.grant(username="nobody", role=ROLE_READ)
        self.assertEqual(response.status_code, 400)
        self.assertIn("nobody", response.json()["error"])

    def test_naming_both_a_user_and_a_group_is_400(self):
        self.client.force_login(self.owner)
        response = self.grant(username="stranger", group="Lab", role=ROLE_READ)
        self.assertEqual(response.status_code, 400)

    def test_naming_neither_is_400(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.grant(role=ROLE_READ).status_code, 400)

    def test_an_unknown_role_is_400(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.grant(username="stranger", role="wizard").status_code, 400)

    def test_a_regrant_changes_the_row_rather_than_adding_one(self):
        self.client.force_login(self.owner)
        self.grant(username="reader", role=ROLE_ADMIN)
        self.assertEqual(
            ProjectAccess.objects.filter(project=self.project, user=self.reader).count(), 1)
        self.assertEqual(self.entry_for(self.reader).role, ROLE_ADMIN)

    def test_demoting_the_last_owner_is_refused(self):
        self.client.force_login(self.owner)
        response = self.grant(username="owner", role=ROLE_ADMIN)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.entry_for(self.owner).role, ROLE_OWNER)

    def test_it_records_who_granted(self):
        self.client.force_login(self.admin)
        self.grant(username="stranger", role=ROLE_READ)
        self.assertEqual(self.entry_for(self.stranger).granted_by_id, self.admin.id)


class GroupGrantTestCase(AccessTestCase):
    def setUp(self):
        super().setUp()
        self.group = AleGroup.objects.create(name="Lab", owner=self.admin)
        AleGroupMembership.objects.create(group=self.group, user=self.admin,
                                          is_manager=True)
        AleGroupMembership.objects.create(group=self.group, user=self.stranger)

    def test_an_admin_can_grant_a_group_they_belong_to(self):
        self.client.force_login(self.admin)
        response = self.grant(group="Lab", role=ROLE_WRITE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kind"], "group")

    def test_and_the_members_get_the_access(self):
        from aledb_experiment.permissions import effective_role
        self.client.force_login(self.admin)
        self.grant(group="Lab", role=ROLE_WRITE)
        self.assertEqual(effective_role(self.stranger, self.project), ROLE_WRITE)

    def test_a_group_you_do_not_belong_to_reads_as_absent(self):
        """Anti-enumeration: the same message either way, so the box is not an oracle."""
        AleGroup.objects.create(name="Secret", owner=self.reader)
        self.client.force_login(self.owner)
        present = self.grant(group="Secret", role=ROLE_READ)
        absent = self.grant(group="No Such Group", role=ROLE_READ)
        self.assertEqual(present.status_code, 400)
        self.assertEqual(absent.status_code, 400)
        self.assertEqual(present.json()["error"].replace("Secret", "No Such Group"),
                         absent.json()["error"])

    def test_a_group_cannot_be_granted_owner(self):
        self.client.force_login(self.owner)
        AleGroupMembership.objects.create(group=self.group, user=self.owner)
        response = self.grant(group="Lab", role=ROLE_OWNER)
        self.assertEqual(response.status_code, 400)


class RevokeTestCase(AccessTestCase):
    def revoke(self, entry):
        return self.client.post(self.revoke_url, {"access_id": entry.id})

    def test_an_admin_can_revoke_a_reader(self):
        self.client.force_login(self.admin)
        response = self.revoke(self.entry_for(self.reader))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectAccess.objects.filter(project=self.project,
                                                      user=self.reader).exists())

    def test_a_writer_cannot_revoke(self):
        self.client.force_login(self.writer)
        self.assertEqual(self.revoke(self.entry_for(self.reader)).status_code, 403)

    def test_an_admin_cannot_revoke_an_owner(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.revoke(self.entry_for(self.owner)).status_code, 403)

    def test_the_last_owner_cannot_be_revoked_even_by_themselves(self):
        self.client.force_login(self.owner)
        response = self.revoke(self.entry_for(self.owner))
        self.assertEqual(response.status_code, 400)
        self.assertIn("owner", response.json()["error"])

    def test_revoking_the_primary_owner_repoints_project_user(self):
        grant_project_access(self.project, self.stranger, ROLE_OWNER)
        self.client.force_login(self.owner)
        response = self.revoke(self.entry_for(self.owner))
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.user_id, self.stranger.id)
        self.assertEqual(response.json()["owner"], "stranger")

    def test_an_unknown_access_id_is_404(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(self.revoke_url, {"access_id": 99999}).status_code, 404)

    def test_a_grant_on_another_project_cannot_be_revoked_through_this_one(self):
        other = Project.objects.create(name="other", user=self.stranger)
        set_primary_owner(other, self.stranger)
        foreign = ProjectAccess.objects.get(project=other, user=self.stranger)
        self.client.force_login(self.owner)
        self.assertEqual(self.revoke(foreign).status_code, 404)


class EndToEndTestCase(AccessTestCase):
    """Granting write actually lets someone work in the project. The point of all of it."""

    def test_a_stranger_granted_write_can_use_the_project(self):
        self.client.force_login(self.stranger)
        self.assertEqual(
            self.client.get("/ale/project/%s/" % self.project.id).status_code, 403)

        self.client.force_login(self.owner)
        self.assertEqual(self.grant(username="stranger", role=ROLE_WRITE).status_code, 200)

        self.client.force_login(self.stranger)
        self.assertEqual(
            self.client.get("/ale/project/%s/" % self.project.id).status_code, 200)
        created = self.client.post("/ale/experiments/create/",
                                   {"name": "E", "project": self.project.id})
        self.assertEqual(created.status_code, 200)
        self.assertIn("experiment_id", created.json())

    def test_but_not_delete_it(self):
        """Deleting moved to admin when editing widened to write."""
        self.client.force_login(self.owner)
        self.grant(username="stranger", role=ROLE_WRITE)
        self.client.force_login(self.stranger)
        response = self.client.post("/ale/project/%s/delete/" % self.project.id)
        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.deleted_at)

    def test_an_admin_can_delete_it(self):
        self.client.force_login(self.admin)
        response = self.client.post("/ale/project/%s/delete/" % self.project.id)
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.deleted_at)

    def test_a_reader_cannot_create_an_experiment(self):
        self.client.force_login(self.reader)
        response = self.client.post("/ale/experiments/create/",
                                    {"name": "E", "project": self.project.id})
        self.assertEqual(response.status_code, 403)
