"""The one page that shows ancestral mutations instead of hiding them.

`/mutations/breseq` is what breseq called in one sample. Every page that analyses the data
subtracts the designated ancestor; this one tints those rows red, because a row silently
missing here would make the page disagree with the report it was imported from -- and the
reader would have no way to find out why.

That exception is only safe if it is *stated*, which is what most of these tests are about.
"""

from aledb_mutation_editor.tests.base import EditorTestCase

BRESEQ = "/mutations/breseq"


class BreseqAncestorTestCase(EditorTestCase):

    def get(self, **params):
        params.setdefault("ale_experiment_id", self.experiment.ale_id)
        return self.client.get(BRESEQ, params)


class TestTheTint(BreseqAncestorTestCase):

    def test_an_ancestral_row_is_tinted(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        # sample_b carries mut_1, which the ancestor also carries.
        self.assertContains(self.get(reseq_id=self.sample_b.id), "ancestral_table_row")

    def test_nothing_is_tinted_without_a_designation(self):
        self.assertNotContains(self.get(reseq_id=self.sample_b.id), "ancestral_table_row")

    def test_a_non_ancestral_row_is_not_tinted(self):
        evolved_only = self.make_mutation(position=999, sequence_change="T>C")
        self.observe(self.sample_b, evolved_only)
        self.experiment.set_ancestor(self.sample_a, self.owner)

        response = self.get(reseq_id=self.sample_b.id)
        # Two rows on this sample, exactly one of them ancestral.
        self.assertEqual(response.content.decode().count("ancestral_table_row"), 1)

    def test_the_rows_are_still_there(self):
        """Tinted, not hidden -- the whole point of the exception."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(reseq_id=self.sample_b.id), str(self.mut_1.position))


class TestReachingTheAncestor(BreseqAncestorTestCase):

    def test_the_picker_does_not_list_it(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        response = self.get(reseq_id=self.sample_b.id)
        self.assertNotContains(response, "reseq_id=%d" % self.sample_a.id)

    def test_its_own_page_still_opens(self):
        """`_selected_reseq` resolves it outside the picker. Without that fallback a link to
        the ancestor renders a *different* sample and looks entirely normal doing it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        response = self.get(reseq_id=self.sample_a.id)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context["selected_reseq"].id, self.sample_a.id)

    def test_its_own_page_says_what_it_is(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(reseq_id=self.sample_a.id), "designated ancestor")

    def test_a_sample_from_another_experiment_is_still_not_reachable(self):
        """The fallback is scoped to the experiment, so it is not a way around anything."""
        other = self.client.post(
            "/ale/projects/create/", {"name": "P2", "experiment": "E2"}).json()
        from aledb_experiment.models import AleExperiment
        foreign = AleExperiment.objects.get(pk=other["experiment_id"])
        response = self.client.get(BRESEQ, {"ale_experiment_id": foreign.ale_id,
                                            "reseq_id": self.sample_a.id})
        self.assertNotEqual(getattr(response.context.get("selected_reseq"), "id", None),
                            self.sample_a.id)


class TestWhatThePageClaims(BreseqAncestorTestCase):

    def test_it_does_not_claim_to_have_subtracted(self):
        """This page passes `ancestor_subtracted=False`. A page describing filtering it did not
        do is the failure `aledb_filter` is built to prevent, and it cuts both ways."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertNotContains(self.get(reseq_id=self.sample_b.id),
                               "are excluded, and so is that sample")

    def test_the_legend_explains_the_red(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(reseq_id=self.sample_b.id), "Rows shaded red")
