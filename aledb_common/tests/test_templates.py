"""Template and asset hygiene -- things a rendering test would not catch.

`{# ... #}` is a *single-line* construct in Django. Spanning it across lines does
not comment anything out -- the text renders into the page verbatim, and nothing
raises. It shipped that way on the project list, where the reader saw a paragraph
of implementation notes above the table.
"""

import os
import re
import unittest

from django.test import TestCase

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


class BreseqTableAssetsTravelTogetherTestCase(unittest.TestCase):
    """breseq's table markup is generated in Python, so its behaviour cannot live in a page.

    `aledb_import.annotate.display` collapses a wide deletion's gene list behind a Show
    button, and every page rendering a row from `aledb_seq.breseq_report.build_rows` gets
    that markup: the Samples page, the genome browser and the mutation editor's Edit/Delete
    listing. The click handler was an inline script in the first of those three, so on the
    other two the button rendered, was styled by the shared stylesheet, and did nothing.

    The rule now is that the two assets are one thing -- link `breseq_table.css`, load
    `breseq_table.js` -- with no exception for a page that has no Description column today,
    because deciding which pages need it per page is what went wrong.
    """

    CSS = "css/breseq_table.css"
    JS = "js/breseq_table.js"

    def test_every_template_with_the_stylesheet_loads_the_script(self):
        missing = []
        users = 0
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            if self.CSS not in text:
                continue
            users += 1
            if self.JS not in text:
                missing.append(os.path.relpath(path, CORE))

        self.assertGreater(users, 1, "expected several templates to link %s" % self.CSS)
        self.assertEqual(
            [], missing,
            "these link %s without loading %s, so the Show button on a collapsed gene list "
            "renders and does nothing: %s" % (self.CSS, self.JS, ", ".join(missing)))

    def test_the_script_exists_and_drives_the_generated_class_names(self):
        """The classes are underscored because htmlize() rewrites a hyphen as &#8209;."""
        path = os.path.join(CORE, "aledb_common", "staticfiles", "js", "breseq_table.js")
        self.assertTrue(os.path.exists(path), "%s is missing" % self.JS)
        with open(path) as handle:
            script = handle.read()
        self.assertIn("breseq_gene_toggle", script)
        self.assertIn("breseq_gene_list", script)


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

    #: A DataTables combined build that carries Bootstrap. These were `cdn.datatables.net/v/bs-`
    #: URLs and are vendored under this directory prefix now -- the naming is what these two
    #: tests recognise a Bootstrap-carrying bundle by, so keep it if the layout ever moves.
    BOOTSTRAP_BUNDLE = "vendor/datatables-bundle-bs"

    def test_no_standalone_bootstrap_beside_the_bundle(self):
        html = self._base_html()
        bundles_bootstrap = self.BOOTSTRAP_BUNDLE in html
        # Not a bare "bootstrap.min.js" search: dataTables.bootstrap.min.js is the
        # DataTables styling integration and contains that as a substring.
        standalone = re.search(r"bootstrap-[\d.]+/js/bootstrap\.min\.js", html) is not None
        self.assertFalse(
            bundles_bootstrap and standalone,
            "base.html loads a bs- DataTables bundle (which contains Bootstrap) and "
            "bootstrap.min.js as well -- that is two copies of Bootstrap's data-api")

    def test_the_bundle_is_still_recognisable(self):
        """Guards the guard, and it is not hypothetical.

        Both tests here located Bootstrap by searching for a `cdn.datatables.net` URL. When the
        assets were vendored those strings vanished, and `test_no_standalone_bootstrap_beside_
        the_bundle` did not fail -- it started passing *vacuously*, with `bundles_bootstrap`
        false, silently stopping guarding anything. A test that cannot find its subject must
        say so rather than agree.
        """
        self.assertIn(self.BOOTSTRAP_BUNDLE, self._base_html())

    def test_bootstrap_toggle_comes_after_bootstrap(self):
        """It extends Bootstrap, so loading it first leaves $.fn.bootstrapToggle undefined."""
        html = self._base_html()
        bundle = html.find(self.BOOTSTRAP_BUNDLE)
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


class ExperimentSidebarLabelTestCase(TestCase):
    """base.html joins the two names itself: `{{ ale_project_name }}: {{ ale_experiment_name }}`.

    `Experiment.experiment_context()` used to return a *composed* "project: experiment"
    under the experiment key and no project key at all, so a view that simply trusted it
    rendered a stray leading colon, and one that added the project name without also
    overriding the composed one rendered the project twice. Every experiment-scoped view
    carried its own workaround; the ones that did not carried the bug -- the sample edit
    pages had the colon, the genome browser and the Add Data page had the doubled name.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "Proj", "experiment": "Exp"}).json()
        from aledb_experiment.models import Experiment

        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

    def test_the_context_keeps_the_names_apart(self):
        context = self.experiment.experiment_context()

        self.assertEqual("Exp", context["ale_experiment_name"])
        self.assertEqual("Proj", context["ale_project_name"])
        self.assertEqual(self.experiment.project_id, context["ale_project_id"])

    def test_a_project_less_experiment_gives_an_empty_name_rather_than_raising(self):
        """`Experiment.project` is nullable, and this used to be `self.project.name`."""
        self.experiment.project = None
        self.experiment.save()

        self.assertEqual("", self.experiment.experiment_context()["ale_project_name"])

    def _sidebar_label(self, url):
        import re

        html = self.client.get(url, follow=True).content.decode()
        match = re.search(
            r'<a href="/stats\?ale_experiment_id=%d"><b>(.*?)</b>' % self.experiment.id,
            html)
        return match.group(1).strip() if match else None

    def test_every_experiment_page_labels_it_the_same_way(self):
        """No leading colon, no doubled project -- on the pages that used to have each."""
        pages = {
            "edit samples": "/ale/experiment/%d/samples/" % self.experiment.id,
            "add data": "/import/add/?ale_experiment_id=%d" % self.experiment.id,
            "overview": "/stats/?ale_experiment_id=%d" % self.experiment.id,
            "metadata": "/metadata/?ale_experiment_id=%d" % self.experiment.id,
        }
        for name, url in pages.items():
            with self.subTest(page=name):
                self.assertEqual("Proj: Exp", self._sidebar_label(url))
