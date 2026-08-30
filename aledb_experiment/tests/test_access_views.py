"""The sharing page and its two endpoints.

The end-to-end test at the bottom is the one that proves the feature: a stranger granted
`write` can actually create an experiment under the project and open its page.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import AleGroup, AleGroupMembership, Project, ProjectAccess
from aledb_experiment.permissions import (
    effective_role, grant_project_access, set_primary_owner,
)
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

    def test_it_states_your_own_role_above_the_table(self):
        for user, expected in ((self.owner, "Owner"), (self.admin, "Admin")):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get(self.page).content.decode("utf-8")
                self.assertIn("Your project role: %s" % expected, html)
                above, below = html.split("<h4>Who has access</h4>")
                self.assertIn("Your project role:", below.split("<table")[0])

    def test_an_owners_own_row_has_no_remove_button(self):
        """The dropdown stays -- stepping down is the transfer -- and the button goes."""
        self.client.force_login(self.owner)
        row = self._row_html(self.owner)
        self.assertIn("pa-role", row)
        self.assertNotIn("pa-remove", row)

    def test_an_admins_own_row_offers_leave_rather_than_remove(self):
        self.client.force_login(self.admin)
        row = self._row_html(self.admin)
        self.assertIn("pa-remove", row)
        self.assertIn(">Leave<", row)

    def test_somebody_elses_row_still_says_remove(self):
        self.client.force_login(self.admin)
        row = self._row_html(self.reader)
        self.assertIn(">Remove<", row)

    def _row_html(self, user):
        entry = self.entry_for(user)
        html = self.client.get(self.page).content.decode("utf-8")
        return html.split('<tr data-access-id="%s">' % entry.id)[1].split("</tr>")[0]

    def test_the_groups_you_can_add_are_named_and_managing_them_is_a_button(self):
        group = AleGroup.objects.create(name="lab", owner=self.owner)
        AleGroupMembership.objects.create(group=group, user=self.owner, is_manager=True)
        self.client.force_login(self.owner)
        html = self.client.get(self.page).content.decode("utf-8")
        self.assertIn("You can add these groups:", html)
        self.assertIn('class="btn btn-default btn-sm" href="/ale/groups/">Manage Groups', html)

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

    def test_the_sole_owner_removing_themselves_is_refused_as_a_self_removal(self):
        """403 from `self_revoke_refusal`, before the last-owner invariant is reached.

        Which of the two answers first stops being visible once an owner may not remove their
        own row at all -- the invariant is what refuses somebody *else* doing it, below.
        """
        self.client.force_login(self.owner)
        response = self.revoke(self.entry_for(self.owner))
        self.assertEqual(response.status_code, 403)
        self.assertIn("owner", response.json()["error"])

    def test_the_last_owner_cannot_be_revoked_by_anyone_else_either(self):
        superuser = make_user("root", is_superuser=True)
        self.client.force_login(superuser)
        response = self.revoke(self.entry_for(self.owner))
        self.assertEqual(response.status_code, 400)
        self.assertIn("must have an owner", response.json()["error"])

    def test_revoking_the_primary_owner_repoints_project_user(self):
        """Removed by the *other* owner: an owner cannot remove their own row."""
        grant_project_access(self.project, self.stranger, ROLE_OWNER)
        self.client.force_login(self.stranger)
        response = self.revoke(self.entry_for(self.owner))
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.user_id, self.stranger.id)
        self.assertEqual(response.json()["owner"], "stranger")

    def test_an_owner_cannot_remove_themselves_even_with_a_second_owner(self):
        """The last-owner invariant is not what stops this, so a second owner does not lift it.

        Leaving is a transfer: hand ownership over, step down, then go.
        """
        grant_project_access(self.project, self.stranger, ROLE_OWNER)
        self.client.force_login(self.owner)
        response = self.revoke(self.entry_for(self.owner))
        self.assertEqual(response.status_code, 403)
        self.assertIn("owner", response.json()["error"])
        self.assertTrue(ProjectAccess.objects.filter(project=self.project,
                                                     user=self.owner).exists())

    def test_an_admin_can_remove_themselves(self):
        self.client.force_login(self.admin)
        response = self.revoke(self.entry_for(self.admin))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectAccess.objects.filter(project=self.project,
                                                      user=self.admin).exists())

    def test_the_transfer_path_an_owner_is_left_with(self):
        """Grant ownership, step down, leave -- each step through the endpoints themselves.

        Every step is checked, not just the last. This test used to assert only the final
        state, and walked straight through a middle step that did nothing: stepping down left
        `Project.user` naming the outgoing owner, so `effective_role` went on answering owner
        for them and the third step was what silently repaired it. Someone who stops after
        step two -- which is all the refusal message asks of them -- was still an owner.
        """
        self.client.force_login(self.owner)

        self.assertEqual(self.grant(username="stranger", role=ROLE_OWNER).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.user_id, self.owner.id,
                         "a second owner must not take the primary flag by itself")

        self.assertEqual(self.grant(username="owner", role=ROLE_ADMIN).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.user_id, self.stranger.id,
                         "stepping down has to move Project.user, or it does nothing")
        self.assertEqual(effective_role(self.owner, self.project), ROLE_ADMIN)

        self.assertEqual(self.revoke(self.entry_for(self.owner)).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.user_id, self.stranger.id)
        self.assertIsNone(effective_role(self.owner, self.project))

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


class BulkTestCase(AccessTestCase):
    """Applying one role to several subjects in a single request.

    Deliberately partial, unlike the sample table's all-or-nothing save: grants are
    independent of each other, so what can be applied is, and what cannot is named. The
    ordering assertion in `test_a_batch_cannot_demote_the_last_owner` is the one that matters
    -- the last-owner check reads the database each call, so a batch validated up front and
    applied afterwards would pass every individual check against the starting state.
    """

    def setUp(self):
        super().setUp()
        self.bulk_url = self.page + "bulk/"
        self.client.force_login(self.owner)

    def ids_for(self, *users):
        return list(ProjectAccess.objects.filter(
            project=self.project, user__in=users).values_list("id", flat=True))

    def bulk(self, **data):
        data.setdefault("role", ROLE_READ)
        return self.client.post(self.bulk_url, data)

    def role_of(self, user):
        return ProjectAccess.objects.get(project=self.project, user=user).role

    # --- the happy path -------------------------------------------------------------------

    def test_one_role_lands_on_several_rows(self):
        import json

        response = self.bulk(
            access_ids=json.dumps(self.ids_for(self.writer, self.reader)), role=ROLE_ADMIN)

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()["applied"])
        self.assertEqual(ROLE_ADMIN, self.role_of(self.writer))
        self.assertEqual(ROLE_ADMIN, self.role_of(self.reader))

    def test_several_people_are_added_at_once(self):
        one = make_user("newbie1")
        two = make_user("newbie2")

        body = self.bulk(usernames="newbie1\nnewbie2", role=ROLE_WRITE).json()

        self.assertEqual(2, body["added"])
        self.assertEqual({}, body["errors"])
        self.assertEqual(ROLE_WRITE, self.role_of(one))
        self.assertEqual(ROLE_WRITE, self.role_of(two))

    def test_blank_lines_are_ignored(self):
        make_user("newbie1")
        body = self.bulk(usernames="\n  newbie1  \n\n", role=ROLE_READ).json()
        self.assertEqual(1, body["added"])

    def test_rows_and_names_can_be_mixed(self):
        import json

        make_user("newbie1")
        body = self.bulk(access_ids=json.dumps(self.ids_for(self.reader)),
                         usernames="newbie1", role=ROLE_WRITE).json()

        self.assertEqual(2, body["applied"])
        self.assertEqual(1, body["added"], "only the new one counts as added")

    # --- partial success ------------------------------------------------------------------

    def test_an_unknown_name_is_reported_and_the_rest_still_land(self):
        make_user("newbie1")

        body = self.bulk(usernames="newbie1\nnosuchperson", role=ROLE_READ).json()

        self.assertEqual(1, body["applied"])
        self.assertIn("nosuchperson", body["errors"])
        self.assertIn("no user named", body["errors"]["nosuchperson"])

    def test_a_batch_cannot_demote_the_last_owner(self):
        """The one cross-row dependency, and why the loop applies sequentially.

        `_remaining_owner_count` reads the database each call. Validated up front against the
        starting state, both of these would look fine and the project would end up ownerless.
        """
        import json

        grant_project_access(self.project, self.admin, ROLE_OWNER)
        owners = self.ids_for(self.owner, self.admin)

        body = self.bulk(access_ids=json.dumps(owners), role=ROLE_READ).json()

        self.assertEqual(1, body["applied"], "the first demotion is allowed")
        self.assertEqual(1, len(body["errors"]), "the second is refused")
        self.assertEqual(
            1, ProjectAccess.objects.filter(project=self.project, role=ROLE_OWNER).count(),
            "the project still has an owner")

    def test_a_grant_from_another_project_is_refused(self):
        import json

        other = Project.objects.create(name="Other", user=self.stranger)
        set_primary_owner(other, self.stranger)
        foreign = ProjectAccess.objects.get(project=other, user=self.stranger)

        body = self.bulk(access_ids=json.dumps([foreign.id]), role=ROLE_READ).json()

        self.assertEqual(0, body["applied"])
        self.assertIn(str(foreign.id), body["errors"])
        self.assertEqual(ROLE_OWNER, ProjectAccess.objects.get(pk=foreign.pk).role)

    def test_a_row_above_the_actors_role_is_skipped(self):
        """The page renders those as text rather than a dropdown; a bulk apply must not
        quietly include what the page would not offer."""
        import json

        self.client.force_login(self.admin)
        owner_row = self.ids_for(self.owner)

        body = self.bulk(access_ids=json.dumps(owner_row), role=ROLE_READ).json()

        self.assertEqual(0, body["applied"])
        self.assertIn("owner", body["errors"])
        self.assertEqual(ROLE_OWNER, self.role_of(self.owner))

    def test_the_same_subject_twice_collapses_to_one_grant(self):
        """ProjectAccess is conditionally unique per subject, and naming someone in both the
        ticked rows and the add box is the obvious way to do it by accident."""
        import json

        body = self.bulk(access_ids=json.dumps(self.ids_for(self.reader)),
                         usernames="reader", role=ROLE_WRITE).json()

        self.assertEqual(1, body["applied"])
        self.assertEqual(
            1, ProjectAccess.objects.filter(project=self.project, user=self.reader).count())

    # --- refusals -------------------------------------------------------------------------

    def test_a_writer_cannot_bulk_apply(self):
        import json

        self.client.force_login(self.writer)
        response = self.bulk(access_ids=json.dumps(self.ids_for(self.reader)))

        self.assertEqual(403, response.status_code)
        self.assertEqual(ROLE_READ, self.role_of(self.reader))

    def test_nobody_can_grant_above_their_own_role(self):
        import json

        self.client.force_login(self.admin)
        response = self.bulk(access_ids=json.dumps(self.ids_for(self.reader)),
                             role=ROLE_OWNER)

        self.assertEqual(403, response.status_code)
        self.assertIn("above your own", response.json()["error"])

    def test_an_empty_batch_is_refused(self):
        self.assertEqual(400, self.bulk(access_ids="[]", usernames="").status_code)

    def test_an_unknown_role_is_refused(self):
        import json

        response = self.bulk(access_ids=json.dumps(self.ids_for(self.reader)), role="king")
        self.assertEqual(400, response.status_code)

    def test_malformed_ids_are_a_400_not_a_500(self):
        self.assertEqual(400, self.bulk(access_ids="not json").status_code)

    def test_it_refuses_a_GET(self):
        self.assertEqual(405, self.client.get(self.bulk_url).status_code)

    def test_anonymous_is_refused(self):
        import json

        self.client.logout()
        response = self.bulk(access_ids=json.dumps(self.ids_for(self.reader)))
        self.assertEqual(403, response.status_code)


class BulkPageTestCase(AccessTestCase):
    """What the page renders for the bulk controls."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_page_offers_a_checkbox_per_editable_row(self):
        html = self.client.get(self.page).content.decode()

        self.assertIn('id="pa-select-all"', html)
        self.assertIn('class="pa-pick"', html)
        self.assertIn('id="pa-apply"', html)

    def test_the_username_box_takes_several(self):
        html = self.client.get(self.page).content.decode()
        self.assertIn("one per line", html)

    def test_a_refused_role_change_no_longer_reloads_the_page(self):
        """It used to `fail(err); reload();`, which wiped the refusal a moment after showing
        it -- so a declined change looked like one that had simply not taken."""
        html = self.client.get(self.page).content.decode()
        self.assertNotIn("fail(err);\n                reload();", html)

    def test_a_partial_apply_moves_the_rows_that_succeeded(self):
        """The page deliberately does not reload on a partial apply, so the per-subject
        refusals stay readable. That left the rows that *did* change still showing their old
        role, under a line reading "Applied to 7" -- the page contradicting itself.

        This asserts only that the handler is wired, because the behaviour itself is not
        reachable from a Django test: it needs a browser and a resolved fetch. Measured in
        headless Chrome, four rows ticked and the first refused by the server:

            before= admin:admin, owner:owner,  reader:read,  writer:write
            after=  admin:admin, owner:admin, reader:admin, writer:admin
            message="Applied to 3, refused 1:"

        The refused row keeps what it still has, the three that applied follow, and the
        message stays on screen.
        """
        html = self.client.get(self.page).content.decode()

        self.assertIn("showAppliedRole", html)
        self.assertIn("if (data.role) { showAppliedRole(data.role, errors); }", html)
        # The two guards that make it correct rather than merely present: a row nobody ticked
        # was not part of the apply, and a refused one did not move.
        self.assertIn('if (!row.querySelector(".pa-pick:checked")) { return; }', html)
        self.assertIn('if (errors[select.getAttribute("data-subject")]) { return; }', html)
