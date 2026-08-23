"""Template and asset hygiene -- things a rendering test would not catch.

`{# ... #}` is a *single-line* construct in Django. Spanning it across lines does
not comment anything out -- the text renders into the page verbatim, and nothing
raises. It shipped that way on the project list, where the reader saw a paragraph
of implementation notes above the table.
"""

import os
import re
import unittest

import aledb_common

CORE = os.path.dirname(os.path.dirname(os.path.abspath(aledb_common.__file__)))
COMMENT = re.compile(r"\{#(.*?)#\}", re.S)
SKIP = ("/env/", "/node_modules/", "/.claude/", "/staticfiles/")


def _templates():
    for root, _dirs, files in os.walk(CORE):
        if any(part in root + "/" for part in SKIP):
            continue
        for name in files:
            if name.endswith(".html"):
                yield os.path.join(root, name)


class TemplateCommentsTestCase(unittest.TestCase):

    def test_no_multiline_hash_comments(self):
        offenders = []
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            for match in COMMENT.finditer(text):
                if "\n" in match.group(1):
                    line = text[:match.start()].count("\n") + 1
                    offenders.append("%s:%d" % (os.path.relpath(path, CORE), line))
        self.assertEqual(
            [], offenders,
            "{# #} does not span lines -- use {%% comment %%}. Offenders: %s"
            % ", ".join(offenders))

    def test_the_check_can_actually_see_templates(self):
        """Guards the walk itself: a bad root would make the test above vacuous."""
        found = list(_templates())
        self.assertGreater(len(found), 20)
        self.assertTrue(any(p.endswith("base.html") for p in found))


class BootstrapLoadedOnceTestCase(unittest.TestCase):
    """Bootstrap's JS must be evaluated exactly once.

    Its data-api binds delegated click handlers on document, so a second copy makes
    every [data-toggle] fire twice. A modal opened and shut inside the same
    millisecond, which looked like the modal "not working" rather than like a
    duplicate script.

    The second copy was not obvious from the script tags: a DataTables *combined*
    build bundles its styling framework, so the bs-3.3.7 variant carries Bootstrap
    itself.
    """

    def _base_html(self):
        with open(os.path.join(CORE, "aledb_common", "templates", "base.html")) as handle:
            return handle.read()

    def test_no_standalone_bootstrap_beside_the_bundle(self):
        html = self._base_html()
        bundles_bootstrap = "cdn.datatables.net/v/bs-" in html
        # Not a bare "bootstrap.min.js" search: dataTables.bootstrap.min.js is the
        # DataTables styling integration and contains that as a substring.
        standalone = re.search(r"bootstrap/[\d.]+/js/bootstrap\.min\.js", html) is not None
        self.assertFalse(
            bundles_bootstrap and standalone,
            "base.html loads a bs- DataTables bundle (which contains Bootstrap) and "
            "bootstrap.min.js as well -- that is two copies of Bootstrap's data-api")

    def test_bootstrap_toggle_comes_after_bootstrap(self):
        """It extends Bootstrap, so loading it first leaves $.fn.bootstrapToggle undefined."""
        html = self._base_html()
        bundle = html.find("cdn.datatables.net/v/bs-3.3.7")
        toggle = html.find("bootstrap-toggle.min.js")
        self.assertNotEqual(-1, bundle)
        self.assertNotEqual(-1, toggle)
        self.assertLess(bundle, toggle,
                        "bootstrap-toggle must follow the bundle that provides Bootstrap")


class ButtonsAreNotFloatedTestCase(unittest.TestCase):
    """`.btn { float: right }` applied to every button in the app.

    Floating takes an element out of the flow, so a "+ New ..." control landed at
    the right edge and rode up out of line with the toolbar it should sit above.
    Buttons that want to sit right belong in a .pull-right container, one at a time.
    """

    def _common_css(self):
        with open(os.path.join(CORE, "aledb_common", "staticfiles", "css", "common.css")) as handle:
            return handle.read()

    def test_no_blanket_float_on_btn(self):
        # Strip comments first: the rule is described in one, so a naive search
        # matches the explanation of the bug rather than the bug.
        css = re.sub(r"/\*.*?\*/", "", self._common_css(), flags=re.S)
        match = re.search(r"(?<![\w.+>~-])\.btn\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "expected a .btn rule to still exist")
        self.assertNotIn("float", match.group(1),
                         "an unscoped .btn rule must not float every button in the app")
