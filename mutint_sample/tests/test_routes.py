"""Which routes under /mutations/ exist, and which deliberately do not."""

from django.test import TestCase
from django.urls import Resolver404, resolve


class CompareLeftCoreTestCase(TestCase):
    """The URL space core gave up when Compare moved to the mutint-compare plugin.

    Only core's own routes are asserted here. Whether "Compare" appears in the sidebar is
    not core's business -- the nav registry is shared, its contents depend on what else is
    installed, and core is meant to have no knowledge of plugins at all. That belongs in
    mutint_compare's own tests, where it is.
    """

    def test_the_compare_route_is_gone(self):
        self.assertEqual(404, self.client.get("/mutations/").status_code)

    def test_the_per_sample_routes_still_resolve(self):
        """/mutations/ was the root of mutint_sample's include; its siblings are unaffected.

        Asserted through `resolve` rather than a request: `browse` answers 404 of its own
        accord without a mutation to open at, which would pass this test for the wrong
        reason."""
        from django.urls import Resolver404, resolve

        for url, view in (("/mutations/breseq", "breseq_table"),
                          ("/mutations/browse", "browse_mutation")):
            with self.subTest(url=url):
                try:
                    self.assertEqual(view, resolve(url).url_name)
                except Resolver404:
                    self.fail("%s no longer resolves" % url)
