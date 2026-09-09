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

import mutint_common

CORE = os.path.dirname(os.path.dirname(os.path.abspath(mutint_common.__file__)))
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
    """breseq's table markup is generated in Python, so its behavior cannot live in a page.

    `mutint_import.annotate.display` collapses a wide deletion's gene list behind a Show
    button, and every page rendering a row from `mutint_sample.breseq_report.build_rows` gets
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
        path = os.path.join(CORE, "mutint_common", "staticfiles", "js", "breseq_table.js")
        self.assertTrue(os.path.exists(path), "%s is missing" % self.JS)
        with open(path) as handle:
            script = handle.read()
        self.assertIn("breseq_gene_toggle", script)
        self.assertIn("breseq_gene_list", script)


class MutationMatrixAssetsTestCase(unittest.TestCase):
    """A page that renders `{% mutation_matrix %}` needs three assets, not one.

    The partial itself emits no <link> or <script>: the page owns its head. So a page that
    uses the tag and forgets the script gets a header and two menus that do nothing, and no
    error anywhere. Same rule, same shape as the breseq pair above.
    """

    ASSETS = ("css/breseq_table.css", "js/breseq_table.js", "js/mutation_matrix.js")

    def test_every_template_using_the_tag_links_all_three(self):
        users, missing = 0, []
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            if "{% mutation_matrix " not in text:
                continue
            users += 1
            for asset in self.ASSETS:
                if asset not in text:
                    missing.append("%s lacks %s" % (os.path.relpath(path, CORE), asset))
        self.assertGreaterEqual(users, 1, "expected a template to render the matrix")
        self.assertEqual([], missing)

    def test_the_script_exists(self):
        path = os.path.join(CORE, "mutint_common", "staticfiles", "js", "mutation_matrix.js")
        self.assertTrue(os.path.exists(path))


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
        with open(os.path.join(CORE, "mutint_common", "templates", "base.html")) as handle:
            return handle.read()

    #: A DataTables combined build that carries Bootstrap. These were `cdn.datatables.net/v/bs-`
    #: URLs and are vendored under this directory prefix now -- the naming is what these two
    #: tests recognize a Bootstrap-carrying bundle by, so keep it if the layout ever moves.
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

    def test_the_bundle_is_still_recognizable(self):
        """Guards the guard, and it is not hypothetical.

        Both tests here located Bootstrap by searching for a `cdn.datatables.net` URL. When the
        assets were vendored those strings vanished, and `test_no_standalone_bootstrap_beside_
        the_bundle` did not fail -- it started passing *vacuously*, with `bundles_bootstrap`
        false, silently stopping guarding anything. A test that cannot find its subject must
        say so rather than agree.
        """
        self.assertIn(self.BOOTSTRAP_BUNDLE, self._base_html())


class ButtonsAreNotFloatedTestCase(unittest.TestCase):
    """`.btn { float: right }` applied to every button in the app.

    Floating takes an element out of the flow, so a "+ New ..." control landed at
    the right edge and rode up out of line with the toolbar it should sit above.
    Buttons that want to sit right belong in a .pull-right container, one at a time.
    """

    def _common_css(self):
        with open(os.path.join(CORE, "mutint_common", "staticfiles", "css", "common.css")) as handle:
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
    """The sidebar names the project and the experiment on two rows of its own.

    The project's row sits under the Projects entry and the experiment's heads its pages,
    and each is bold. They were one row reading `{{ project_name }}: {{ experiment_name }}`.

    The two names have to stay apart in the context for either row to be right, and that is
    the older bug these tests were written for: `Experiment.experiment_context()` used to
    return a *composed* "project: experiment" under the experiment key and no project key at
    all, so a view that simply trusted it rendered a stray leading colon, and one that added
    the project name without also overriding the composed one rendered the project twice.
    Every experiment-scoped view carried its own workaround; the ones that did not carried
    the bug -- the sample edit pages had the colon, the genome browser and the Import data
    page had the doubled name.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/project/create/", {"name": "Proj", "experiment": "Exp"}).json()
        from mutint_experiment.models import Experiment

        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

    def test_the_context_keeps_the_names_apart(self):
        context = self.experiment.experiment_context()

        self.assertEqual("Exp", context["experiment_name"])
        self.assertEqual("Proj", context["project_name"])
        self.assertEqual(self.experiment.project_id, context["project_id"])

    def test_a_project_less_experiment_gives_an_empty_name_rather_than_raising(self):
        """`Experiment.project` is nullable, and this used to be `self.project.name`."""
        self.experiment.project = None
        self.experiment.save()

        self.assertEqual("", self.experiment.experiment_context()["project_name"])

    def _html(self, url):
        return self.client.get(url, follow=True).content.decode()

    def _sidebar_label(self, html):
        import re

        match = re.search(
            r'<a href="/stats\?experiment_id=%d"[^>]*><b>(.*?)</b>' % self.experiment.id,
            html)
        return match.group(1).strip() if match else None

    def _project_row(self, html):
        import re

        match = re.search(
            r'<a href="/project/%d/"[^>]*><b>(.*?)</b>' % self.experiment.project_id, html)
        return match.group(1).strip() if match else None

    def test_every_experiment_page_labels_it_the_same_way(self):
        """The experiment's name alone. No leading colon, no doubled project -- on the pages
        that used to have each -- and no project prefix, which every page had."""
        pages = {
            "edit samples": "/experiment/%d/samples/" % self.experiment.id,
            "add data": "/import/?experiment_id=%d" % self.experiment.id,
            "overview": "/stats/?experiment_id=%d" % self.experiment.id,
        }
        for name, url in pages.items():
            with self.subTest(page=name):
                self.assertEqual("Exp", self._sidebar_label(self._html(url)))

    def test_the_project_row_sits_between_projects_and_experiments(self):
        """Position is the whole point of the `key` base.html matches on: the row belongs to
        the entry that lists projects, not to the end of the main section."""
        html = self._html("/stats/?experiment_id=%d" % self.experiment.id)

        self.assertEqual("Proj", self._project_row(html))
        menu = html[html.index('id="side-menu"'):]
        self.assertLess(menu.index('href="/project/"'),
                        menu.index('href="/project/%d/"' % self.experiment.project_id))
        self.assertLess(menu.index('href="/project/%d/"' % self.experiment.project_id),
                        menu.index('href="/experiment/"'))

    def test_a_project_page_names_the_project_and_no_experiment(self):
        """`project_detail` supplies the two keys itself -- nothing else on that page would,
        and before it did the sidebar named the selected project nowhere."""
        html = self._html("/project/%d/" % self.experiment.project_id)

        self.assertEqual("Proj", self._project_row(html))
        self.assertIsNone(self._sidebar_label(html))


class PreferencesScriptTestCase(unittest.TestCase):
    """The client half of per-user preferences is one script, `mutint_preferences.js`.

    It was private to `mutation_matrix.js` until a second page -- the per-sample Mutations
    page's References menu -- needed to remember something, and two copies of the store
    would be two opinions about where a choice lives. Same rule as the CSRF cookie read in
    `mutint_crud.js`: one definition.
    """

    STATIC = os.path.join(CORE, "mutint_common", "staticfiles", "js")
    STORE = "mutint_preferences.js"
    CALLERS = ("mutation_matrix.js", "breseq_references.js", "mutint_control_tabs.js")

    def read(self, name):
        with open(os.path.join(self.STATIC, name)) as handle:
            return handle.read()

    def test_base_loads_it_after_the_script_that_owns_the_save(self):
        with open(os.path.join(CORE, "mutint_common", "templates", "base.html")) as handle:
            base = handle.read()
        self.assertIn(self.STORE, base)
        self.assertLess(base.index("mutint_crud.js"), base.index(self.STORE))
        self.assertIn(self.STORE + '" %}?v={{ asset_version }}', base)

    def test_the_store_is_defined_once(self):
        store = self.read(self.STORE)
        for marker in ("localStorage", "mutintPostJson", "window.mutintPreferences ="):
            self.assertIn(marker, store)
        for caller in self.CALLERS:
            script = self.read(caller)
            self.assertIn("window.mutintPreferences(", script, caller)
            for marker in ("localStorage", "mutintPostJson"):
                self.assertNotIn(marker, script, "%s carries its own store" % caller)


class AssetVersionTestCase(unittest.TestCase):
    """Every first-party asset is linked with `asset_version`, never the bare version.

    The bare version changes at a release; an install on the Development channel moves
    between commits without one, and a browser then keeps the previous commit's script under
    an unchanged URL -- the matrix drew new rows with old code for exactly that reason.
    `asset_version` folds every component's git revision in.
    """

    def test_no_template_links_an_asset_with_the_bare_version(self):
        names, users = set(), 0
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            found = re.findall(r"\?v=\{\{\s*(\w+)\s*\}\}", text)
            if found:
                users += 1
            names.update(found)
        self.assertGreater(users, 1, "expected several templates to version their assets")
        self.assertEqual({"asset_version"}, names)


class ControlTabsScriptTestCase(unittest.TestCase):
    """The tab strip above a mutation table: one partial, one script, Bootstrap's plugin."""

    def read(self, *parts):
        with open(os.path.join(CORE, *parts)) as handle:
            return handle.read()

    def test_base_loads_the_script_after_the_store(self):
        base = self.read("mutint_common", "templates", "base.html")
        self.assertIn('mutint_control_tabs.js" %}?v={{ asset_version }}', base)
        self.assertLess(base.index("mutint_preferences.js"), base.index("mutint_control_tabs.js"))

    def test_the_script_restores_and_saves(self):
        script = self.read("mutint_common", "staticfiles", "js", "mutint_control_tabs.js")
        self.assertIn("[data-control-tabs]", script)
        self.assertIn("shown.bs.tab", script)

    def test_every_strip_is_the_shared_partial(self):
        """A second hand-written strip is how two pages come to disagree about the tabs."""
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            if 'data-toggle="tab"' in text and not path.endswith("control_tabs.html"):
                self.fail("%s writes its own tab strip; include control_tabs.html"
                          % os.path.relpath(path, CORE))

    def test_no_template_loads_bootstrap_on_its_own(self):
        """The plugin arrives inside the DataTables bundle; a second copy binds every
        data-api handler twice -- see the comment in base.html."""
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            self.assertNotRegex(text, r"bootstrap-[\d.]+/js/bootstrap\.min\.js",
                                os.path.relpath(path, CORE))
