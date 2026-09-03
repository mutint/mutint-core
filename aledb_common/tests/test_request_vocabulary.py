"""The filter form's parameter names reach the template.

`base_table_template.html` used to write `name="ale_no"` and `value="population"` as
literals. They are context variables now, so that the query-string vocabulary can be renamed
in `constants.py` alone -- but a context variable that does not arrive renders as the empty
string, and Django says nothing. The form would then post `?=1` instead of `?ale_no=1`, every
picker would stop filtering, and the page would still be a page.

So this asserts the wiring rather than the words: whatever `constants.py` says, that is what
comes out of the form. Dropping `request_vocabulary` from the context processors fails here.

The template is rendered directly rather than through a page, because **no aledb-core page
renders it** -- aledb-compare, aledb-converge and aledb-fixation do. Core owns the template
and the context processor, so core is where the seam between them belongs; a test living in
one plugin would leave the other two believing someone else checked.
"""

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.template import loader
from django.test import RequestFactory, TestCase

from aledb_common import constants


class RequestVocabularyTestCase(TestCase):

    def render(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        # The reader's view filter is session-scoped and `{% view_filter_summary %}` reads
        # it, so a bare RequestFactory request is not enough to render this template.
        SessionMiddleware(lambda r: None).process_request(request)
        # `ale_experiment_id` gates the whole control block -- without an experiment
        # selected the page renders no pickers at all, and every assertion below would
        # pass against an empty form.
        return loader.get_template("base_table_template.html").render(
            {"ale_experiment_id": 1, "ales": ["1"], "mutations": []}, request)

    def test_the_pickers_are_named_from_constants(self):
        html = self.render()
        self.assertIn('name="%s"' % constants.REQUEST_ALE_ID, html)
        self.assertIn('name="%s"' % constants.REQUEST_SAMPLE_TYPE, html)

    def test_the_sample_type_options_carry_the_constant_values(self):
        html = self.render()
        self.assertIn('value="%s"' % constants.SAMPLE_TYPE_CLONAL, html)
        self.assertIn('value="%s"' % constants.SAMPLE_TYPE_MIXED, html)
        self.assertIn('value="%s"' % constants.REQUEST_ALL, html)

    def test_no_empty_name_attribute_survives(self):
        """The exact shape of the failure: a missing context variable renders `name=""`."""
        self.assertNotIn('name=""', self.render())
