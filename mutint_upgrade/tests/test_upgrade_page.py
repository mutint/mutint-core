"""`/upgrade/`: who may see it, what it shows, and what the two endpoints refuse.

The page itself is mostly an inventory somebody else already tested -- `get_about_sections`
has its own suite -- so what is worth pinning here is the gating and the refusals. Both
endpoints move or stage code that every reader of the deployment is then served by, which is
why each re-checks the permission the page checked rather than trusting the page.

Nothing here reaches a git remote: `mutint_common/upgrade.py`'s own tests cover that layer
against real temporary repositories.

**Both classes pin `MUTINT_UPGRADE_ENABLED` on**, and that is not boilerplate. ALEdb sets it
False, so without the override every test of the enabled behaviour passes in mutint-core and
fails in a deployment that declined the feature -- which is the same trap core's own tests
already have a rule about for shared registries: an assertion that depends on the install set
rather than on the code.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from mutint_common import upgrade


@override_settings(MUTINT_UPGRADE_ENABLED=True)
class UpgradePageTestCase(TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = mock.patch("mutint_upgrade.views.upgrade_root", return_value=self.base)
        self.root.start()
        self.addCleanup(self.root.stop)

        self.superuser = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r2@example.com", "pw")

    def test_an_anonymous_visitor_gets_403(self):
        self.assertEqual(403, self.client.get(reverse("upgrade")).status_code)

    def test_an_ordinary_user_gets_403(self):
        """Not merely hidden from the sidebar. The account block's `{% if %}` is what a
        reader sees; this is what stops them typing the URL."""
        self.client.force_login(self.ordinary)

        self.assertEqual(403, self.client.get(reverse("upgrade")).status_code)

    def test_a_superuser_sees_the_component_table(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("upgrade"))

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "mutint-core")

    def test_the_table_says_when_each_component_was_committed(self):
        """A hash says which commit and nothing about how old it is, which is the question
        this page exists to answer -- especially on the Development channel, where `main`
        moves several times a day."""
        self.client.force_login(self.superuser)
        revision = {"short": "abcdef0", "full": "a" * 40,
                    "committed": "2026-09-07T11:53:23-04:00",
                    "url": None, "repository": None}

        with mock.patch("mutint_common.util.get_revision", return_value=revision):
            response = self.client.get(reverse("upgrade"))

        self.assertContains(response, "<th>Committed</th>", html=False)
        # Formatted by the same helper that describes the available version, so the two
        # timestamps on this page cannot be written differently.
        self.assertContains(response, "2026-09-07 11:53 -04:00")

    def test_a_component_with_no_git_history_shows_no_date(self):
        """A deployment shipped without its `.git` -- production images usually are -- has
        no revision and therefore no date, and says so rather than showing an empty cell that
        reads as a bug."""
        self.client.force_login(self.superuser)

        with mock.patch("mutint_common.util.get_revision", return_value=None):
            response = self.client.get(reverse("upgrade"))

        self.assertContains(response, "no git history")

    def test_a_components_name_links_to_its_repository(self):
        """Read off its checkout's remote, whichever owner that names."""
        self.client.force_login(self.superuser)
        revision = {"short": "abcdef0", "full": "a" * 40,
                    "url": "https://github.com/someone-else/mutint-core/commit/" + "a" * 40,
                    "repository": "https://github.com/someone-else/mutint-core"}

        with mock.patch("mutint_common.util.get_revision", return_value=revision):
            response = self.client.get(reverse("upgrade"))

        self.assertContains(response, 'href="https://github.com/someone-else/mutint-core"')
        self.assertContains(response, ">mutint-core</a>")

    def test_it_renders_without_ever_having_been_checked(self):
        """A fresh installation has no state file, and must not need one seeded."""
        self.client.force_login(self.superuser)
        with mock.patch.object(upgrade, "blockers", return_value=[]):
            response = self.client.get(reverse("upgrade"))

        self.assertContains(response, "Not checked yet")

    def test_a_checkout_that_cannot_be_upgraded_says_so_on_the_page(self):
        """A temporary directory is not a git checkout, which is the same state a copied
        installation is in -- and the reason belongs on the page rather than only in the
        response to a button nobody has pressed yet."""
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("upgrade"))

        self.assertContains(response, "not a git checkout")

    def test_a_staged_upgrade_says_to_relaunch(self):
        """The one instruction the whole feature depends on somebody following."""
        upgrade.request(self.base, "v1.2.3", by="root")
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("upgrade"))

        self.assertContains(response, "v1.2.3 is staged")
        self.assertContains(response, "start it again")

    def test_a_failed_apply_is_reported_on_the_page(self):
        """`apply_staged` swallows the failure so the launch survives, which means this page
        is the only place it is ever seen."""
        upgrade.write_state(self.base, {
            "channel": upgrade.STABLE,
            "last_result": {"ref": "v9.9.9", "ok": False, "detail": "no origin remote"},
        })
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("upgrade"))

        self.assertContains(response, "did not run")
        self.assertContains(response, "no origin remote")

    @override_settings(MUTINT_UPGRADE_ENABLED=False)
    def test_a_deployment_can_turn_it_off(self):
        """ALEdb does: private repository, upgraded by hand. The page still renders -- the
        component inventory is worth having either way -- and offers nothing."""
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("upgrade"))

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "not enabled on this deployment")
        self.assertNotContains(response, "Check for updates")


@override_settings(MUTINT_UPGRADE_ENABLED=True)
class EndpointTestCase(TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        patcher = mock.patch("mutint_upgrade.views.upgrade_root", return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.superuser = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r2@example.com", "pw")

    def _post(self, name, payload):
        return self.client.post(reverse(name), data=json.dumps(payload),
                                content_type="application/json")

    def test_check_refuses_an_ordinary_user(self):
        """Re-checked rather than trusted: the gate on a write endpoint is not the gate on
        the page that offered it."""
        self.client.force_login(self.ordinary)

        self.assertEqual(403, self._post("upgrade_check", {}).status_code)

    def test_install_refuses_an_ordinary_user(self):
        self.client.force_login(self.ordinary)

        self.assertEqual(403, self._post("upgrade_install", {"ref": "v1"}).status_code)

    def test_check_refuses_an_unknown_channel(self):
        self.client.force_login(self.superuser)

        self.assertEqual(400, self._post("upgrade_check", {"channel": "nightly"}).status_code)

    def test_check_remembers_the_channel(self):
        self.client.force_login(self.superuser)

        self._post("upgrade_check", {"channel": upgrade.MAIN})

        self.assertEqual(upgrade.MAIN, upgrade.read_state(self.base)["channel"])

    def test_check_reports_why_it_could_not_look(self):
        """A temporary directory is not a checkout, so this exercises the path a page in a
        broken installation takes: a sentence, not a 500."""
        self.client.force_login(self.superuser)

        response = self._post("upgrade_check", {})

        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.json()["available"])
        self.assertIn("adopt", response.json()["error"])

    def test_install_refuses_a_ref_nothing_offered(self):
        """Otherwise this endpoint takes an arbitrary string from a form field and hands it
        to `git checkout` on the next launch, which is a much larger promise than the page
        is making."""
        upgrade.write_state(self.base, {"channel": upgrade.STABLE,
                                        "available": {"ref": "v1.2.3"}})
        self.client.force_login(self.superuser)

        response = self._post("upgrade_install", {"ref": "v9.9.9"})

        self.assertEqual(409, response.status_code)
        self.assertNotIn("requested", upgrade.read_state(self.base))

    def test_install_refuses_nothing(self):
        self.client.force_login(self.superuser)

        self.assertEqual(400, self._post("upgrade_install", {"ref": " "}).status_code)

    def test_install_refuses_when_the_checkout_would_block_it(self):
        """The same refusals `./mutint upgrade` makes, asked before staging rather than at
        the next launch -- a request that could only fail is not worth taking."""
        upgrade.write_state(self.base, {"channel": upgrade.STABLE,
                                        "available": {"ref": "v1.2.3"}})
        self.client.force_login(self.superuser)

        response = self._post("upgrade_install", {"ref": "v1.2.3"})

        self.assertEqual(409, response.status_code)
        self.assertNotIn("requested", upgrade.read_state(self.base))

    def test_install_stages_and_records_who_asked(self):
        upgrade.write_state(self.base, {"channel": upgrade.STABLE,
                                        "available": {"ref": "v1.2.3"}})
        self.client.force_login(self.superuser)
        with mock.patch.object(upgrade, "blockers", return_value=[]):
            response = self._post("upgrade_install", {"ref": "v1.2.3"})

        self.assertEqual(200, response.status_code)
        staged = upgrade.read_state(self.base)["requested"]
        self.assertEqual("v1.2.3", staged["ref"])
        self.assertEqual("root", staged["by"])

    def test_a_non_json_body_is_a_400_not_a_500(self):
        self.client.force_login(self.superuser)

        response = self.client.post(reverse("upgrade_check"), data="not json",
                                    content_type="application/json")

        self.assertEqual(400, response.status_code)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.superuser)

        self.assertEqual(405, self.client.get(reverse("upgrade_check")).status_code)


class AccountBlockTestCase(TestCase):
    """The link lives in base.html, so it is gated by the template rather than by a registry."""

    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r2@example.com", "pw")

    def test_a_superuser_is_offered_the_link(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("jobs"))

        self.assertContains(response, reverse("upgrade"))

    def test_an_ordinary_user_is_not(self):
        """nav_registry has no per-user visibility concept, which is the whole reason this
        entry is written into the template; an entry that renders for everyone and 403s is
        the dead end this codebase refuses."""
        self.client.force_login(self.ordinary)

        response = self.client.get(reverse("jobs"))

        self.assertNotContains(response, reverse("upgrade"))
