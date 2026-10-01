"""The query-string vocabulary reaches the templates.

Templates write `{{ PARAM_POPULATION }}` and the sample-type values rather than literals, so
the vocabulary can be renamed in `constants.py` alone -- but a context variable that does not
arrive renders as the empty string, and Django says nothing. A link would then carry `&=1`
instead of `&population=1`, and the page would still be a page.

So this asserts the wiring rather than the words: whatever `constants.py` says is what a
template rendered with a request gets. Dropping `request_vocabulary` from the context
processors fails here. Two templates link with it -- the per-sample Mutations page's picker and
mutint-needle's panel -- and Compare, whose pickers were the first user, now decides
everything in the browser and has none: `test_the_matrix_page_has_no_server_pickers` holds it
to that, since a picker posting to a view that ignores it would be a control that does
nothing.
"""

from collections import OrderedDict

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.template import loader
from django.test import RequestFactory, TestCase

from mutint_common import constants
from mutint_sample.mutation_matrix import build_matrix


class RequestVocabularyTestCase(TestCase):

    def render(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        # The page's tag reads the reader's preferences, so a bare request is not enough.
        SessionMiddleware(lambda r: None).process_request(request)
        # `experiment_id` gates the whole control block -- without an experiment
        # selected the page renders no pickers at all, and every assertion below would
        # pass against an empty form.
        return loader.get_template("mutation_matrix/page.html").render(
            {"experiment_id": 1, "matrix": build_matrix([], OrderedDict()),
             "view_filter_state": {"min_freq": None, "max_freq": None, "genes": []},
             "empty_message": "Nothing."}, request)

    def test_the_vocabulary_reaches_a_template(self):
        from django.template import Template
        from django.template.context import RequestContext

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        html = Template("{{ PARAM_POPULATION }}|{{ PARAM_SAMPLE_TYPE }}|{{ SAMPLE_TYPE_CLONAL }}|"
                        "{{ SAMPLE_TYPE_MIXED }}|{{ PARAM_ALL }}").render(RequestContext(request))
        self.assertEqual("|".join([constants.REQUEST_POPULATION, constants.REQUEST_SAMPLE_TYPE,
                                   constants.SAMPLE_TYPE_CLONAL, constants.SAMPLE_TYPE_MIXED,
                                   constants.REQUEST_ALL]), html)

    def test_the_matrix_page_has_no_server_pickers(self):
        html = self.render()
        self.assertNotIn('name="%s"' % constants.REQUEST_POPULATION, html)
        self.assertNotIn('name="%s"' % constants.REQUEST_SAMPLE_TYPE, html)
        self.assertIn('data-role="sample-types"', html)
        self.assertIn('data-role="populations"', html)

    def test_no_empty_name_attribute_survives(self):
        """The exact shape of the failure: a missing context variable renders `name=""`."""
        self.assertNotIn('name=""', self.render())
