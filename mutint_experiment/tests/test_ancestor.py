"""Designating an ancestor: the page, the write, and who may do it.

The designation is the one setting in the product that changes what *everyone* sees. That is
the property these tests exist to protect -- the repo already has a scar from a shared setting
anybody with write access could change silently (`AleExperimentFilter`), and the answer here is
that the write is attributed, gated on `can_edit_experiment`, and therefore refused outright
while the experiment is locked.

The subtraction itself is pinned in `test_ancestor_subtraction.py`; this file is about the
endpoint.
"""

import json
import re

from django.contrib.auth.models import User

from mutint_curate.models import KIND_COPY, MutationEditSet
from mutint_curate.tests.base import EditorTestCase
from mutint_experiment.models import Experiment, Population
from mutint_experiment.permissions import grant_project_access
from mutint_experiment.roles import ROLE_READ
from mutint_sample.models import MutationCall, Sample

PAGE = "/experiment/%d/ancestor/"
APPLY = "/experiment/%d/ancestor/apply/"


class AncestorPageTestCase(EditorTestCase):

    def page(self):
        return self.client.get(PAGE % self.experiment.id)

    def apply(self, **data):
        return self.client.post(APPLY % self.experiment.id, data)

    def reloaded(self):
        return Experiment.objects.get(pk=self.experiment.pk)


class TestThePage(AncestorPageTestCase):

    def test_it_lists_every_sample_as_a_radio_the_script_posts(self):
        """`name="ancestor"` with the sample's id as the value is what the Save button reads;
        a row rendered otherwise looks right and posts nothing."""
        response = self.page()
        for sample in (self.sample_a, self.sample_b):
            self.assertContains(response, 'name="ancestor" value="%d"' % sample.id)

    def test_no_radio_is_checked_when_there_is_no_ancestor(self):
        self.assertNotContains(self.page(), " checked")

    def test_the_current_ancestor_is_the_one_checked_radio(self):
        """Listed even though no other page lists it -- this is the page that changes it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.page().content.decode()
        self.assertIn('name="ancestor" value="%d" checked' % self.sample_a.id, body)
        self.assertEqual(1, body.count(" checked"))

    def test_saving_lands_on_the_samples_page(self):
        """Both writes return to where the page was reached from, rather than reloading."""
        body = self.page().content.decode()
        self.assertIn('window.location.href = "/experiment/%d/samples/"' % self.experiment.id, body)
        self.assertNotIn("location.reload()", body)

    def test_it_links_the_shared_mutation_builder_for_an_editor(self):
        self.assertContains(self.page(), 'href="/experiment/%d/ancestor/shared/"' % self.experiment.id)
        reader = User.objects.create(username="reader", is_active=True)
        grant_project_access(self.experiment.project, reader, ROLE_READ, granted_by=self.owner)
        self.client.force_login(reader)
        body = self.page().content.decode()
        self.assertNotIn('id="anc-shared"', body)
        self.assertNotIn('id="anc-save"', body)

    def test_it_says_what_designating_will_do(self):
        self.assertContains(self.page(), "subtracted")

    def test_it_names_the_current_ancestor_at_the_top(self):
        """What the reader comes back to check after saving; the page reloads onto it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.page().content.decode()
        self.assertIn("Ancestor:", body)
        self.assertIn(self.sample_a.label, body)
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
        self.apply(sample_id=self.sample_a.id)
        experiment = self.reloaded()
        self.assertEqual(experiment.ancestor_id, self.sample_a.id)
        self.assertEqual(experiment.ancestor_set_by_id, self.owner.id)
        self.assertIsNotNone(experiment.ancestor_set_at)

    def test_designating_switches_away_from_the_prior_one_in_one_write(self):
        """One column, so there is no prior flag left to clear and no way to end up with two."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.apply(sample_id=self.sample_b.id)
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
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        foreign = Experiment.objects.get(pk=other["experiment_id"])
        response = self.client.post(APPLY % foreign.id, {"sample_id": self.sample_a.id})
        self.assertEqual(404, response.status_code)
        self.assertIsNone(Experiment.objects.get(pk=foreign.pk).ancestor_id)

    def test_a_nonsense_id_is_refused(self):
        response = self.apply(sample_id="banana")
        self.assertEqual(400, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)


class TestPermission(AncestorPageTestCase):
    """Every refusal is followed by an assertion that the write did not happen -- a 403 whose
    side effect landed anyway is the failure this shape of test exists to catch."""

    def test_a_stranger_cannot_designate(self):
        stranger = User.objects.create(username="stranger", is_active=True)
        self.client.force_login(stranger)
        response = self.apply(sample_id=self.sample_a.id)
        self.assertEqual(403, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)

    def test_a_locked_experiment_refuses_its_owner(self):
        self.experiment.lock(self.owner)
        response = self.apply(sample_id=self.sample_a.id)
        self.assertEqual(403, response.status_code)
        self.assertIsNone(self.reloaded().ancestor_id)

    def test_a_locked_experiment_refuses_a_superuser(self):
        """A lock outranks every role, which is what makes it a lock rather than a fifth one."""
        root = User.objects.create(username="root", is_active=True, is_superuser=True,
                                   is_staff=True)
        self.experiment.lock(self.owner)
        self.client.force_login(root)
        response = self.apply(sample_id=self.sample_a.id)
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
        self.assertIn("locked", self.apply(sample_id=self.sample_a.id).json()["error"])


# --- building an ancestor from the mutations the samples share ------------------------------

SHARED = "/experiment/%d/ancestor/shared/"
SHARED_APPLY = "/experiment/%d/ancestor/shared/apply/"


class SharedAncestorTestCase(AncestorPageTestCase):
    """The fixture plus a second population with one sample carrying `mut_1`, so `mut_1` is
    in 2 populations / 3 samples and `mut_2`, `mut_3` in 1 / 1."""

    def setUp(self):
        super().setUp()
        self.ale_2 = Population.objects.create(experiment=self.experiment, name=2)
        self.sample_c = Sample.objects.create(
            population=self.ale_2, time_point=1, name=1, is_clonal=True, source_name="A2 F1 I1")
        self.observe(self.sample_c, self.mut_1)

    def shared_page(self):
        return self.client.get(SHARED % self.experiment.id)

    def shared_apply(self, mutation_ids, name="My ancestor"):
        return self.client.post(SHARED_APPLY % self.experiment.id,
                                {"name": name, "mutation_ids": json.dumps(mutation_ids)})

    def payload(self):
        body = self.shared_page().content.decode()
        match = re.search(r'<script id="sa-data"[^>]*>(.*?)</script>', body, re.S)
        return json.loads(match.group(1))

    def by_id(self, payload):
        return {row["id"]: row for row in payload["mutations"]}

    def new_sample(self):
        return Sample.objects.get(population__experiment=self.experiment,
                                  population__name="Unspecified")


class TestTheSharedPage(SharedAncestorTestCase):

    def test_the_payload_counts_populations_and_samples(self):
        payload = self.payload()
        self.assertEqual(2, payload["population_count"])
        self.assertEqual(3, payload["sample_count"])
        rows = self.by_id(payload)
        self.assertEqual({self.sample_a.id, self.sample_b.id, self.sample_c.id},
                         set(rows[self.mut_1.id]["sample_ids"]))
        self.assertEqual([self.sample_a.id], rows[self.mut_2.id]["sample_ids"])
        self.assertEqual({sample["id"]: sample["population_id"] for sample in payload["samples"]},
                         {self.sample_a.id: self.ale.id, self.sample_b.id: self.ale.id,
                          self.sample_c.id: self.ale_2.id})

    def test_the_payload_leaves_out_the_current_ancestor_sample_but_not_its_mutations(self):
        """Raw calls, deliberately: subtracting the ancestor's mutations would hide exactly
        the ones this page exists to find. Its sample still neither counts as a population
        nor inflates the denominator."""
        self.experiment.set_ancestor(self.sample_c, self.owner)
        payload = self.payload()
        self.assertEqual(1, payload["population_count"])
        self.assertEqual(2, payload["sample_count"])
        self.assertEqual({self.sample_a.id, self.sample_b.id},
                         set(self.by_id(payload)[self.mut_1.id]["sample_ids"]))

    def test_the_defaults_are_every_population_and_every_sample(self):
        body = self.shared_page().content.decode()
        self.assertIn('max="2" step="1"\n                       value="2" data-role="pop-slider"', body)
        self.assertIn('value="100" data-role="frac-slider"', body)

    def test_the_name_box_defaults_to_shared_ancestor(self):
        self.assertContains(self.shared_page(), 'id="sa-name" maxlength="200"\n'
                            '                   value="Shared ancestor"')

    def test_a_reader_sees_no_create_button(self):
        reader = User.objects.create(username="reader", is_active=True)
        grant_project_access(self.experiment.project, reader, ROLE_READ, granted_by=self.owner)
        self.client.force_login(reader)
        response = self.shared_page()
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, 'id="sa-create"')

    def test_a_stranger_gets_403(self):
        self.client.force_login(User.objects.create(username="stranger", is_active=True))
        self.assertEqual(403, self.shared_page().status_code)


class TestCreatingTheSharedAncestor(SharedAncestorTestCase):

    def test_it_creates_the_sample_under_unspecified_with_no_time_point(self):
        response = self.shared_apply([self.mut_1.id])
        self.assertEqual(200, response.status_code, response.content)
        sample = self.new_sample()
        self.assertEqual(sample.id, response.json()["sample_id"])
        self.assertIsNone(sample.time_point)
        self.assertEqual("1", sample.name)
        self.assertEqual("My ancestor", sample.description)
        self.assertEqual("My ancestor", sample.label)
        self.assertTrue(sample.is_clonal)

    def test_it_copies_exactly_the_selected_mutations_as_fixed(self):
        before = self.call_count()
        self.shared_apply([self.mut_1.id, self.mut_2.id])
        sample = self.new_sample()
        self.assertEqual({self.mut_1.id, self.mut_2.id}, self.call_ids(sample))
        for call in MutationCall.objects.filter(sample=sample):
            self.assertEqual(1.0, call.frequency)
            self.assertIs(True, call.present)
        # Nothing else moved: two new calls, and every other sample's calls as they were.
        self.assertEqual(before + 2, self.call_count())
        self.assertEqual({self.mut_1.id, self.mut_2.id, self.mut_3.id}, self.call_ids(self.sample_a))

    def test_it_designates_the_new_sample_and_attributes_it(self):
        self.shared_apply([self.mut_1.id])
        experiment = self.reloaded()
        self.assertEqual(self.new_sample().id, experiment.ancestor_id)
        self.assertEqual(self.owner.id, experiment.ancestor_set_by_id)

    def test_it_records_one_restorable_edit_set(self):
        response = self.shared_apply([self.mut_1.id, self.mut_2.id])
        edit_sets = MutationEditSet.objects.filter(experiment=self.experiment)
        self.assertEqual(1, edit_sets.count())
        edit_set = edit_sets.get()
        self.assertEqual(edit_set.pk, response.json()["edit_set_id"])
        self.assertEqual(KIND_COPY, edit_set.kind)
        self.assertIn("My ancestor", edit_set.note)
        edits = list(edit_set.edits.all())
        self.assertEqual(2, len(edits))
        for edit in edits:
            self.assertEqual(self.new_sample().id, edit.sample_id)
            self.assertIsNotNone(edit.source_sample_id)

    def test_a_second_build_numbers_the_next_sample_and_moves_the_ancestor(self):
        self.shared_apply([self.mut_1.id], name="First")
        self.shared_apply([self.mut_1.id], name="Second")
        samples = Sample.objects.filter(population__experiment=self.experiment,
                                        population__name="Unspecified").order_by("pk")
        self.assertEqual(["1", "2"], [sample.name for sample in samples])
        self.assertEqual(samples[1].id, self.reloaded().ancestor_id)

    def assert_nothing_written(self):
        self.assertFalse(Sample.objects.filter(population__name="Unspecified").exists())
        self.assertFalse(MutationEditSet.objects.filter(experiment=self.experiment).exists())

    def test_it_refuses_an_empty_name(self):
        self.assertEqual(400, self.shared_apply([self.mut_1.id], name="  ").status_code)
        self.assert_nothing_written()

    def test_it_refuses_no_mutations(self):
        self.assertEqual(400, self.shared_apply([]).status_code)
        self.assert_nothing_written()

    def test_it_refuses_a_mutation_no_sample_carries(self):
        foreign = self.make_mutation(position=900, sequence_change="T>A")
        self.assertEqual(404, self.shared_apply([self.mut_1.id, foreign.id]).status_code)
        self.assert_nothing_written()

    def test_a_locked_experiment_refuses_and_creates_nothing(self):
        self.experiment.lock(self.owner)
        response = self.shared_apply([self.mut_1.id])
        self.assertEqual(403, response.status_code)
        self.assertIn("locked", response.json()["error"])
        self.assert_nothing_written()
        self.assertIsNone(self.reloaded().ancestor_id)

    def test_a_stranger_cannot_create(self):
        self.client.force_login(User.objects.create(username="stranger", is_active=True))
        self.assertEqual(403, self.shared_apply([self.mut_1.id]).status_code)
        self.assert_nothing_written()
