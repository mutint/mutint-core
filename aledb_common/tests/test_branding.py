"""Deployment branding, and the attribution that is not branding.

Two separate things share this file because the point is that they are separate:

- What the *deployment* calls itself -- the name and version at the top of the
  sidebar, the logo upper-right, and the landing page. All configurable, all
  absent by default, because aledb-core is not ALEdb.
- What *aledb-core* is -- the "Powered by ALEdb" watermark, which every
  deployment carries and none can configure away.
"""

import os
import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common.version import __version__

BRANDED = {"name": "ALEdb", "version": "9.9.9", "logo": "img/aledb_header.png"}

# An internal page, so base.html renders; `/` is the landing page and is its own case.
INTERNAL_PAGE = "/ale/projects/"


def _templates_with(extra_dir):
    """The configured TEMPLATES, with `extra_dir` searched first."""
    from django.conf import settings
    templates = [dict(engine) for engine in settings.TEMPLATES]
    templates[0]["DIRS"] = [extra_dir] + list(templates[0]["DIRS"])
    return templates


class BrandingTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())
        self.client.force_login(self.user)

    # --- unbranded is the default --------------------------------------------

    def test_no_deployment_name_by_default(self):
        content = self.client.get(INTERNAL_PAGE).content.decode()
        self.assertNotIn("navbar-brand", content)
        self.assertNotIn("ALEdb 1.1.0", content)

    def test_no_deployment_logo_by_default(self):
        self.assertNotIn("aledb_header.png", self.client.get(INTERNAL_PAGE).content.decode())

    def test_error_pages_carry_no_deployment_name(self):
        """The titles were hardcoded `ALEDB - 404`; unbranded they are just the code."""
        response = self.client.get("/no/such/page/here")
        self.assertContains(response, "<title> 404</title>", status_code=404, html=False)

    # --- a deployment can supply its own -------------------------------------

    @override_settings(ALEDB_BRANDING=BRANDED)
    def test_a_deployment_name_and_version_render(self):
        content = self.client.get(INTERNAL_PAGE).content.decode()
        self.assertIn("navbar-brand", content)
        self.assertIn("ALEdb", content)
        self.assertIn("9.9.9", content)

    @override_settings(ALEDB_BRANDING=BRANDED)
    def test_a_deployment_logo_renders(self):
        """The logo path is a setting, resolved through {% static %} at render time."""
        self.assertIn("aledb_header.png", self.client.get(INTERNAL_PAGE).content.decode())

    @override_settings(ALEDB_BRANDING={"name": "Bare"})
    def test_a_name_without_a_logo_renders_no_image(self):
        content = self.client.get(INTERNAL_PAGE).content.decode()
        self.assertIn("Bare", content)
        self.assertNotIn("aledb_header.png", content)

    @override_settings(ALEDB_BRANDING=BRANDED)
    def test_error_pages_pick_up_the_deployment_name(self):
        self.assertContains(self.client.get("/no/such/page/here"),
                            "<title>ALEdb 404</title>", status_code=404, html=False)

    # --- the watermark is not configurable -----------------------------------

    def test_the_watermark_is_present_unbranded(self):
        content = self.client.get(INTERNAL_PAGE).content.decode()
        self.assertIn("Powered by ALEdb v%s" % __version__, content)
        self.assertIn("aledb-watermark", content)

    @override_settings(ALEDB_BRANDING=BRANDED)
    def test_the_watermark_survives_being_branded_over(self):
        """A deployment names itself; it does not get to drop the attribution."""
        self.assertIn("Powered by ALEdb v%s" % __version__,
                      self.client.get(INTERNAL_PAGE).content.decode())

    def test_the_watermark_reports_the_real_version(self):
        """Asserted against version.py, so a bump cannot rot this test."""
        self.assertIn("Powered by ALEdb v%s" % __version__,
                      self.client.get(INTERNAL_PAGE).content.decode())
        self.assertNotIn("Powered by ALEdb v{{", self.client.get(INTERNAL_PAGE).content.decode())


class LandingPageTestCase(TestCase):
    """`/` is the project list until a deployment supplies home/splash.html."""

    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())
        self.client.force_login(self.user)

    def test_it_falls_back_to_the_project_view(self):
        response = self.client.get("/")
        self.assertContains(response, "project_table")
        self.assertContains(response, "Project List")

    def test_the_fallback_stays_on_the_root_url(self):
        """Rendered in place, not redirected -- `/` is still `/`."""
        self.assertEqual(200, self.client.get("/").status_code)

    def test_a_supplied_splash_takes_over(self):
        splash_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, splash_dir, True)
        os.makedirs(os.path.join(splash_dir, "home"))
        with open(os.path.join(splash_dir, "home", "splash.html"), "w") as handle:
            handle.write("<h1>A deployment's own landing page</h1>")

        with override_settings(TEMPLATES=_templates_with(splash_dir)):
            response = self.client.get("/")

        self.assertContains(response, "A deployment's own landing page")
        self.assertNotContains(response, "project_table")


class InstitutionalFooterTestCase(TestCase):
    """Who hosts the deployment is the deployment's business.

    The SBRG / CFB / UCSD footer used to be pasted into three core pages. It is
    now one overridable include that aledb-core ships empty.
    """

    PAGES = ("/about", "/dashboard")

    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())
        self.client.force_login(self.user)

    def test_core_credits_no_institution(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                content = self.client.get(page).content.decode()
                for marker in ("sbrg-logo", "ucsd-logo", "cfb-logo",
                               "hosted and maintained", "Feist"):
                    self.assertNotIn(marker, content)

    def test_a_deployment_footer_is_picked_up(self):
        footer_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, footer_dir, True)
        os.makedirs(os.path.join(footer_dir, "branding"))
        with open(os.path.join(footer_dir, "branding", "footer.html"), "w") as handle:
            handle.write("<p>Hosted by Somebody Else</p>")

        with override_settings(TEMPLATES=_templates_with(footer_dir)):
            for page in self.PAGES:
                with self.subTest(page=page):
                    self.assertContains(self.client.get(page), "Hosted by Somebody Else")

    def test_the_about_page_describes_the_platform(self):
        """It was ALEdb's about page -- publication, PI, contact. Now it is core's."""
        content = self.client.get("/about").content.decode()
        self.assertIn("aledb-core", content)
        self.assertIn(__version__, content)
        self.assertNotIn("afeist@ucsd.edu", content)
