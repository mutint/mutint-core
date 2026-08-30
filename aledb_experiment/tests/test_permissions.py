"""The access policy itself, without the HTTP layer.

`aledb_experiment/permissions.py` is the only thing in the product that decides who may read
or change anything, so these are the tests that matter most if any of it is touched.
"""

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase

from aledb_experiment.models import AleGroup, AleGroupMembership, Project, ProjectAccess
from aledb_experiment.permissions import (
    AccessError, accessible_projects, can_add_experiment_filter, can_admin_project,
    can_delete_project, can_edit_project, can_manage_project_access, can_own_project,
    can_view_project, clear_role_cache, effective_role, grant_project_access,
    resolve_group_name, resolve_username, revoke_project_access, set_primary_owner,
)
from aledb_experiment.roles import ROLE_ADMIN, ROLE_OWNER, ROLE_READ, ROLE_WRITE


def make_user(username, **kwargs):
    return User.objects.create(username=username, email="%s@e.com" % username,
                               is_active=True, **kwargs)


class RoleLadderTestCase(TestCase):
    """Each role satisfies exactly the predicates at or below it."""

    #: predicate -> the minimum role that satisfies it
    PREDICATES = (
        (can_view_project, ROLE_READ),
        (can_edit_project, ROLE_WRITE),
        (can_admin_project, ROLE_ADMIN),
        (can_manage_project_access, ROLE_ADMIN),
        (can_delete_project, ROLE_ADMIN),
        (can_own_project, ROLE_OWNER),
    )
    LADDER = (ROLE_READ, ROLE_WRITE, ROLE_ADMIN, ROLE_OWNER)

    def setUp(self):
        self.owner = make_user("owner")
        self.project = Project.objects.create(name="P", user=self.owner, is_public=False)
        set_primary_owner(self.project, self.owner)
        self.member = make_user("member")

    def test_each_role_grants_exactly_what_it_should(self):
        from aledb_experiment.roles import rank
        for index, role in enumerate(self.LADDER):
            grant_project_access(self.project, self.member, role)
            for predicate, minimum in self.PREDICATES:
                with self.subTest(role=role, predicate=predicate.__name__):
                    self.assertEqual(predicate(self.member, self.project),
                                     rank(role) >= rank(minimum))

    def test_a_stranger_has_no_role_at_all(self):
        stranger = make_user("stranger")
        self.assertIsNone(effective_role(stranger, self.project))
        self.assertFalse(can_view_project(stranger, self.project))

    def test_effective_role_of_no_project_is_none(self):
        """Every experiment-scoped caller passes `experiment.project`, which is nullable."""
        self.assertIsNone(effective_role(self.owner, None))
        self.assertFalse(can_edit_project(self.owner, None))


class ImplicitRolesTestCase(TestCase):
    """The three ways to hold a role with no ProjectAccess row."""

    def setUp(self):
        self.owner = make_user("owner")
        self.stranger = make_user("stranger")
        self.private = Project.objects.create(name="private", user=self.owner,
                                              is_public=False)
        self.public = Project.objects.create(name="public", user=self.owner,
                                             is_public=True)

    def test_project_user_is_owner_without_a_grant_row(self):
        """The old trap, gone.

        `can_view_project` used to consult only the guardian grant, so
        `Project.objects.create(user=someone)` produced a project its named owner could not
        open. A missing mirror row is now at worst a display bug.
        """
        self.assertFalse(ProjectAccess.objects.filter(project=self.private).exists())
        self.assertEqual(effective_role(self.owner, self.private), ROLE_OWNER)

    def test_a_superuser_is_owner_everywhere(self):
        admin = make_user("admin", is_superuser=True)
        self.assertEqual(effective_role(admin, self.private), ROLE_OWNER)

    def test_staff_hold_nothing(self):
        """Inverted deliberately: `can_view_project` used to end `return bool(is_staff)`.

        `load_projects` creates every imported user with `is_staff=True`, so that clause made
        nearly everything readable by nearly everyone and every role below admin decorative.
        """
        staff = make_user("staff", is_staff=True)
        self.assertIsNone(effective_role(staff, self.private))
        self.assertFalse(can_view_project(staff, self.private))

    def test_public_gives_read_and_never_more(self):
        self.assertEqual(effective_role(self.stranger, self.public), ROLE_READ)
        self.assertFalse(can_edit_project(self.stranger, self.public))

    def test_an_anonymous_visitor_reads_public_projects_only(self):
        anonymous = AnonymousUser()
        self.assertEqual(effective_role(anonymous, self.public), ROLE_READ)
        self.assertIsNone(effective_role(anonymous, self.private))

    def test_a_grant_beats_public_read(self):
        grant_project_access(self.public, self.stranger, ROLE_WRITE)
        self.assertEqual(effective_role(self.stranger, self.public), ROLE_WRITE)


class GroupGrantTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.lead = make_user("lead")
        self.member = make_user("member")
        self.stranger = make_user("stranger")

        self.project = Project.objects.create(name="P", user=self.owner, is_public=False)
        set_primary_owner(self.project, self.owner)

        self.group = AleGroup.objects.create(name="Lab", owner=self.lead)
        AleGroupMembership.objects.create(group=self.group, user=self.lead, is_manager=True)
        AleGroupMembership.objects.create(group=self.group, user=self.member)

    def test_a_group_grant_reaches_every_member(self):
        grant_project_access(self.project, self.group, ROLE_WRITE)
        self.assertEqual(effective_role(self.member, self.project), ROLE_WRITE)

    def test_a_group_grant_reaches_the_groups_owner(self):
        """Through their membership row, not a special case -- which is why one is written."""
        grant_project_access(self.project, self.group, ROLE_WRITE)
        self.assertEqual(effective_role(self.lead, self.project), ROLE_WRITE)

    def test_it_reaches_nobody_else(self):
        grant_project_access(self.project, self.group, ROLE_WRITE)
        self.assertIsNone(effective_role(self.stranger, self.project))

    def test_the_higher_of_a_direct_and_a_group_grant_wins(self):
        grant_project_access(self.project, self.group, ROLE_READ)
        grant_project_access(self.project, self.member, ROLE_ADMIN)
        self.assertEqual(effective_role(self.member, self.project), ROLE_ADMIN)

    def test_and_it_wins_the_other_way_round_too(self):
        grant_project_access(self.project, self.group, ROLE_ADMIN)
        grant_project_access(self.project, self.member, ROLE_READ)
        self.assertEqual(effective_role(self.member, self.project), ROLE_ADMIN)

    def test_a_group_cannot_be_made_an_owner(self):
        with self.assertRaises(AccessError):
            grant_project_access(self.project, self.group, ROLE_OWNER)

    def test_leaving_the_group_takes_the_access_away(self):
        grant_project_access(self.project, self.group, ROLE_WRITE)
        AleGroupMembership.objects.filter(group=self.group, user=self.member).delete()
        clear_role_cache()
        self.assertIsNone(effective_role(self.member, self.project))


class CacheTestCase(TestCase):
    """The per-request role cache, and the generation counter that invalidates it.

    Not an optimisation: `mutation_table_builder` asks `can_add_experiment_filter` once per
    sample column and once per mutation row, so a table of 400 mutations over 20 samples asks
    420 times. django-guardian used to absorb that in its own per-user cache.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.member = make_user("member")
        self.project = Project.objects.create(name="P", user=self.owner, is_public=False)
        set_primary_owner(self.project, self.owner)
        grant_project_access(self.project, self.member, ROLE_READ)

    def test_repeated_checks_cost_one_query(self):
        # Warm it the way a request does: one `User` instance for the whole page.
        self.assertTrue(can_view_project(self.member, self.project))
        with self.assertNumQueries(0):
            for _ in range(100):
                can_view_project(self.member, self.project)

    def test_the_first_check_costs_exactly_one_query(self):
        with self.assertNumQueries(1):
            can_view_project(self.member, self.project)

    def test_a_new_grant_is_visible_to_the_same_user_object(self):
        """The generation counter earning its keep.

        Without it a `User` instance that outlives a grant -- which is every test, and the
        CLI -- keeps answering with the role it had when first asked.
        """
        self.assertFalse(can_edit_project(self.member, self.project))
        grant_project_access(self.project, self.member, ROLE_WRITE)
        self.assertTrue(can_edit_project(self.member, self.project))

    def test_a_revoked_grant_is_visible_to_the_same_user_object(self):
        entry = ProjectAccess.objects.get(project=self.project, user=self.member)
        self.assertTrue(can_view_project(self.member, self.project))
        revoke_project_access(self.project, entry)
        self.assertFalse(can_view_project(self.member, self.project))


class AccessibleProjectsTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.member = make_user("member")
        self.stranger = make_user("stranger")

        self.owned = Project.objects.create(name="owned", user=self.owner)
        set_primary_owner(self.owned, self.owner)
        self.shared = Project.objects.create(name="shared", user=self.owner)
        set_primary_owner(self.shared, self.owner)
        self.public = Project.objects.create(name="public", user=self.owner, is_public=True)
        set_primary_owner(self.public, self.owner)

        grant_project_access(self.shared, self.member, ROLE_WRITE)

    def test_it_is_always_a_queryset(self):
        """It used to return a QuerySet for superusers and a list for everyone else."""
        from django.db.models import QuerySet
        for user in (self.owner, self.member, AnonymousUser(),
                     make_user("admin", is_superuser=True)):
            with self.subTest(user=user):
                self.assertIsInstance(accessible_projects(user), QuerySet)

    def test_a_member_sees_what_they_are_granted_plus_public(self):
        self.assertCountEqual(accessible_projects(self.member),
                              [self.shared, self.public])

    def test_the_minimum_filters(self):
        self.assertCountEqual(accessible_projects(self.member, ROLE_WRITE), [self.shared])
        self.assertCountEqual(accessible_projects(self.member, ROLE_ADMIN), [])

    def test_public_projects_count_for_reading_and_not_for_writing(self):
        self.assertIn(self.public, accessible_projects(self.stranger))
        self.assertNotIn(self.public, accessible_projects(self.stranger, ROLE_WRITE))

    def test_an_anonymous_visitor_gets_public_projects_and_nothing_writable(self):
        self.assertCountEqual(accessible_projects(AnonymousUser()), [self.public])
        self.assertCountEqual(accessible_projects(AnonymousUser(), ROLE_WRITE), [])

    def test_a_soft_deleted_project_drops_out(self):
        self.shared.soft_delete(self.owner)
        self.assertNotIn(self.shared, accessible_projects(self.member))

    def test_a_group_grant_does_not_duplicate_rows(self):
        """`.distinct()` is load-bearing: the membership join fans out one row per member."""
        group = AleGroup.objects.create(name="Lab", owner=self.owner)
        AleGroupMembership.objects.create(group=group, user=self.member, is_manager=True)
        AleGroupMembership.objects.create(group=group, user=make_user("other"))
        grant_project_access(self.owned, group, ROLE_READ)
        visible = list(accessible_projects(self.member))
        self.assertEqual(len(visible), len(set(visible)))
        self.assertIn(self.owned, visible)

    def test_a_superuser_sees_every_live_project(self):
        admin = make_user("admin", is_superuser=True)
        self.assertCountEqual(accessible_projects(admin),
                              [self.owned, self.shared, self.public])


class OwnershipInvariantTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.second = make_user("second")
        self.project = Project.objects.create(name="P", user=self.owner)
        set_primary_owner(self.project, self.owner)

    def test_set_primary_owner_writes_both_the_field_and_the_row(self):
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        self.assertEqual(entry.role, ROLE_OWNER)
        self.assertEqual(self.project.user_id, self.owner.id)

    def test_the_last_owner_cannot_be_revoked(self):
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        with self.assertRaises(AccessError):
            revoke_project_access(self.project, entry)

    def test_the_last_owner_cannot_be_demoted(self):
        with self.assertRaises(AccessError):
            grant_project_access(self.project, self.owner, ROLE_ADMIN)

    def test_a_second_owner_makes_the_first_removable(self):
        grant_project_access(self.project, self.second, ROLE_OWNER)
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        revoke_project_access(self.project, entry)
        self.assertEqual(effective_role(self.second, self.project), ROLE_OWNER)

    def test_revoking_the_primary_owner_repoints_project_user(self):
        """`Project.user` is displayed as *the* owner, so it cannot name someone with no access."""
        grant_project_access(self.project, self.second, ROLE_OWNER)
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        revoke_project_access(self.project, entry)
        self.project.refresh_from_db()
        self.assertEqual(self.project.user_id, self.second.id)
        self.assertIsNone(effective_role(self.owner, self.project))

    def assertOwnershipConsistent(self, project):
        """`Project.user` names someone who holds an owner grant.

        The standing invariant, asserted after an operation rather than by any one of them:
        `effective_role` grants owner from `Project.user` alone, so a primary owner whose row
        says something lesser is an owner the sharing page cannot show and cannot take away.
        """
        project.refresh_from_db()
        entry = ProjectAccess.objects.filter(
            project=project, user_id=project.user_id, group=None).first()
        self.assertIsNotNone(
            entry, "Project.user names %s, who holds no grant at all" % project.user_id)
        self.assertEqual(ROLE_OWNER, entry.role,
                         "Project.user names %s, whose grant says %s -- effective_role still "
                         "answers owner for them" % (project.user_id, entry.role))

    def test_demoting_the_primary_owner_repoints_project_user(self):
        """The defect. `revoke` re-pointed and `grant` did not, so a demotion did nothing.

        The refusal an owner is shown tells them to give ownership away and then lower their
        own role -- and lowering it left `Project.user` naming them, which `effective_role`
        reads as owner on its own.
        """
        grant_project_access(self.project, self.second, ROLE_OWNER)
        grant_project_access(self.project, self.owner, ROLE_ADMIN)

        self.project.refresh_from_db()
        self.assertEqual(self.second.id, self.project.user_id)
        self.assertEqual(ROLE_ADMIN, effective_role(self.owner, self.project))
        self.assertOwnershipConsistent(self.project)

    def test_demoting_a_second_owner_leaves_the_primary_alone(self):
        grant_project_access(self.project, self.second, ROLE_OWNER)
        grant_project_access(self.project, self.second, ROLE_READ)

        self.project.refresh_from_db()
        self.assertEqual(self.owner.id, self.project.user_id)
        self.assertOwnershipConsistent(self.project)

    def test_the_primary_owner_cannot_be_demoted_without_a_mirror_row(self):
        """`Project.user` counts as an owner, with or without the row.

        `Project.objects.create(user=...)` makes exactly this shape and the suite blesses it,
        so a guard counting only ProjectAccess would demote away the last effective owner.
        """
        bare = Project.objects.create(name="Bare", user=self.owner)
        self.assertEqual(ROLE_OWNER, effective_role(self.owner, bare))

        with self.assertRaises(AccessError):
            grant_project_access(bare, self.owner, ROLE_READ)

    def test_a_regrant_is_an_upsert_not_a_second_row(self):
        grant_project_access(self.project, self.second, ROLE_READ)
        grant_project_access(self.project, self.second, ROLE_ADMIN)
        rows = ProjectAccess.objects.filter(project=self.project, user=self.second)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().role, ROLE_ADMIN)


class OwnerDeletionTestCase(TestCase):
    """Ownership is transferred, not deleted out from under a project.

    `Project.user` is NOT NULL and carries a real FK, so deleting a primary owner always
    failed -- but as DO_NOTHING it failed as an `IntegrityError` from the database at commit,
    after the admin had already promised it would work. PROTECT refuses up front and names
    what is in the way.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.second = make_user("second")
        self.project = Project.objects.create(name="P", user=self.owner)
        set_primary_owner(self.project, self.owner)

    def test_deleting_the_primary_owner_is_refused(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.owner.delete()

        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_deleting_a_non_primary_owner_is_allowed_and_leaves_an_owner(self):
        """Their grant cascades; the project keeps the owner it is named after."""
        grant_project_access(self.project, self.second, ROLE_OWNER)

        self.second.delete()

        self.project.refresh_from_db()
        self.assertEqual(self.owner.id, self.project.user_id)
        self.assertEqual(ROLE_OWNER, effective_role(self.owner, self.project))

    def test_the_way_out_is_to_hand_the_project_on_first(self):
        grant_project_access(self.project, self.second, ROLE_OWNER)
        grant_project_access(self.project, self.owner, ROLE_READ)
        entry = ProjectAccess.objects.get(project=self.project, user=self.owner)
        revoke_project_access(self.project, entry)

        self.owner.delete()

        self.project.refresh_from_db()
        self.assertEqual(self.second.id, self.project.user_id)


class ResolveTestCase(TestCase):
    def setUp(self):
        self.actor = make_user("actor")
        self.alice = make_user("alice")

    def test_an_exact_username_resolves(self):
        self.assertEqual(resolve_username("alice"), self.alice)

    def test_surrounding_whitespace_is_forgiven(self):
        self.assertEqual(resolve_username("  alice "), self.alice)

    def test_case_is_forgiven_when_it_is_unambiguous(self):
        self.assertEqual(resolve_username("Alice"), self.alice)

    def test_an_exact_match_wins_over_a_case_insensitive_one(self):
        """Django usernames are unique case-*sensitively*, so both can exist."""
        upper = make_user("ALICE")
        self.assertEqual(resolve_username("ALICE"), upper)
        self.assertEqual(resolve_username("alice"), self.alice)

    def test_an_ambiguous_case_insensitive_match_is_refused_by_name(self):
        make_user("ALICE")
        with self.assertRaises(AccessError) as caught:
            resolve_username("AlIcE")
        self.assertIn("case-sensitive", str(caught.exception))

    def test_an_unknown_username_says_so(self):
        with self.assertRaises(AccessError) as caught:
            resolve_username("nobody")
        self.assertIn("nobody", str(caught.exception))

    def test_an_empty_username_asks_for_one(self):
        with self.assertRaises(AccessError):
            resolve_username("   ")

    def test_a_group_you_belong_to_resolves(self):
        group = AleGroup.objects.create(name="Lab", owner=self.actor)
        AleGroupMembership.objects.create(group=group, user=self.actor, is_manager=True)
        self.assertEqual(resolve_group_name("lab", self.actor), group)

    def test_a_group_you_do_not_belong_to_is_indistinguishable_from_one_that_is_absent(self):
        """The anti-enumeration guardrail.

        A box that resolved any name would let anyone list every group on the installation by
        typing names until one was accepted.
        """
        AleGroup.objects.create(name="Secret Lab", owner=self.alice)
        with self.assertRaises(AccessError) as present:
            resolve_group_name("Secret Lab", self.actor)
        with self.assertRaises(AccessError) as absent:
            resolve_group_name("No Such Lab", self.actor)
        self.assertEqual(str(present.exception).replace("Secret", "No Such"),
                         str(absent.exception))


class ExperimentFilterTestCase(TestCase):
    """`can_add_experiment_filter` moved from the view permission to write."""

    def setUp(self):
        from aledb_experiment.models import AleExperiment, Instrument
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.writer = make_user("writer")
        self.project = Project.objects.create(name="P", user=self.owner)
        set_primary_owner(self.project, self.owner)
        grant_project_access(self.project, self.reader, ROLE_READ)
        grant_project_access(self.project, self.writer, ROLE_WRITE)
        instrument, _ = Instrument.objects.get_or_create(name="i")
        self.experiment = AleExperiment.objects.create(
            name="E", project=self.project, instrument=instrument, person="owner")

    def test_a_reader_may_not_curate(self):
        """Tagging and filtering write shared state that four mutation tables read back."""
        self.assertFalse(can_add_experiment_filter(self.reader, self.experiment))

    def test_a_writer_may(self):
        self.assertTrue(can_add_experiment_filter(self.writer, self.experiment))

    def test_no_experiment_is_a_refusal_not_a_crash(self):
        self.assertFalse(can_add_experiment_filter(self.owner, None))
