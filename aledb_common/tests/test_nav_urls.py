"""Every registered sidebar entry points at a route that exists.

`nav_registry` skips an entry whose `url_name` will not reverse, so a plugin's typo cannot
500 a page. **It cannot do that for an entry given a literal `url`** -- there is nothing to
reverse and nothing to fail, so a path that has stopped existing renders as an ordinary link
on every page in the site and 404s only when somebody clicks it.

Core's own entries are all literal paths, deliberately: several of these routes rely on an
`APPEND_SLASH` redirect (`/mutations` -> `/mutations/`) that reversing would silently change.
So the guard has to be this, asked of the registry rather than of a hard-coded list -- which
is also what makes it cover a plugin's entries in the assembled project, where the same
mistake is likelier because the plugin cannot see core's urlconf.

The `/` variant is accepted because `APPEND_SLASH` is how several of these are meant to be
reached; what is not accepted is a path that resolves neither way.
"""

from django.test import TestCase
from django.urls import Resolver404, resolve

from aledb_common.nav_registry import EXPERIMENT_SECTION, MAIN_SECTION, get_nav_items


class NavUrlTestCase(TestCase):

    def nav_items(self):
        items = get_nav_items(MAIN_SECTION) + get_nav_items(EXPERIMENT_SECTION)
        self.assertTrue(items, "nothing registered -- this test would prove nothing")
        return items

    def test_every_literal_nav_url_resolves(self):
        for item in self.nav_items():
            url = (item.get("url") or "").split("?")[0]
            if not url:
                continue  # a url_name entry: the registry already skips it if it breaks
            with self.subTest(label=item["label"], url=url):
                candidates = [url] if url.endswith("/") else [url, url + "/"]
                for candidate in candidates:
                    try:
                        resolve(candidate)
                        break
                    except Resolver404:
                        continue
                else:
                    self.fail("%r points at %s, which resolves to nothing -- the sidebar "
                              "would show it on every page and 404 on click"
                              % (item["label"], url))

    def test_a_url_name_entry_that_cannot_reverse_is_dropped(self):
        """The other half, restated: this is why a `url_name` entry needs no guard."""
        from aledb_common import nav_registry

        before = len(get_nav_items(MAIN_SECTION))
        registered = list(nav_registry._nav_items)
        self.addCleanup(lambda: nav_registry._nav_items.__setitem__(
            slice(None), registered))

        nav_registry.register_nav_item("Nowhere", url_name="no_such_route_at_all",
                                       section=MAIN_SECTION)
        self.assertEqual(before, len(get_nav_items(MAIN_SECTION)))
