"""Designating an ancestor: the page, the write, and who may do it.

The designation is the one setting in the product that changes what *everyone* sees. That is
the property these tests exist to protect -- the repo already has a scar from a shared setting
anybody with write access could change silently (`AleExperimentFilter`), and the answer here is
that the write is attributed, gated on `can_edit_experiment`, and therefore refused outright
while the experiment is locked.

The subtraction itself is pinned in `test_ancestor_subtraction.py`; this file is about the
endpoint.
"""

from django.contrib.auth.models import User

from aledb_experiment.models import AleExperiment
from aledb_mutation_editor.tests.base import EditorTestCase

PAGE = "/ale/experiment/%d/ancestor/"
APPLY = "/ale/experiment/%d/ancestor/apply/"


class AncestorPageTestCase(EditorTestCase):

    def page(self):
        return self.client.get(PAGE % self.experiment.ale_id)

    def apply(self, **data):
        return self.client.post(APPLY % self.experiment.ale_id, data)

    def reloaded(self):
        return AleExperiment.objects.get(pk=self.experiment.pk)


class TestThePage(AncestorPageTestCase):

    def test_it_lists_every_sample_with_the_id_the_picker_posts(self):
        """`data-value` is what `aledbSelectList` reads a row's id off, so a list rendered
        without it looks right and posts an empty selection."""
        response = self.page()
        for sample in (self.sample_a, self.sample_b):
            self.assertContains(response, 'data-value="%d"' % sample.id)

    def test_it_offers_no_select_all_buttons(self):
        """Exactly one row is the answer, so `single` suppresses them."""
        self.assertNotContains(self.page(), 'data-select="all"')

    def test_it_lists_the_current_ancestor_even_though_no_other_page_does(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        response = self.page()
        self.assertContains(response, 'data-value="%d"' % self.sample_a.id)
        self.assertContains(response, "active")

    def test_it_says_what_designating_will_do(self):
        self.assertContains(self.page(), "subtracted")

    def test_it_names_the_current_ancestor_at_the_top(self):
        """What the reader comes back to check after saving; the page reloads onto it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.page().content.decode()
        self.assertIn("Ancestor:", body)
        self.assertIn(self.sample_a.ale_flask_isolate_str, body)
        self.assertIn(self.owner.get_username(), body)

    def test_it_says_so_when_there_is_no_ancestor(self):
        self.assertContains(self.page(), "No ancestor is designated")

    def test_it_uses_the_sweetalert_2_api(self):
        """A live bug this pins: written against sweetalert 1 -- `type`, `showCancelButton`
        and a callback as the second argument -- the confirm never fires and the Save button
        looks dead rather than broken, with no error anywhere. base.html loads sweetalert 2,
        whose options are `icon`/`buttons` and which returns a promise."""
        body = self.page().content.decode()
        self.assertIn('icon: "warning"', body)
        self.assertIn("buttons: true", body)
        # The option syntax, with the colon: the bare words appear in the comment above the
        # call, which is where the v1 spelling is named so nobody reintroduces it.
        self.assertNotIn("showCancelButton:", body)
        self.assertNotIn("confirmButtonText:", body)
        self.assertNotIn("type: \"warning\"", body)


class TestTheWrite(AncestorPageTestCase):

    def test_designating_records_who_and_when(self):
        self.apply(reseq_id=self.sample_a.id)
        experiment = self.reloaded()
        self.assertEqual(experiment.ancestor_id, self.sample_a.id)
        self.assertEqual(experiment.ancestor_set_by_id, self.owner.id)
        self.assertIsNotNone(experiment.ancestor_set_at)

    def test_designating_switches_away_from_the_prior_one_in_one_write(self):
        """One column, so there is no prior flag left to clear and no way to end up with two."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.apply(reseq_id=self.sample_b.id)
        self.assertEqual(self.reloaded().ancestor_id, self.sample_b.id)

    def test_posting_nothing_clears_the_designation(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.apply()
        experiment = self.reloaded()
        self.assertIsNone(experiment.ancestor_id)
        self.assertIsNone(experiment.ancestor_set_at)
        self.assertIsNone(experiment.ancestor_set_by_id)

    def test_a_sample_from_another_experiment_is_refused(self):
        """Nothing in the schema stops the column pointing across experiments, so the endpoint
        has to: a mutation belongs to one experiment's reference genome."""
        other = self.client.post(
            "/ale/projects/create/", {"name": "P2", "experiment": "E2"}).json()
        foreign = AleExperiment.objects.get(pk=other["experiment_id"])
        response = self.client.post(APPLY % foreign.ale_id, {"reseq_id": self.sample_a.id})
        self.assertEqual(404, response.status_code)
        self.assertIsNone(AleExperiment.objects.get(pk=foreign.pk).ancestor_id)

    def test_a_nonsense_id_is_refused(self):
        response = self.apply(reseq_id="banana")
        self.assertEqual(400, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)


class TestPermission(AncestorPageTestCase):
    """Every refusal is followed by an assertion that the write did not happen -- a 403 whose
    side effect landed anyway is the failure this shape of test exists to catch."""

    def test_a_stranger_cannot_designate(self):
        stranger = User.objects.create(username="stranger", is_active=True)
        self.client.force_login(stranger)
        response = self.apply(reseq_id=self.sample_a.id)
        self.assertEqual(403, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)

    def test_a_locked_experiment_refuses_its_owner(self):
        self.experiment.lock(self.owner)
        response = self.apply(reseq_id=self.sample_a.id)
        self.assertEqual(403, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)

    def test_a_locked_experiment_refuses_a_superuser(self):
        """A lock outranks every role, which is what makes it a lock rather than a fifth one."""
        root = User.objects.create(username="root", is_active=True, is_superuser=True,
                                   is_staff=True)
        self.experiment.lock(self.owner)
        self.client.force_login(root)
        response = self.apply(reseq_id=self.sample_a.id)
        self.assertEqual(403, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)

    def test_a_locked_experiment_refuses_to_clear_the_designation_too(self):
        """The way out is to unlock, change it, and lock again -- not to route around it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.experiment.lock(self.owner)
        response = self.apply()
        self.assertEqual(403, response.status_code)
        self.assertEqual(self.reloaded().ancestor_id, self.sample_a.id)

    def test_the_refusal_says_the_experiment_is_locked(self):
        self.experiment.lock(self.owner)
        self.assertIn("locked", self.apply(reseq_id=self.sample_a.id).json()["error"])
