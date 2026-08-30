"""`/admin/` cannot express an ownership state the application forbids.

`Project.user` and an owner `ProjectAccess` row are two statements of one fact, and
`effective_role` grants owner from either alone. A plain `ModelAdmin` writes one and not the
other, which is the desync in its most durable form: the sharing page cannot show the new owner
and cannot take the old one's ownership away.

Driven through the real `ModelAdmin` hooks rather than through HTTP -- what is being pinned is
that the admin routes to `set_primary_owner` / `grant_project_access` / `revoke_project_access`,
not Django's own form plumbing.
"""

from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from aledb_experiment.admin import ProjectAccessAdmin, ProjectAdmin
from aledb_experiment.models import Project, ProjectAccess
from aledb_experiment.permissions import (
    ROLE_ADMIN, ROLE_OWNER, ROLE_READ, effective_role, grant_project_access, set_primary_owner,
)


def make_user(username):
    return User.objects.create(username=username, email=username + "@e.com", is_active=True)


class AdminOwnershipTestCase(TestCase):

    def setUp(self):
        self.staff = make_user("staff")
        self.owner = make_user("owner")
        self.second = make_user("second")
        self.project = Project.objects.create(name="P", user=self.owner)
        set_primary_owner(self.project, self.owner)

        self.request = RequestFactory().post("/admin/")
        self.request.user = self.staff
        # message_user writes to the messages framework, which a bare RequestFactory request
        # has no storage for; the refusal path is what needs it.
        self.messages = []
        self.request._messages = type(
            "Capture", (), {"add": lambda _self, level, message, extra_tags="":
                            self.messages.append(message)})()

        self.project_admin = ProjectAdmin(Project, django_admin.site)
        self.access_admin = ProjectAccessAdmin(ProjectAccess, django_admin.site)

    def _save_project(self):
        self.project_admin.save_model(self.request, self.project, form=None, change=True)

    def test_repointing_project_user_writes_the_owner_grant(self):
        """The plain ModelAdmin wrote the field alone, leaving an owner with no row."""
        self.project.user = self.second
        self._save_project()

        entry = ProjectAccess.objects.get(project=self.project, user=self.second)
        self.assertEqual(ROLE_OWNER, entry.role)
        self.assertEqual(ROLE_OWNER, effective_role(self.second, self.project))

    def test_creating_a_project_here_gives_its_owner_a_grant_too(self):
        fresh = Project.objects.create(name="Fresh", user=self.second)
        self.project_admin.save_model(self.request, fresh, form=None, change=False)

        self.assertTrue(ProjectAccess.objects.filter(
            project=fresh, user=self.second, role=ROLE_OWNER).exists())

    def test_deleting_the_last_owners_grant_is_refused_with_a_message(self):
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)

        self.access_admin.delete_model(self.request, entry)

        self.assertTrue(ProjectAccess.objects.filter(pk=entry.pk).exists())
        self.assertTrue(any("must have an owner" in m for m in self.messages), self.messages)

    def test_deleting_the_primary_owners_grant_repoints_project_user(self):
        grant_project_access(self.project, self.second, ROLE_OWNER)
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)

        self.access_admin.delete_model(self.request, entry)

        self.project.refresh_from_db()
        self.assertEqual(self.second.id, self.project.user_id)

    def test_a_bulk_delete_still_stops_at_the_last_owner(self):
        grant_project_access(self.project, self.second, ROLE_OWNER)
        queryset = ProjectAccess.objects.filter(project=self.project, role=ROLE_OWNER)

        self.access_admin.delete_queryset(self.request, queryset)

        self.project.refresh_from_db()
        self.assertEqual(1, ProjectAccess.objects.filter(
            project=self.project, role=ROLE_OWNER).count())
        self.assertEqual(ROLE_OWNER,
                         effective_role(User.objects.get(pk=self.project.user_id), self.project))

    def test_editing_a_row_here_goes_through_the_same_guard(self):
        """Demoting the primary owner from the admin re-points, as it does everywhere else."""
        grant_project_access(self.project, self.second, ROLE_OWNER)
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        entry.role = ROLE_ADMIN

        self.access_admin.save_model(self.request, entry, form=None, change=True)

        self.project.refresh_from_db()
        self.assertEqual(self.second.id, self.project.user_id)
        self.assertEqual(ROLE_ADMIN, effective_role(self.owner, self.project))

    def test_demoting_the_only_owner_here_is_refused_with_a_message(self):
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        entry.role = ROLE_READ

        self.access_admin.save_model(self.request, entry, form=None, change=True)

        self.assertTrue(any("must have an owner" in m for m in self.messages), self.messages)
        self.assertEqual(ROLE_OWNER, effective_role(self.owner, self.project))
