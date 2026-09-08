"""What a table says about the subtraction it just did.

`{% view_filter_summary %}` is included once in `mutation_matrix/page.html`, so a sentence added
there reaches mutint-compare, mutint-fixation and mutint-converge without an edit to any of those
repositories -- the same leverage that got them their filter controls.

The sentence has to exist because the empty branch used to read "No filtering: every stored
mutation for this experiment is shown", which stops being true the moment an ancestor is
designated. A page that quietly drops rows while claiming to show all of them is worse than one
that says nothing.
"""

from django.template import Context, Template
from django.test import RequestFactory

from mutint_curate.tests.base import EditorTestCase

TEMPLATE = Template("{% load view_filter %}{% view_filter_summary %}")
OWN_PAGE = Template(
    "{% load view_filter %}{% view_filter_summary ancestral='own' %}")
TOGGLE = Template(
    "{% load view_filter %}{% view_filter_summary ancestral='toggle' %}")
TOGGLE_COUNTED = Template(
    "{% load view_filter %}{% view_filter_summary ancestral='toggle' ancestral_count=3 %}")
OWN_RULES = Template(
    "{% load view_filter %}{% view_filter_summary own_rules='Different rules here.' %}")
BUTTON = 'data-role="ancestral-toggle"'


class SummaryTestCase(EditorTestCase):

    def render(self, template=TEMPLATE, query=""):
        request = RequestFactory().get("/" + ("?" + query if query else ""))
        request.session = {}
        return template.render(Context({"experiment_id": self.experiment.id,
                                        "request": request}))


class TestWithoutAnAncestor(SummaryTestCase):

    def test_it_still_claims_every_stored_mutation(self):
        self.assertIn("every stored mutation", self.render())

    def test_it_names_no_ancestor(self):
        self.assertNotIn("designated ancestor", self.render())


class TestWithAnAncestor(SummaryTestCase):

    def setUp(self):
        super().setUp()
        self.experiment.set_ancestor(self.sample_a, self.owner)

    def test_it_names_the_ancestor(self):
        rendered = self.render()
        self.assertIn("designated ancestor", rendered)
        self.assertIn(self.sample_a.label, rendered)

    def test_it_stops_claiming_every_stored_mutation(self):
        """The lie this whole sentence exists to prevent."""
        self.assertNotIn("every stored mutation", self.render())

    def test_it_links_to_the_ancestor(self):
        self.assertIn("sample_id=%d" % self.sample_a.id, self.render())

    def test_the_ancestors_own_page_says_nothing(self):
        """There is nothing to subtract there, and the banner above already says what it is."""
        self.assertNotIn("designated ancestor", self.render(OWN_PAGE))

    def test_own_rules_does_not_suppress_it(self):
        """phylogeny and search pass `own_rules` to describe a different *frequency* rule --
        they are not opting out of the subtraction, and both still apply it."""
        rendered = self.render(OWN_RULES)
        self.assertIn("Different rules here.", rendered)
        self.assertIn("designated ancestor", rendered)

    def test_the_bare_tag_offers_no_button(self):
        """A button whose view ignores it is a control that does nothing."""
        self.assertNotIn(BUTTON, self.render())
        self.assertNotIn(BUTTON, self.render(OWN_RULES))
        self.assertIn("are excluded, and so is that sample", self.render())


class TestTheToggle(SummaryTestCase):
    """A page that draws the subtracted rows on request says which state is in force and
    offers the other one."""

    def setUp(self):
        super().setUp()
        self.experiment.set_ancestor(self.sample_a, self.owner)

    def test_hidden_by_default_and_offers_show(self):
        rendered = self.render(TOGGLE)
        self.assertIn("are hidden", rendered)
        self.assertIn(BUTTON, rendered)
        self.assertIn("ancestral=show", rendered)
        self.assertIn("Show ancestral mutations", rendered)
        self.assertNotIn("are excluded, and so is that sample", rendered)

    def test_shown_says_so_and_offers_hide(self):
        rendered = self.render(TOGGLE, query="ancestral=show")
        self.assertIn("shaded red", rendered)
        self.assertIn("still leaves them out", rendered)
        self.assertIn("ancestral=hide", rendered)

    def test_the_button_can_count(self):
        self.assertIn("Show 3 ancestral mutations", self.render(TOGGLE_COUNTED))

    def test_no_button_without_a_designation(self):
        self.experiment.clear_ancestor()
        rendered = self.render(TOGGLE)
        self.assertNotIn(BUTTON, rendered)
        self.assertIn("every stored mutation", rendered)
