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
INTERNAL_PAGE = "/project/"


def _templates_with(extra_dir):
    """The configured TEMPLATES, with `extra_dir` searched first."""
    from django.conf import settings
    templates = [dict(engine) for engine in settings.TEMPLATES]
    templates[0]["DIRS"] = [extra_dir] + list(templates[0]["DIRS"])
    return templates


def _core_templates_only():
    """TEMPLATES with the project's own directory dropped, leaving what core ships.

    The override seam is `DIRS`: an assembled project's `templates/` is searched ahead of
    every app's, which is how aledb-deploy replaces the About page and supplies a splash and
    an institutional footer. Tests about core's *defaults* have to empty it, or they assert
    the deployment's choices -- which is exactly what four of them did the moment a bare
    `./aledb-deploy test` began running them.
    """
    from django.conf import settings
    templates = [dict(engine) for engine in settings.TEMPLATES]
    templates[0]["DIRS"] = []
    return templates


class BrandingTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())
        self.client.force_login(self.user)

    # --- unbranded is the default --------------------------------------------
    #
    # Each of these pins ALEDB_BRANDING to the shipped default rather than reading whatever
    # is configured. They are about what *core* does with no branding, and an assembled
    # project legitimately sets its own -- so without the override they assert the deployment's
    # configuration and fail in MutInt and aledb-deploy, which is exactly what they did once a
    # bare `./mutint test` started running them.

    @override_settings(ALEDB_BRANDING={})
    def test_no_deployment_name_by_default(self):
        content = self.client.get(INTERNAL_PAGE).content.decode()
        self.assertNotIn("ALEdb 1.1.0", content)

    @override_settings(ALEDB_BRANDING={})
    def test_the_brand_slot_still_links_to_the_dashboard(self):
        """`navbar-brand` renders unbranded, and this asserts it deliberately.

        It used to be inside the `{% if branding %}`, and this test used to say
        `assertNotIn("navbar-brand", ...)`. What changed is that the brand is now the only
        route to `/dashboard` -- the app registers no nav entry, because an inventory of the
        whole installation is what clicking the site's own name asks for and two entries
        three rows apart said the same thing twice.

        Unbranded, the element renders with the word **Dashboard** in it. That is not a name
        this deployment has acquired: it is the label of a link, and the four things
        `CLAUDE.md` says a deployment adds -- a name, a version, a logo, an institution --
        are each still absent. What is not acceptable is the third option, where an unbranded
        checkout renders no brand and the dashboard is reachable from nowhere.
        """
        content = self.client.get(INTERNAL_PAGE).content.decode()

        self.assertIn("navbar-brand", content)
        self.assertIn("/dashboard", content)
        self.assertIn("Dashboard", content)

    @override_settings(ALEDB_BRANDING=BRANDED)
    def test_a_branded_deployment_puts_its_own_name_in_that_link(self):
        """The fallback is a fallback: branded, the word Dashboard does not appear."""
        content = self.client.get(INTERNAL_PAGE).content.decode()
        brand = content[content.index("navbar-brand"):]
        brand = brand[:brand.index("</a>")]

        self.assertIn("/dashboard", content)
        self.assertIn("ALEdb", brand)
        self.assertNotIn("Dashboard", brand)

    @override_settings(ALEDB_BRANDING={})
    def test_no_deployment_logo_by_default(self):
        self.assertNotIn("aledb_header.png", self.client.get(INTERNAL_PAGE).content.decode())

    @override_settings(ALEDB_BRANDING={})
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
        self.assertIn("Powered by ALEdb", content)
        self.assertIn("aledb-watermark", content)

    @override_settings(ALEDB_BRANDING=BRANDED)
    def test_the_watermark_survives_being_branded_over(self):
        """A deployment names itself; it does not get to drop the attribution."""
        self.assertIn("Powered by ALEdb",
                      self.client.get(INTERNAL_PAGE).content.decode())

    def test_the_watermark_carries_no_version(self):
        """It is an attribution, not a status line.

        This used to assert the opposite -- that the mark read `Powered by ALEdb v<version>`,
        against `version.py` so a bump could not rot it. The version is gone from the foot of
        every page deliberately, so the test is inverted rather than deleted: putting it back
        should have to be a decision, not a tidy-up. `./aledb version` and `/about`, which
        lists every installed component's version and revision, are where it lives now.
        """
        content = self.client.get(INTERNAL_PAGE).content.decode()
        self.assertIn("Powered by ALEdb", content)
        self.assertNotIn("Powered by ALEdb v", content)


class LandingPageTestCase(TestCase):
    """`/` is the project list until a deployment supplies home/splash.html."""

    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())
        self.client.force_login(self.user)

    def test_it_falls_back_to_the_project_view(self):
        with override_settings(TEMPLATES=_core_templates_only()):
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
        with override_settings(TEMPLATES=_core_templates_only()):
            self._assert_no_institution()

    def _assert_no_institution(self):
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

    @override_settings(TEMPLATES=_core_templates_only())
    def test_the_about_page_describes_the_platform(self):
        """It was ALEdb's about page -- publication, PI, contact. Now it is core's."""
        content = self.client.get("/about").content.decode()
        self.assertIn("aledb-core", content)
        self.assertIn(__version__, content)
        self.assertNotIn("afeist@ucsd.edu", content)
