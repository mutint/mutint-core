"""`{% import_tabs %}` and the panel it draws under the strip.

**The trap this file exists for.** `InclusionNode.render` builds its template's context with
`Context.new(...)`, which keeps the builtins and discards everything else -- so nothing the
page has reaches `_tabs.html` unless the tag put it in the dict it returns. No `request`, no
`user`, and crucially no `asset_version`, which comes from a context processor.

A missing `asset_version` fails **silently**: `?v={{ asset_version }}` renders as `?v=`, and a
browser then serves the previous release's script from cache under an unchanged URL -- which
is the exact failure `get_asset_version` was added to prevent. `AssetVersionTestCase` scans
template *text* for the right variable name and would pass either way, so the assertion has to
be made against what the tag actually returns and what the partial actually renders.
"""

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from mutint_experiment.models import Experiment, Project
from mutint_import.templatetags.import_tabs import import_tabs


class ImportTabsContextTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.project = Project.objects.create(name="P", user=self.user)
        self.experiment = Experiment.objects.create(name="E", project=self.project)
        request = RequestFactory().get("/import/")
        request.user = self.user
        self.request = request

    def _context(self, **extra):
        base = {"experiment_id": self.experiment.id, "request": self.request}
        base.update(extra)
        return import_tabs(base, "reference")

    def test_it_carries_everything_the_partial_reads(self):
        found = self._context()
        self.assertTrue(found["asset_version"])
        self.assertEqual(self.experiment.id, found["experiment_id"])
        self.assertEqual("/import/annotators/status", found["status_url"])
        self.assertEqual({"busy", "stalled", "jobs"}, set(found["annotation_status"]))

    def test_it_takes_a_status_the_page_already_worked_out(self):
        """Core's import view needs the same answer for its own script, so the tag takes
        that rather than asking a second time per render."""
        theirs = {"busy": True, "stalled": None, "jobs": []}
        self.assertIs(theirs, self._context(annotation_status=theirs)["annotation_status"])

    def test_without_an_experiment_it_renders_nothing(self):
        self.assertEqual({"tabs": [], "active": "reference"},
                         import_tabs({"request": self.request}, "reference"))


class PluginPageRenderTestCase(TestCase):
    """A plugin's tab page -- mutint-breseq's launcher, mutint-refsniff's -- loads the tag
    with only an `experiment_id` in context, and must still get a versioned script."""

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.project = Project.objects.create(name="P", user=self.user)
        self.experiment = Experiment.objects.create(name="E", project=self.project)

    def _render(self):
        request = RequestFactory().get("/somewhere/")
        request.user = self.user
        return Template("{% load import_tabs %}{% import_tabs 'reference' %}").render(
            Context({"experiment_id": self.experiment.id, "request": request}))

    def test_the_script_is_versioned(self):
        html = self._render()
        self.assertIn("mutint_annotation_status.js", html)
        self.assertNotIn("?v=\"", html)
        self.assertNotIn("?v='", html)

    def test_the_panel_is_there_and_hidden(self):
        html = self._render()
        self.assertIn('id="mutint-annotation-panel"', html)
        self.assertIn("display: none", html)
        self.assertIn('data-experiment="%s"' % self.experiment.id, html)
        self.assertIn('id="mutint-annotation-status"', html)
