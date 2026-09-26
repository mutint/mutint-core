"""The seam that lets a component link to something it keeps about one sample.

Every test registers its own provider and asserts only on its own links, for the reason the
panel tests give: what else is in a shared registry is a fact about the deployment.
"""

import shutil
import tempfile

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common.sample_link_registry import (
    register_sample_link,
    sample_links,
    unregister_sample_link,
)
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample


class SampleLinkTestCase(TestCase):

    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")

    def _register(self, name, links):
        register_sample_link(self.app_config, name, links)
        self.addCleanup(unregister_sample_link, self.app_config.name, name)

    def test_a_provider_returns_links_for_the_sample_it_is_asked_about(self):
        self._register("t_links", lambda sample, request: [
            ("Report %s" % sample, "/r/%s" % sample, "tip")])

        mine = [link for link in sample_links("s1") if link["url"] == "/r/s1"]

        self.assertEqual([{"label": "Report s1", "url": "/r/s1", "title": "tip"}], mine)

    def test_a_provider_that_raises_is_dropped_and_the_rest_still_render(self):
        def broken(sample, request):
            raise RuntimeError("boom")
        self._register("t_broken", broken)
        self._register("t_fine", lambda sample, request: [("Fine", "/fine", "")])

        with self.assertLogs("mutint_common.sample_link_registry", level="ERROR"):
            links = sample_links("s1")

        self.assertIn("/fine", [link["url"] for link in links])


class RenderedLinkTestCase(TestCase):
    """The links reach the box at the top of the sample's Mutations page."""

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, ignore_errors=True)
        override = override_settings(MUTINT_STORE_DIR=self.store)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create(username="links", email="l@e.com", is_active=True)
        self.client.force_login(self.user)
        drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, drop, True)
        breseq_fixture.write_sample(drop, "s1")
        breseq_folder.import_breseq_folders(
            drop, project_name="P", experiment_name="e", owner_name="links")
        self.sample = Sample.objects.get()
        self.experiment = self.sample.experiment
        self.experiment.project.user = self.user
        self.experiment.project.save()

        register_sample_link(apps.get_app_config("mutint_common"), "t_page",
                             lambda sample, request: [("QC", "/qc/%d" % sample.pk, "tip")])
        self.addCleanup(unregister_sample_link, "mutint_common", "t_page")

    def test_the_mutations_page_draws_the_link(self):
        response = self.client.get("/mutations/breseq", {
            "experiment_id": self.experiment.pk, "sample_id": self.sample.pk})

        self.assertContains(response, 'href="/qc/%d"' % self.sample.pk)
        self.assertContains(response, "QC &raquo;")
