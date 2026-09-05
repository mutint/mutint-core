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

from mutint_mutation_editor.tests.base import EditorTestCase

TEMPLATE = Template("{% load view_filter %}{% view_filter_summary %}")
NO_SUBTRACTION = Template(
    "{% load view_filter %}{% view_filter_summary ancestor_subtracted=False %}")
OWN_RULES = Template(
    "{% load view_filter %}{% view_filter_summary own_rules='Different rules here.' %}")


class SummaryTestCase(EditorTestCase):

    def render(self, template=TEMPLATE):
        request = RequestFactory().get("/")
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

    def test_a_page_that_does_not_subtract_can_say_so(self):
        """Exactly one page passes this: the per-sample breseq table, which tints instead."""
        self.assertNotIn("designated ancestor", self.render(NO_SUBTRACTION))

    def test_own_rules_does_not_suppress_it(self):
        """phylogeny and search pass `own_rules` to describe a different *frequency* rule --
        they are not opting out of the subtraction, and both still apply it."""
        rendered = self.render(OWN_RULES)
        self.assertIn("Different rules here.", rendered)
        self.assertIn("designated ancestor", rendered)
