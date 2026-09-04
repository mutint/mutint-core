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
        params.setdefault("experiment_id", self.experiment.id)
        return self.client.get(BRESEQ, params)


class TestTheTint(BreseqAncestorTestCase):

    def test_an_ancestral_row_is_tinted(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        # sample_b carries mut_1, which the ancestor also carries.
        self.assertContains(self.get(sample_id=self.sample_b.id), "ancestral_table_row")

    def test_nothing_is_tinted_without_a_designation(self):
        self.assertNotContains(self.get(sample_id=self.sample_b.id), "ancestral_table_row")

    def test_a_non_ancestral_row_is_not_tinted(self):
        evolved_only = self.make_mutation(position=999, sequence_change="T>C")
        self.observe(self.sample_b, evolved_only)
        self.experiment.set_ancestor(self.sample_a, self.owner)

        response = self.get(sample_id=self.sample_b.id)
        # Two rows on this sample, exactly one of them ancestral.
        self.assertEqual(response.content.decode().count("ancestral_table_row"), 1)

    def test_the_rows_are_still_there(self):
        """Tinted, not hidden -- the whole point of the exception."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(sample_id=self.sample_b.id), str(self.mut_1.position))


class TestReachingTheAncestor(BreseqAncestorTestCase):

    def picker(self, response):
        """Just the sample dropdown. The assertion below has to be scoped to it: the legend
        links the ancestor by design, so "the page never mentions it" is the wrong question
        and stopped being true the moment that link was added."""
        body = response.content.decode()
        start = body.index('<ul class="dropdown-menu aledb-menu">')
        return body[start:body.index("</ul>", start)]

    def test_the_picker_lists_it_first_and_tinted(self):
        """The one listing that keeps the ancestor. Nothing aggregates on this page, so there
        is nothing for it to contaminate -- and this is the only picker that could reach it,
        so hiding it here made it unreachable rather than merely excluded."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        picker = self.picker(self.get(sample_id=self.sample_b.id))
        self.assertIn("sample_id=%d" % self.sample_a.id, picker)
        self.assertIn("sample_id=%d" % self.sample_b.id, picker)
        self.assertLess(picker.index("sample_id=%d" % self.sample_a.id),
                        picker.index("sample_id=%d" % self.sample_b.id))
        self.assertIn("reseq-ancestor", picker)

    def test_only_the_ancestor_is_tinted_in_the_picker(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        picker = self.picker(self.get(sample_id=self.sample_b.id))
        self.assertEqual(picker.count("reseq-ancestor\""), 1)

    def test_the_picker_is_untinted_without_a_designation(self):
        self.assertNotIn("reseq-ancestor", self.picker(self.get()))

    def test_the_page_does_not_open_on_the_ancestor(self):
        """Listed first, but not what the page opens on: this view reads as "what evolved in
        this sample", and the one sample where the answer is "nothing, by definition" is a
        poor first thing to show. One click away, which is the point of listing it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertEqual(self.get().context["selected_reseq"].id, self.sample_b.id)

    def test_an_experiment_of_only_the_ancestor_still_shows_it(self):
        self.sample_b.delete()
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertEqual(self.get().context["selected_reseq"].id, self.sample_a.id)

    def test_its_own_page_still_opens(self):
        """`_selected_reseq` resolves it outside the picker. Without that fallback a link to
        the ancestor renders a *different* sample and looks entirely normal doing it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        response = self.get(sample_id=self.sample_a.id)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context["selected_reseq"].id, self.sample_a.id)

    def test_its_own_page_says_what_it_is(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(sample_id=self.sample_a.id), "designated ancestor")

    def test_a_sample_from_another_experiment_is_still_not_reachable(self):
        """The fallback is scoped to the experiment, so it is not a way around anything."""
        other = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        from aledb_experiment.models import Experiment
        foreign = Experiment.objects.get(pk=other["experiment_id"])
        response = self.client.get(BRESEQ, {"experiment_id": foreign.id,
                                            "sample_id": self.sample_a.id})
        self.assertNotEqual(getattr(response.context.get("selected_reseq"), "id", None),
                            self.sample_a.id)


class TestWhatThePageClaims(BreseqAncestorTestCase):

    def test_it_does_not_claim_to_have_subtracted(self):
        """This page passes `ancestor_subtracted=False`. A page describing filtering it did not
        do is the failure `aledb_filter` is built to prevent, and it cuts both ways."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertNotContains(self.get(sample_id=self.sample_b.id),
                               "are excluded, and so is that sample")

    def test_the_legend_explains_the_red(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(sample_id=self.sample_b.id), "Rows shaded red")

    def test_the_legend_names_and_links_the_ancestor(self):
        """Which sample turned these rows red is the question the tint provokes, so the
        legend answers it rather than leaving the reader to go and look."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.get(sample_id=self.sample_b.id).content.decode()
        self.assertIn(self.sample_a.ale_flask_isolate_str, body)
        self.assertIn("sample_id=%d" % self.sample_a.id, body)

    def test_the_legend_is_its_own_row(self):
        """`.breseq-legend > div` carries the row spacing and the 9pt type, so this belongs in
        a div of its own rather than trailing the amino-acid colour key -- it is not about how
        a cell is rendered, it is about rows being excluded everywhere else."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.get(sample_id=self.sample_b.id).content.decode()
        legend = body[body.index('class="breseq-legend"'):]
        red = legend.index("Rows shaded red")
        # The nearest tag opening before the text is this row's own <div>, not the colour
        # key's -- i.e. nothing but whitespace and the swatch span sits between them.
        self.assertNotIn("nonsense", legend[legend.rindex("<div>", 0, red):red])

    def test_it_does_not_link_the_ancestor_to_itself(self):
        """On the ancestor's own page the name is still given, without a link back here."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.get(sample_id=self.sample_a.id).content.decode()
        legend = body[body.index("Rows shaded red"):]
        row = legend[:legend.index("</div>")]
        self.assertIn("this sample", row)
        self.assertNotIn("<a ", row)

    def test_no_legend_row_without_a_designation(self):
        self.assertNotContains(self.get(sample_id=self.sample_b.id), "Rows shaded red")
