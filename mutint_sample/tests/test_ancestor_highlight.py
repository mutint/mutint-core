"""The per-sample page's ancestral rows: hidden by default, tinted red on request.

`/mutations/breseq` is what breseq called in one sample. Every page that analyzes the data
subtracts the designated ancestor; this one draws those rows when the reader asks, tinted
red, because a row silently missing would make the page disagree with the report it was
imported from -- and the tint says why it is missing everywhere else.

Whichever state is in force has to be *stated*, which is what most of these tests are about.
"""

from mutint_curate.tests.base import EditorTestCase

BRESEQ = "/mutations/breseq"
BUTTON = 'data-role="ancestral-toggle"'


class BreseqAncestorTestCase(EditorTestCase):

    def get(self, **params):
        params.setdefault("experiment_id", self.experiment.id)
        return self.client.get(BRESEQ, params)

    def evolved_only(self):
        """A second row on sample_b that the ancestor does not carry. At 100%, so it takes a
        stripe rather than the polymorphism green."""
        mutation = self.make_mutation(position=999, sequence_change="T>C")
        self.observe(self.sample_b, mutation, frequency="1.0000")
        return mutation


class TestHiddenByDefault(BreseqAncestorTestCase):

    def setUp(self):
        super().setUp()
        self.evolved_only()
        self.experiment.set_ancestor(self.sample_a, self.owner)

    def test_the_ancestral_row_is_not_on_the_page(self):
        body = self.get(sample_id=self.sample_b.id).content.decode()
        self.assertNotIn("ancestral_table_row", body)
        self.assertNotIn(">%d<" % self.mut_1.start_position, body)
        self.assertIn(">999<", body)

    def test_the_summary_says_so_and_counts(self):
        body = self.get(sample_id=self.sample_b.id).content.decode()
        self.assertIn("are hidden", body)
        self.assertIn(BUTTON, body)
        self.assertIn("Show 1 ancestral mutation<", body)
        self.assertNotIn("Rows shaded red", body)

    def test_striping_starts_from_the_first_drawn_row(self):
        """Dropped before `build_rows`, so the remaining row is shaded as the first row and
        not as the second of two."""
        body = self.get(sample_id=self.sample_b.id).content.decode()
        self.assertIn("alternate_table_row_0", body)
        self.assertNotIn("alternate_table_row_1", body)

    def test_the_button_keeps_the_sample(self):
        body = self.get(sample_id=self.sample_b.id).content.decode()
        button = body[body.index(BUTTON) - 200:body.index(BUTTON)]
        self.assertIn("sample_id=%d" % self.sample_b.id, button)
        self.assertIn("ancestral=show", button)


class TestTheTint(BreseqAncestorTestCase):

    def get(self, **params):
        params.setdefault("ancestral", "show")
        return super().get(**params)

    def test_an_ancestral_row_is_tinted(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        # sample_b carries mut_1, which the ancestor also carries.
        self.assertContains(self.get(sample_id=self.sample_b.id), "ancestral_table_row")

    def test_nothing_is_tinted_without_a_designation(self):
        self.assertNotContains(self.get(sample_id=self.sample_b.id), "ancestral_table_row")

    def test_a_non_ancestral_row_is_not_tinted(self):
        self.evolved_only()
        self.experiment.set_ancestor(self.sample_a, self.owner)

        response = self.get(sample_id=self.sample_b.id)
        # Two rows on this sample, exactly one of them ancestral.
        self.assertEqual(response.content.decode().count("ancestral_table_row"), 1)

    def test_the_rows_are_there(self):
        """Tinted, not hidden, once asked for."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(sample_id=self.sample_b.id), str(self.mut_1.start_position))

    def test_the_summary_offers_hide(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.get(sample_id=self.sample_b.id).content.decode()
        self.assertIn("shaded red", body)
        self.assertIn("ancestral=hide", body)


class TestTheMemory(BreseqAncestorTestCase):

    def setUp(self):
        super().setUp()
        self.experiment.set_ancestor(self.sample_a, self.owner)

    def test_show_survives_a_reload_without_the_parameter(self):
        self.get(sample_id=self.sample_b.id, ancestral="show")
        self.assertContains(self.get(sample_id=self.sample_b.id), "ancestral_table_row")

    def test_hide_is_remembered_too(self):
        self.get(sample_id=self.sample_b.id, ancestral="show")
        self.get(sample_id=self.sample_b.id, ancestral="hide")
        self.assertNotContains(self.get(sample_id=self.sample_b.id), "ancestral_table_row")


class TestTheAncestorsOwnPage(BreseqAncestorTestCase):
    """Every row is ancestral there, so the toggle would empty the page: it always shows
    them, tinted, and offers no button."""

    def setUp(self):
        super().setUp()
        self.experiment.set_ancestor(self.sample_a, self.owner)

    def test_every_row_is_tinted_whatever_the_state(self):
        for params in ({}, {"ancestral": "hide"}):
            body = self.get(sample_id=self.sample_a.id, **params).content.decode()
            self.assertEqual(3, body.count("ancestral_table_row"))
            self.assertNotIn(BUTTON, body)
            self.assertIn("Rows shaded red", body)


class TestReachingTheAncestor(BreseqAncestorTestCase):

    def picker(self, response):
        """Just the sample dropdown. The assertion below has to be scoped to it: the legend
        links the ancestor by design, so "the page never mentions it" is the wrong question
        and stopped being true the moment that link was added."""
        body = response.content.decode()
        start = body.index('<ul class="dropdown-menu mutint-menu">')
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
        self.assertIn("sample-ancestor", picker)

    def test_only_the_ancestor_is_tinted_in_the_picker(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        picker = self.picker(self.get(sample_id=self.sample_b.id))
        self.assertEqual(picker.count("sample-ancestor\""), 1)

    def test_the_picker_is_untinted_without_a_designation(self):
        self.assertNotIn("sample-ancestor", self.picker(self.get()))

    def test_the_page_does_not_open_on_the_ancestor(self):
        """Listed first, but not what the page opens on: this view reads as "what evolved in
        this sample", and the one sample where the answer is "nothing, by definition" is a
        poor first thing to show. One click away, which is the point of listing it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertEqual(self.get().context["selected_sample"].id, self.sample_b.id)

    def test_an_experiment_of_only_the_ancestor_still_shows_it(self):
        self.sample_b.delete()
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertEqual(self.get().context["selected_sample"].id, self.sample_a.id)

    def test_its_own_page_still_opens(self):
        """`_selected_sample` resolves it outside the picker. Without that fallback a link to
        the ancestor renders a *different* sample and looks entirely normal doing it."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        response = self.get(sample_id=self.sample_a.id)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context["selected_sample"].id, self.sample_a.id)

    def test_its_own_page_says_what_it_is(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)
        self.assertContains(self.get(sample_id=self.sample_a.id), "designated ancestor")

    def test_a_sample_from_another_experiment_is_still_not_reachable(self):
        """The fallback is scoped to the experiment, so it is not a way around anything."""
        other = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        from mutint_experiment.models import Experiment
        foreign = Experiment.objects.get(pk=other["experiment_id"])
        response = self.client.get(BRESEQ, {"experiment_id": foreign.id,
                                            "sample_id": self.sample_a.id})
        self.assertNotEqual(getattr(response.context.get("selected_sample"), "id", None),
                            self.sample_a.id)


class TestWhatThePageClaims(BreseqAncestorTestCase):

    def get(self, **params):
        params.setdefault("ancestral", "show")
        return super().get(**params)

    def test_it_does_not_claim_to_have_subtracted(self):
        """The sentence says which state is in force. A page describing filtering it did not
        do is the failure `mutint_filter` is built to prevent, and it cuts both ways."""
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
        self.assertIn(self.sample_a.label, body)
        self.assertIn("sample_id=%d" % self.sample_a.id, body)

    def test_the_legend_is_its_own_row(self):
        """`.breseq-legend > div` carries the row spacing and the 9pt type, so this belongs in
        a div of its own rather than trailing the amino-acid color key -- it is not about how
        a cell is rendered, it is about rows being excluded everywhere else."""
        self.experiment.set_ancestor(self.sample_a, self.owner)
        body = self.get(sample_id=self.sample_b.id).content.decode()
        legend = body[body.index('class="breseq-legend"'):]
        red = legend.index("Rows shaded red")
        # The nearest tag opening before the text is this row's own <div>, not the color
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
