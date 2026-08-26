"""The role ordering. Pure functions, no database."""

from django.test import SimpleTestCase

from aledb_experiment.roles import (
    ROLE_ADMIN, ROLE_CHOICES, ROLE_OWNER, ROLE_READ, ROLE_WRITE,
    at_least, best_role, is_role, rank, roles_at_least,
)


class RankTestCase(SimpleTestCase):
    def test_the_four_roles_are_ordered(self):
        self.assertLess(rank(ROLE_READ), rank(ROLE_WRITE))
        self.assertLess(rank(ROLE_WRITE), rank(ROLE_ADMIN))
        self.assertLess(rank(ROLE_ADMIN), rank(ROLE_OWNER))

    def test_no_role_and_an_unknown_role_are_worth_nothing(self):
        """Deliberately 0 rather than an exception.

        `effective_role` answers None for someone with no access, and a row written by a
        future version naming a role this one has never heard of must refuse access rather
        than 500 the page it is read on.
        """
        self.assertEqual(rank(None), 0)
        self.assertEqual(rank("wizard"), 0)
        self.assertEqual(rank(""), 0)


class AtLeastTestCase(SimpleTestCase):
    def test_a_role_satisfies_itself_and_everything_below(self):
        self.assertTrue(at_least(ROLE_ADMIN, ROLE_ADMIN))
        self.assertTrue(at_least(ROLE_ADMIN, ROLE_WRITE))
        self.assertTrue(at_least(ROLE_ADMIN, ROLE_READ))

    def test_a_role_does_not_satisfy_anything_above(self):
        self.assertFalse(at_least(ROLE_ADMIN, ROLE_OWNER))
        self.assertFalse(at_least(ROLE_READ, ROLE_WRITE))

    def test_no_role_satisfies_nothing(self):
        self.assertFalse(at_least(None, ROLE_READ))

    def test_an_unknown_minimum_is_never_satisfied(self):
        """`rank(minimum) > 0` in the comparison: otherwise every role would clear a typo."""
        self.assertFalse(at_least(ROLE_OWNER, "wizard"))


class BestRoleTestCase(SimpleTestCase):
    def test_the_higher_wins_either_way_round(self):
        self.assertEqual(best_role(ROLE_READ, ROLE_ADMIN), ROLE_ADMIN)
        self.assertEqual(best_role(ROLE_ADMIN, ROLE_READ), ROLE_ADMIN)

    def test_it_folds_over_none(self):
        self.assertEqual(best_role(None, ROLE_WRITE), ROLE_WRITE)
        self.assertEqual(best_role(ROLE_WRITE, None), ROLE_WRITE)
        self.assertIsNone(best_role(None, None))


class RolesAtLeastTestCase(SimpleTestCase):
    def test_it_names_the_role_and_everything_above(self):
        self.assertEqual(set(roles_at_least(ROLE_WRITE)),
                         {ROLE_WRITE, ROLE_ADMIN, ROLE_OWNER})

    def test_read_is_all_four(self):
        self.assertEqual(set(roles_at_least(ROLE_READ)),
                         {ROLE_READ, ROLE_WRITE, ROLE_ADMIN, ROLE_OWNER})

    def test_owner_is_only_owner(self):
        self.assertEqual(roles_at_least(ROLE_OWNER), [ROLE_OWNER])


class IsRoleTestCase(SimpleTestCase):
    def test_every_choice_is_a_role(self):
        for value, _label in ROLE_CHOICES:
            self.assertTrue(is_role(value), value)

    def test_nothing_else_is(self):
        self.assertFalse(is_role("wizard"))
        self.assertFalse(is_role(None))
