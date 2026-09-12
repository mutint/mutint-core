"""The seam that lets a component put its own content on the experiment Overview.

Core registers one panel of its own -- Storage, from `mutint_experiment` -- and the needle
plot, which was the last thing written into `/stats` directly, is the mutint-needle component
now. Every test here still registers its own and unregisters it afterwards, which is the only
way core *can* test the mechanism: asserting on what is absent from a shared registry is a
statement about the install set rather than about core, and would fail the moment a component
that registers a panel is installed beside it.

**Every assertion here is about this test's own panels, never about the whole list.** These
were written comparing the rendered list outright, which passed standalone and failed four
ways under `./mutint test` the first time it ran, because mutint-needle's panel is in it. The
rule is the same one the About-page and nav tests learned: a shared registry's contents are a
fact about the deployment, and the only thing core can assert is what core put there.
"""

import shutil
import tempfile

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common.panel_registry import (
    get_overview_panels,
    register_overview_panel,
    render_overview_panels,
    unregister_overview_panel,
)
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample


class PanelRegistryTestCase(TestCase):

    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")

    def _register(self, name, **kwargs):
        register_overview_panel(self.app_config, name=name, **kwargs)
        self.addCleanup(unregister_overview_panel, self.app_config.name, name)

    def test_registering_puts_the_panel_in_the_list(self):
        self._register("t1", title="T", template="about/index.html")
        self.assertIn(("mutint_common", "t1"),
                      [(p["app"], p["name"]) for p in get_overview_panels()])

    def test_registering_the_same_name_twice_replaces_rather_than_doubles(self):
        """A panel appearing twice on the page is the shape a reload bug takes, and it is
        much harder to notice than one appearing not at all."""
        self._register("t2", title="First", template="about/index.html")
        self._register("t2", title="Second", template="about/index.html")

        mine = [p for p in get_overview_panels() if p["name"] == "t2"]
        self.assertEqual(1, len(mine))
        self.assertEqual("Second", mine[0]["title"])


class RenderedPanelTestCase(TestCase):
    """Rendering, which is where a panel can fail somebody else's page."""

    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.user = User.objects.create(username="panel", email="p@e.com", is_active=True)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="panel")
        self.experiment = Sample.objects.get().experiment
        self.experiment.project.user = self.user
        self.experiment.project.save()

    def _register(self, name, **kwargs):
        register_overview_panel(self.app_config, name=name, **kwargs)
        self.addCleanup(unregister_overview_panel, self.app_config.name, name)

    def _mine(self, panels, *names):
        """Only the panels this test registered, in order.

        Whatever else is installed registers panels too -- mutint-needle is one -- and they
        are in the same list. Asserting on the list itself is asserting on the install set.
        """
        return [p for p in panels if p["name"] in names]

    def _request(self, path="/stats?contig=x"):
        from django.test import RequestFactory

        request = RequestFactory().get(path)
        request.user = self.user
        return request

    def test_the_context_callable_gets_the_experiment_and_the_request(self):
        """Both, because a panel may legitimately depend on the query string -- the needle
        plot's sequence picker is a `?contig=` away."""
        seen = {}

        def context(experiment, request):
            seen["experiment"] = experiment
            seen["contig"] = request.GET.get("contig")
            return {"panel_body": "rendered %s" % experiment.name}

        self._register("body", title="T", template="tests/panel.html", context=context)
        mine = self._mine(render_overview_panels(self.experiment, self._request()), "body")

        self.assertEqual(self.experiment, seen["experiment"])
        self.assertEqual("x", seen["contig"])
        self.assertEqual(1, len(mine))
        self.assertIn("rendered e", mine[0]["html"])

    def test_a_panel_sees_the_context_processors(self):
        """`request=` is passed to the renderer, so a panel gets the same `mutint_version`,
        user and static configuration as the page around it. Without it a panel could not
        version its own assets and would not know who is looking."""
        self._register("version", title="T", template="tests/panel_version.html")

        mine = self._mine(render_overview_panels(self.experiment, self._request()), "version")

        from mutint_common.version import __version__
        self.assertIn(__version__, mine[0]["html"])

    def test_each_panel_renders_with_only_its_own_context(self):
        """Two panels using one name for different things would otherwise read each other's,
        which is the failure that makes a shared context dict unusable here."""
        self._register("one", title="One", template="tests/panel.html",
                       context=lambda e, r: {"panel_body": "first"})
        self._register("two", title="Two", template="tests/panel.html",
                       context=lambda e, r: {"panel_body": "second"})

        mine = self._mine(render_overview_panels(self.experiment, self._request()),
                          "one", "two")

        self.assertEqual(["first", "second"], [p["html"].strip() for p in mine])

    def test_a_panel_that_raises_is_dropped_and_the_others_render(self):
        """One component's typo must not take down a page that is mostly somebody else's
        content -- the posture nav_registry takes with a url_name that will not reverse."""
        def boom(experiment, request):
            raise ValueError("no")

        self._register("bad", title="Bad", template="tests/panel.html", context=boom)
        self._register("good", title="Good", template="tests/panel.html",
                       context=lambda e, r: {"panel_body": "fine"})

        with self.assertLogs("mutint_common.panel_registry", level="ERROR"):
            panels = render_overview_panels(self.experiment, self._request())

        self.assertEqual(["good"], [p["name"] for p in self._mine(panels, "bad", "good")])

    def test_a_missing_template_is_dropped_rather_than_500ing(self):
        self._register("gone", title="Gone", template="no/such/template.html")

        with self.assertLogs("mutint_common.panel_registry", level="ERROR"):
            panels = render_overview_panels(self.experiment, self._request())

        self.assertEqual([], self._mine(panels, "gone"))

    def test_the_overview_page_renders_a_registered_panel(self):
        """End to end, because the registry answering correctly and the page drawing the
        answer are two different things -- and the page is the half in another app."""
        self._register("onpage", title="A Panel Title", template="tests/panel.html",
                       context=lambda e, r: {"panel_body": "panel body here"})
        self.client.force_login(self.user)

        body = self.client.get(
            "/stats?experiment_id=%s" % self.experiment.id,
            follow=True).content.decode()

        self.assertIn("A Panel Title", body)
        self.assertIn("panel body here", body)
