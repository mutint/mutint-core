"""Every asset the browser loads must come from this deployment.

The reason `igv.min.js` and phylotree are vendored is that a deployment should work with no
outbound network. That was not true for years: `base.html` could not render without jQuery from
`ajax.googleapis.com`, and the suite pulled 22 assets from seven CDN hosts. Nothing was broken,
so nobody noticed -- **which is the whole difficulty**. A missing CDN is invisible to everyone
who has a working connection, and the failure it eventually produces is a page with no styling
and dead JavaScript, in an environment where nobody can debug it.

So the property is asserted rather than intended. See
`mutint_common/staticfiles/vendor/VENDOR.md`.
"""

import os
import re

from django.test import TestCase

from mutint_common.about_registry import first_party_app_configs

#: The only two allowed, each for a stated reason. Add nothing here without one: the point of
#: this test is that "we need this from the internet" is a decision somebody argues for, not a
#: thing that accumulates.
ALLOWED_PREFIXES = (
    # NCBI's Sequence Viewer is a client for NCBI's own backend -- the page it draws needs
    # ncbi.nlm.nih.gov reachable whatever we do with the script file, so vendoring buys
    # nothing. Offline it never reaches a verified contig and never renders the script at
    # all, which `mutint_sample/tests/test_ncbi_view.py` asserts directly.
    "https://www.ncbi.nlm.nih.gov/projects/sviewer/js/sviewer.js",
    # Analytics, and **conditional**: the tag sits inside `{% if GOOGLE_ANALYTICS_TAG %}` and
    # that setting defaults to ''. So a deployment that has not configured analytics makes no
    # request here at all, and one that has, has chosen to. It rendered unconditionally until
    # this test was written -- every page of every deployment called Google, reporting to an
    # empty tag id. This scan cannot see an `{% if %}`, so the exemption is by URL and the
    # condition is the thing to preserve.
    "https://www.googletagmanager.com/gtag/js",
)

#: Only asset *loads*. A plain `<a href="https://www.ncbi.nlm.nih.gov/nuccore/...">` is an
#: ordinary hyperlink -- it costs nothing offline and simply does not work when clicked, which
#: is expected. Confusing the two would forbid linking to the outside world at all.
ASSET_LOAD = re.compile(
    r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["'](https?://[^"']+)["']""",
    re.IGNORECASE | re.DOTALL)

#: Django comments render nothing, so a URL inside one is not a load. `dashboard.html` carries a
#: commented-out d3 include that would otherwise fail this test for no reason.
DJANGO_COMMENT = re.compile(r"\{#.*?#\}|\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.DOTALL)


def _templates():
    """Every .html shipped by a first-party app, as (label, path).

    `first_party_app_configs` is the same predicate the About page inventories with and the
    test runner picks apps by, so a new app is covered without editing this file, and a
    third-party package's templates are never scanned.
    """
    found = []
    for app_config in first_party_app_configs():
        for dirpath, _dirs, files in os.walk(app_config.path):
            for name in files:
                if name.endswith(".html"):
                    found.append((app_config.label, os.path.join(dirpath, name)))
    return found


class OfflineAssetsTestCase(TestCase):
    def test_no_template_loads_an_asset_from_another_host(self):
        offenders = []
        for label, path in _templates():
            body = DJANGO_COMMENT.sub("", open(path, encoding="utf-8").read())
            for url in ASSET_LOAD.findall(body):
                if url.startswith(ALLOWED_PREFIXES):
                    continue
                offenders.append("%s: %s" % (os.path.relpath(path), url))

        self.assertEqual([], sorted(offenders), "\n".join([
            "",
            "These templates load an asset from outside this deployment, so the app does not",
            "work without a connection to them:",
            "",
        ] + ["  " + line for line in sorted(offenders)] + [
            "",
            "Vendor it under mutint_common/staticfiles/vendor/<name>-<version>/ and record it",
            "in that directory's VENDOR.md. If a stylesheet, bring the fonts it asks for by",
            "url() as well -- missing ones render as blank boxes with no error.",
        ]))

    def test_the_scan_actually_finds_things(self):
        """Guards the guard: a regex that matched nothing would pass the test above forever."""
        sample = '<script src="https://example.com/x.js"></script><link href="https://e.com/a.css">'
        self.assertEqual(["https://example.com/x.js", "https://e.com/a.css"],
                         ASSET_LOAD.findall(sample))

    def test_a_plain_hyperlink_is_not_an_asset_load(self):
        """The NCBI accession links on the Reference page are ordinary links and must stay."""
        self.assertEqual([], ASSET_LOAD.findall(
            '<a href="https://www.ncbi.nlm.nih.gov/nuccore/NC_000913.3">NC_000913.3</a>'))

    def test_a_commented_out_include_is_not_a_load(self):
        self.assertEqual(
            "", DJANGO_COMMENT.sub("", '{#    <script src="https://cdn/d3.js"></script>#}'))

    def test_there_are_templates_to_scan(self):
        """If app discovery ever returns nothing, every assertion above passes vacuously."""
        self.assertGreater(len(_templates()), 20)
