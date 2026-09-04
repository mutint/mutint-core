"""Groups: the model, the pages, and every row of the standing table.

The table these enforce (owner / manager / member) is written out in
`aledb_experiment/group_permissions.py`.
"""

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from aledb_experiment.group_permissions import (
    is_group_manager, is_group_member, is_group_owner, manageable_groups, visible_groups,
)
from aledb_experiment.models import UserGroup, UserGroupMembership, Project, ProjectAccess
from aledb_experiment.permissions import (
    effective_role, grant_project_access, set_primary_owner,
)
from aledb_experiment.roles import ROLE_WRITE


def make_user(username, **kwargs):
    user = User.objects.create(username=username, email="%s@e.com" % username,
                               is_active=True, **kwargs)
    user.set_password("pw")
    user.save()
    return user


class GroupTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.manager = make_user("manager")
        self.member = make_user("member")
        self.stranger = make_user("stranger")

        self.group = UserGroup.objects.create(name="Lab", description="a lab",
                                             owner=self.owner)
        UserGroupMembership.objects.create(group=self.group, user=self.owner,
                                          is_manager=True)
        self.manager_row = UserGroupMembership.objects.create(
            group=self.group, user=self.manager, is_manager=True)
        self.member_row = UserGroupMembership.objects.create(
            group=self.group, user=self.member)

        self.base = "/ale/group/%s/" % self.group.id

    def owner_row(self):
        return UserGroupMembership.objects.get(group=self.group, user=self.owner)


class ModelTestCase(GroupTestCase):
    def test_names_are_unique_ignoring_case(self):
        """Because a group is added to a project by typing its name into a plain box."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserGroup.objects.create(name="lab", owner=self.stranger)

    def test_a_person_holds_at_most_one_membership_row(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserGroupMembership.objects.create(group=self.group, user=self.member)

    def test_member_count(self):
        self.assertEqual(self.group.member_count(), 3)


class StandingTestCase(GroupTestCase):
    def test_the_owner_holds_every_standing(self):
        self.assertTrue(is_group_owner(self.owner, self.group))
        self.assertTrue(is_group_manager(self.owner, self.group))
        self.assertTrue(is_group_member(self.owner, self.group))

    def test_a_manager_is_a_manager_and_a_member_but_not_the_owner(self):
        self.assertFalse(is_group_owner(self.manager, self.group))
        self.assertTrue(is_group_manager(self.manager, self.group))
        self.assertTrue(is_group_member(self.manager, self.group))

    def test_a_plain_member_is_only_a_member(self):
        self.assertFalse(is_group_manager(self.member, self.group))
        self.assertTrue(is_group_member(self.member, self.group))

    def test_a_stranger_holds_nothing(self):
        self.assertFalse(is_group_member(self.stranger, self.group))

    def test_a_superuser_holds_every_standing(self):
        admin = make_user("admin", is_superuser=True)
        self.assertTrue(is_group_owner(admin, self.group))

    def test_visible_groups_is_what_you_belong_to(self):
        self.assertCountEqual(visible_groups(self.member), [self.group])
        self.assertCountEqual(visible_groups(self.stranger), [])

    def test_manageable_groups_is_what_you_manage(self):
        self.assertCountEqual(manageable_groups(self.manager), [self.group])
        self.assertCountEqual(manageable_groups(self.member), [])


class ListAndCreateTestCase(GroupTestCase):
    def test_the_list_is_403_signed_out(self):
        self.assertEqual(self.client.get("/ale/groups/").status_code, 403)

    def test_it_shows_your_groups_and_your_standing(self):
        self.client.force_login(self.manager)
        html = self.client.get("/ale/groups/").content.decode("utf-8")
        self.assertIn("Lab", html)
        self.assertIn("Manager", html)

    def test_it_does_not_show_someone_elses(self):
        self.client.force_login(self.stranger)
        html = self.client.get("/ale/groups/").content.decode("utf-8")
        self.assertNotIn("Lab", html)

    def test_the_new_page_is_403_signed_out(self):
        self.assertEqual(self.client.get("/ale/groups/new/").status_code, 403)

    def test_creating_makes_you_owner_and_a_manager_member(self):
        """The owner's membership row is what lets the permission query reach them."""
        self.client.force_login(self.stranger)
        response = self.client.post("/ale/groups/create/",
                                    {"name": "New Lab", "description": "d"})
        self.assertEqual(response.status_code, 200)
        group = UserGroup.objects.get(pk=response.json()["group_id"])
        self.assertEqual(group.owner_id, self.stranger.id)
        membership = UserGroupMembership.objects.get(group=group, user=self.stranger)
        self.assertTrue(membership.is_manager)

    def test_a_duplicate_name_is_a_message_not_an_integrityerror(self):
        self.client.force_login(self.stranger)
        response = self.client.post("/ale/groups/create/", {"name": "LAB"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("already a group", response.json()["error"])

    def test_a_missing_name_is_400(self):
        self.client.force_login(self.stranger)
        self.assertEqual(
            self.client.post("/ale/groups/create/", {"name": "  "}).status_code, 400)

    def test_signed_out_is_403(self):
        self.assertEqual(
            self.client.post("/ale/groups/create/", {"name": "X"}).status_code, 403)


class DetailPageTestCase(GroupTestCase):
    def test_a_member_sees_it(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.base).status_code, 200)

    def test_a_stranger_does_not(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.base).status_code, 403)

    def test_a_plain_member_gets_no_add_box(self):
        self.client.force_login(self.member)
        html = self.client.get(self.base).content.decode("utf-8")
        self.assertNotIn('id="gd-add"', html)

    def test_a_manager_does(self):
        self.client.force_login(self.manager)
        html = self.client.get(self.base).content.decode("utf-8")
        self.assertIn('id="gd-add"', html)

    def test_only_the_owner_gets_the_delete_and_transfer_controls(self):
        for user, expected in ((self.owner, True), (self.manager, False)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get(self.base).content.decode("utf-8")
                self.assertEqual('id="gd-delete"' in html, expected)
                self.assertEqual('id="gd-transfer-go"' in html, expected)

    def test_it_states_your_own_role_under_the_members_heading(self):
        for user, expected in ((self.owner, "Owner"), (self.manager, "Manager"),
                               (self.member, "Member")):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get(self.base).content.decode("utf-8")
                self.assertIn("Your group role: %s" % expected, html)

    def test_the_transfer_section_says_what_it_does(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.base).content.decode("utf-8")
        self.assertIn("Transfer group ownership to a different user", html)

    def test_it_names_the_projects_the_group_reaches(self):
        project = Project.objects.create(name="Shared", user=self.stranger)
        set_primary_owner(project, self.stranger)
        grant_project_access(project, self.group, ROLE_WRITE)
        self.client.force_login(self.member)
        html = self.client.get(self.base).content.decode("utf-8")
        self.assertIn("Shared", html)


class MembershipTestCase(GroupTestCase):
    def add(self, **data):
        return self.client.post(self.base + "members/add/", data)

    def remove(self, membership):
        return self.client.post(self.base + "members/remove/",
                                {"membership_id": membership.id})

    def test_a_manager_can_add_a_plain_member(self):
        self.client.force_login(self.manager)
        response = self.add(username="stranger")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_manager"])

    def test_a_plain_member_cannot(self):
        self.client.force_login(self.member)
        self.assertEqual(self.add(username="stranger").status_code, 403)

    def test_a_manager_cannot_appoint_a_manager(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.add(username="stranger", is_manager="1").status_code, 403)

    def test_the_owner_can(self):
        self.client.force_login(self.owner)
        response = self.add(username="stranger", is_manager="1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_manager"])

    def test_an_unknown_username_is_400_and_says_the_name(self):
        self.client.force_login(self.owner)
        response = self.add(username="nobody")
        self.assertEqual(response.status_code, 400)
        self.assertIn("nobody", response.json()["error"])

    def test_adding_someone_twice_says_so_rather_than_doing_nothing(self):
        """They typed a name; a silent no-op reads as having worked."""
        self.client.force_login(self.owner)
        response = self.add(username="member")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already in this group", response.json()["error"])

    def test_a_manager_can_remove_a_plain_member(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.remove(self.member_row).status_code, 200)
        self.assertFalse(
            UserGroupMembership.objects.filter(pk=self.member_row.pk).exists())

    def test_a_manager_cannot_remove_another_manager(self):
        second = make_user("second")
        row = UserGroupMembership.objects.create(group=self.group, user=second,
                                                is_manager=True)
        self.client.force_login(self.manager)
        self.assertEqual(self.remove(row).status_code, 403)

    def test_the_owner_can(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.remove(self.manager_row).status_code, 200)

    def test_the_owners_row_can_never_be_removed(self):
        self.client.force_login(self.owner)
        response = self.remove(self.owner_row())
        self.assertEqual(response.status_code, 400)
        self.assertIn("transfer", response.json()["error"])

    def test_only_the_owner_appoints_or_demotes(self):
        url = self.base + "members/update/"
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.post(url, {"membership_id": self.member_row.id,
                                   "is_manager": "1"}).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(url, {"membership_id": self.member_row.id,
                                   "is_manager": "1"}).status_code, 200)
        self.member_row.refresh_from_db()
        self.assertTrue(self.member_row.is_manager)

    def test_the_owner_cannot_be_demoted(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.base + "members/update/",
                                    {"membership_id": self.owner_row().id})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.owner_row().is_manager)

    def test_a_membership_in_another_group_is_404_here(self):
        other = UserGroup.objects.create(name="Other", owner=self.stranger)
        foreign = UserGroupMembership.objects.create(group=other, user=self.stranger,
                                                    is_manager=True)
        self.client.force_login(self.owner)
        self.assertEqual(self.remove(foreign).status_code, 404)


class GroupAccessPropagationTestCase(GroupTestCase):
    """Membership changes move project access with them."""

    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name="P", user=self.stranger)
        set_primary_owner(self.project, self.stranger)
        grant_project_access(self.project, self.group, ROLE_WRITE)

    def test_a_new_member_gains_the_projects(self):
        newcomer = make_user("newcomer")
        self.assertIsNone(effective_role(newcomer, self.project))
        self.client.force_login(self.owner)
        self.client.post(self.base + "members/add/", {"username": "newcomer"})
        self.assertEqual(effective_role(newcomer, self.project), ROLE_WRITE)

    def test_a_removed_member_loses_them(self):
        self.assertEqual(effective_role(self.member, self.project), ROLE_WRITE)
        self.client.force_login(self.owner)
        self.client.post(self.base + "members/remove/",
                         {"membership_id": self.member_row.id})
        self.assertIsNone(effective_role(self.member, self.project))


class UpdateTransferAndDeleteTestCase(GroupTestCase):
    def test_a_manager_can_rename(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.base + "update/",
                                    {"name": "Renamed", "description": "d"})
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Renamed")

    def test_a_plain_member_cannot(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.post(self.base + "update/", {"name": "Renamed"}).status_code, 403)

    def test_renaming_onto_another_groups_name_is_400(self):
        UserGroup.objects.create(name="Taken", owner=self.stranger)
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(self.base + "update/", {"name": "taken"}).status_code, 400)

    def test_renaming_to_its_own_name_is_fine(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(self.base + "update/", {"name": "Lab"}).status_code, 200)

    def test_only_the_owner_transfers(self):
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.post(self.base + "transfer/",
                             {"username": "manager"}).status_code, 403)

    def test_transferring_promotes_the_new_owner_and_keeps_the_old_as_manager(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.base + "transfer/", {"username": "member"})
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.owner_id, self.member.id)
        self.member_row.refresh_from_db()
        self.assertTrue(self.member_row.is_manager)
        self.assertTrue(self.owner_row().is_manager)
        self.assertTrue(is_group_manager(self.owner, self.group))
        self.assertFalse(is_group_owner(self.owner, self.group))

    def test_transferring_to_a_non_member_brings_them_in(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(self.base + "transfer/",
                             {"username": "stranger"}).status_code, 200)
        self.assertTrue(is_group_member(self.stranger, self.group))

    def test_transferring_to_the_current_owner_is_400(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(self.base + "transfer/", {"username": "owner"}).status_code,
            400)

    def test_only_the_owner_deletes(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.base + "delete/").status_code, 403)

    def test_deleting_revokes_every_project_grant_and_reports_the_count(self):
        project = Project.objects.create(name="P", user=self.stranger)
        set_primary_owner(project, self.stranger)
        grant_project_access(project, self.group, ROLE_WRITE)

        self.client.force_login(self.owner)
        response = self.client.post(self.base + "delete/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["projects_affected"], 1)
        self.assertFalse(UserGroup.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(ProjectAccess.objects.filter(project=project,
                                                      group__isnull=False).exists())
        self.assertIsNone(effective_role(self.member, project))
