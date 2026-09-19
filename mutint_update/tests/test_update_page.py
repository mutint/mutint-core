"""`/update/`: who may see it, what it shows, and what the two endpoints refuse.

The page itself is mostly an inventory somebody else already tested -- `get_about_sections`
has its own suite -- so what is worth pinning here is the gating and the refusals. Both
endpoints move or stage code that every reader of the deployment is then served by, which is
why each re-checks the permission the page checked rather than trusting the page.

Nothing here reaches a git remote: `mutint_common/update.py`'s own tests cover that layer
against real temporary repositories.

**Both classes pin `MUTINT_UPDATE_ENABLED` on**, and that is not boilerplate. ALEdb sets it
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

from mutint_common import update


@override_settings(MUTINT_UPDATE_ENABLED=True)
class UpdatePageTestCase(TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = mock.patch("mutint_update.views.update_root", return_value=self.base)
        self.root.start()
        self.addCleanup(self.root.stop)

        self.superuser = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r2@example.com", "pw")

    def _write_available(self, **extra):
        """A checked state with something available, as `update.check` would have left it.

        `blockers` is silenced because this temporary directory is not a git checkout, and the
        page reports that ahead of anything a check found -- correctly, since a tree it cannot
        fetch into is the more useful thing to say.
        """
        patch = mock.patch.object(update, "blockers", return_value=[])
        patch.start()
        self.addCleanup(patch.stop)
        state = update.read_state(self.base)
        available = {
            "ref": "main", "kind": update.BRANCH, "sha": "def67890",
            "summary": "A new version of MutInt is available (commit def67890) "
                       "committed on 2026-09-07 11:53.",
            "project": {"name": "mutint", "from": "abc12345", "to": "def67890",
                        "change": "moved", "commits": 2},
            "components": [{"name": "mutint-core", "from": "1111aaaa", "to": "2222bbbb",
                            "change": "moved"}],
        }
        available.update(extra)
        state["available"] = available
        state["checked_at"] = "2026-09-07 11:53"
        update.write_state(self.base, state)

    def test_the_project_heads_the_list_of_what_would_move(self):
        """It is the repository the update is *of*; every other bullet is one of its
        submodules. Order is the assertion -- a reader looking for the new revision of MutInt
        itself should not have to find it among the components."""
        self.client.force_login(self.superuser)
        self._write_available()

        body = self.client.get(reverse("update")).content.decode()

        self.assertLess(body.index("mutint</code>"), body.index("mutint-core</code>"))
        self.assertIn("abc12345", body)
        self.assertIn("2 commits", body)

    def test_the_sentence_is_the_one_the_check_composed(self):
        """Composed server-side so this and the poll cannot word it differently."""
        self.client.force_login(self.superuser)
        self._write_available()

        self.assertContains(
            self.client.get(reverse("update")),
            "A new version of MutInt is available (commit def67890)")

    def test_a_version_with_no_project_move_still_lists_its_components(self):
        """`_project_change` answers None when the revisions match, and the list must survive
        it rather than disappearing."""
        self.client.force_login(self.superuser)
        self._write_available(project=None)

        self.assertContains(self.client.get(reverse("update")), "mutint-core")

    def test_the_page_and_the_command_name_the_deployment_the_same_way(self):
        """`summarize` composes one sentence so the page and the command cannot word it
        differently; a name each of them read for itself would put the difference back one
        level down."""
        from mutint_common.context_processors import deployment_name

        with override_settings(MUTINT_BRANDING={"name": "MutInt"}):
            self.assertEqual("MutInt", deployment_name())
        with override_settings(MUTINT_BRANDING={}):
            self.assertIsNone(deployment_name())

    def test_an_anonymous_visitor_gets_403(self):
        self.assertEqual(403, self.client.get(reverse("update")).status_code)

    def test_an_ordinary_user_gets_403(self):
        """Not merely hidden from the sidebar. The account block's `{% if %}` is what a
        reader sees; this is what stops them typing the URL."""
        self.client.force_login(self.ordinary)

        self.assertEqual(403, self.client.get(reverse("update")).status_code)

    def test_a_superuser_sees_the_component_table(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

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
            response = self.client.get(reverse("update"))

        self.assertContains(response, "<th>Committed</th>", html=False)
        # Formatted by the same helper that describes the available version, so the two
        # timestamps on this page cannot be written differently.
        self.assertContains(response,
                            update.readable_time("2026-09-07T11:53:23-04:00"))

    def test_a_component_with_no_git_history_shows_no_date(self):
        """A deployment shipped without its `.git` -- production images usually are -- has
        no revision and therefore no date, and says so rather than showing an empty cell that
        reads as a bug."""
        self.client.force_login(self.superuser)

        with mock.patch("mutint_common.util.get_revision", return_value=None):
            response = self.client.get(reverse("update"))

        self.assertContains(response, "no git history")

    def test_a_components_name_links_to_its_repository(self):
        """Read off its checkout's remote, whichever owner that names."""
        self.client.force_login(self.superuser)
        revision = {"short": "abcdef0", "full": "a" * 40,
                    "url": "https://github.com/someone-else/mutint-core/commit/" + "a" * 40,
                    "repository": "https://github.com/someone-else/mutint-core"}

        with mock.patch("mutint_common.util.get_revision", return_value=revision):
            response = self.client.get(reverse("update"))

        self.assertContains(response, 'href="https://github.com/someone-else/mutint-core"')
        self.assertContains(response, ">mutint-core</a>")

    def test_it_renders_without_ever_having_been_checked(self):
        """A fresh installation has no state file, and must not need one seeded."""
        self.client.force_login(self.superuser)
        with mock.patch.object(update, "blockers", return_value=[]):
            response = self.client.get(reverse("update"))

        self.assertContains(response, "Not checked yet")

    def test_a_checkout_that_cannot_be_updated_says_so_on_the_page(self):
        """A temporary directory is not a git checkout, which is the same state a copied
        installation is in -- and the reason belongs on the page rather than only in the
        response to a button nobody has pressed yet."""
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

        self.assertContains(response, "not a git checkout")

    def test_a_staged_update_says_to_relaunch(self):
        """The one instruction the whole feature depends on somebody following."""
        update.request(self.base, "v1.2.3", by="root")
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

        self.assertContains(response, "An update is staged.")
        self.assertContains(response, "start it again")

    def test_the_staged_sentence_is_the_checks_own_and_names_no_branch(self):
        """`main is staged` is a statement about a branch. What is staged is described the
        way the check described it, and under the channel menu rather than above it."""
        self._write_available(date="2026-09-07T11:53:23-04:00")
        update.request(self.base, "main", by="root")
        self.client.force_login(self.superuser)

        body = self.client.get(reverse("update")).content.decode()

        self.assertIn("(commit def67890) committed on %s is staged."
                      % update.readable_time("2026-09-07T11:53:23-04:00"), body)
        self.assertNotIn("main is staged", body)
        self.assertNotIn("A new version of MutInt is available", body)
        self.assertLess(body.index('id="update-channel"'), body.index("is staged."))
        # What would move is still listed under it.
        self.assertIn("mutint-core</code>", body)

    def test_the_project_heads_the_component_table(self):
        """An assembled project has no Django app, so the inventory cannot see it -- and it
        is the repository an update is *of*. Named as the list of what would move names it."""
        self.client.force_login(self.superuser)

        with mock.patch.object(update, "repository_name", return_value="the-project"), \
                mock.patch("mutint_update.views.aggregator",
                           return_value=("The Project", None, "9.8.7")):
            body = self.client.get(reverse("update")).content.decode()

        table = body[body.index('id="update-components"'):body.index("</table>")]
        self.assertLess(table.index("the-project"), table.index("mutint-core"))
        self.assertIn("9.8.7", table)

    def test_a_project_that_is_a_component_is_not_listed_twice(self):
        """Standalone mutint-core is its own project and already has its row."""
        from mutint_common.about_registry import component_dir
        from django.apps import apps

        core = component_dir(apps.get_app_config("mutint_common"))
        self.client.force_login(self.superuser)

        with mock.patch("mutint_update.views.update_root", return_value=core), \
                mock.patch.object(update, "repository_name", return_value="the-project"):
            response = self.client.get(reverse("update"))

        self.assertNotContains(response, "the-project")

    def test_the_menu_has_no_heading_and_the_page_no_installed_line(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

        self.assertNotContains(response, "Follow</label>")
        self.assertNotContains(response, "Installed:")
        self.assertContains(response, 'aria-label="Update channel"')

    def test_where_mutint_can_be_restarted_installing_is_one_button(self):
        """Beside Check for updates, in a colour of its own, and instead of Install."""
        self._write_available()
        self.client.force_login(self.superuser)

        with mock.patch("mutint_update.views.restart.relaunch_command", return_value="x"):
            body = self.client.get(reverse("update")).content.decode()

        self.assertIn("Restart to install updates", body)
        self.assertIn('class="btn btn-success" id="update-restart"', body)
        self.assertNotIn('id="update-install"', body)
        self.assertLess(body.index('id="update-check"'), body.index('id="update-restart"'))

    def test_from_a_terminal_install_is_offered_and_restart_is_not(self):
        """Nothing can start MutInt again there, so a Restart button would only stop it."""
        self._write_available()
        self.client.force_login(self.superuser)

        with mock.patch("mutint_update.views.restart.relaunch_command", return_value=None):
            body = self.client.get(reverse("update")).content.decode()

        self.assertIn('id="update-install"', body)
        self.assertNotIn('id="update-restart"', body)

    def test_an_applied_update_is_not_still_offered(self):
        """The green banner says what happened; nothing beside it should still be selling the
        version it happened to.

        This pins the page's half -- given no `available`, it offers nothing, whatever
        `last_result` says -- and not the voiding that produces that state, which is
        `test_update.VoidVerdictTestCase`'s. The two were one bug: the state kept the verdict
        and the page rendered its Install button from it, so after an update this page went on
        describing v1.1.0 and offering to install it, past every reload."""
        update.write_state(self.base, {
            "channel": update.STABLE,
            "last_result": {"ref": "v1.1.0", "ok": True, "now": "v1.1.0"},
        })
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

        self.assertContains(response, "Updated to")
        html = response.content.decode("utf-8")
        # The button is rendered either way and hidden by style, and its label is the same
        # either way, so the hiding style and the status sentence are what say whether the
        # page is offering anything. What fills the status box instead is not asserted here
        # -- this fixture's base is not a git checkout, so it is the blocker warning rather
        # than "Not checked yet".
        button = html[html.index('id="update-install"'):]
        self.assertLess(button.index('display: none'), button.index("Install update"))
        self.assertNotIn("v1.1.0 is available", html)

    def test_the_result_banners_never_name_a_branch(self):
        """`main` says which channel was followed and nothing about what is installed."""
        self.client.force_login(self.superuser)
        for result, expected in (
                ({"ref": "main", "ok": True, "now": "def67890"}, "Updated to <strong>commit def67890"),
                ({"ref": "v0.0.2", "ok": True, "now": "v0.0.2"}, "Updated to <strong>version v0.0.2"),
                ({"ref": "main", "ok": True, "now": None}, "Updated."),
                ({"ref": "main", "ok": False, "detail": "x"}, "The update did not run."),
                ({"ref": "v0.0.2", "ok": False, "detail": "x"},
                 "The update to version v0.0.2 did not run.")):
            state = update.read_state(self.base)
            state["last_result"] = result
            update.write_state(self.base, state)

            body = self.client.get(reverse("update")).content.decode()

            self.assertIn(expected, body)
            self.assertNotIn("to main", body)
            self.assertNotIn("<strong>main", body)

    def test_a_failed_apply_is_reported_on_the_page(self):
        """`apply_staged` swallows the failure so the launch survives, which means this page
        is the only place it is ever seen."""
        update.write_state(self.base, {
            "channel": update.STABLE,
            "last_result": {"ref": "v9.9.9", "ok": False, "detail": "no origin remote"},
        })
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

        self.assertContains(response, "did not run")
        self.assertContains(response, "no origin remote")

    @override_settings(MUTINT_UPDATE_ENABLED=False)
    def test_a_deployment_can_turn_it_off(self):
        """ALEdb does: private repository, updated by hand. The page still renders -- the
        component inventory is worth having either way -- and offers nothing."""
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("update"))

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "not enabled on this deployment")
        self.assertNotContains(response, "Check for updates")


@override_settings(MUTINT_UPDATE_ENABLED=True)
class EndpointTestCase(TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        patcher = mock.patch("mutint_update.views.update_root", return_value=self.base)
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

        self.assertEqual(403, self._post("update_check", {}).status_code)

    def test_install_refuses_an_ordinary_user(self):
        self.client.force_login(self.ordinary)

        self.assertEqual(403, self._post("update_install", {"ref": "v1"}).status_code)

    def test_check_refuses_an_unknown_channel(self):
        self.client.force_login(self.superuser)

        self.assertEqual(400, self._post("update_check", {"channel": "nightly"}).status_code)

    def test_check_remembers_the_channel(self):
        self.client.force_login(self.superuser)

        self._post("update_check", {"channel": update.MAIN})

        self.assertEqual(update.MAIN, update.read_state(self.base)["channel"])

    def test_check_reports_why_it_could_not_look(self):
        """A temporary directory is not a checkout, so this exercises the path a page in a
        broken installation takes: a sentence, not a 500."""
        self.client.force_login(self.superuser)

        response = self._post("update_check", {})

        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.json()["available"])
        self.assertIn("adopt", response.json()["error"])

    def test_install_refuses_a_ref_nothing_offered(self):
        """Otherwise this endpoint takes an arbitrary string from a form field and hands it
        to `git checkout` on the next launch, which is a much larger promise than the page
        is making."""
        update.write_state(self.base, {"channel": update.STABLE,
                                        "available": {"ref": "v1.2.3"}})
        self.client.force_login(self.superuser)

        response = self._post("update_install", {"ref": "v9.9.9"})

        self.assertEqual(409, response.status_code)
        self.assertNotIn("requested", update.read_state(self.base))

    def test_install_refuses_nothing(self):
        self.client.force_login(self.superuser)

        self.assertEqual(400, self._post("update_install", {"ref": " "}).status_code)

    def test_install_refuses_when_the_checkout_would_block_it(self):
        """The same refusals `./mutint update` makes, asked before staging rather than at
        the next launch -- a request that could only fail is not worth taking."""
        update.write_state(self.base, {"channel": update.STABLE,
                                        "available": {"ref": "v1.2.3"}})
        self.client.force_login(self.superuser)

        response = self._post("update_install", {"ref": "v1.2.3"})

        self.assertEqual(409, response.status_code)
        self.assertNotIn("requested", update.read_state(self.base))

    def test_install_stages_and_records_who_asked(self):
        update.write_state(self.base, {"channel": update.STABLE,
                                        "available": {"ref": "v1.2.3"}})
        self.client.force_login(self.superuser)
        with mock.patch.object(update, "blockers", return_value=[]):
            response = self._post("update_install", {"ref": "v1.2.3"})

        self.assertEqual(200, response.status_code)
        staged = update.read_state(self.base)["requested"]
        self.assertEqual("v1.2.3", staged["ref"])
        self.assertEqual("root", staged["by"])

    def test_a_non_json_body_is_a_400_not_a_500(self):
        self.client.force_login(self.superuser)

        response = self.client.post(reverse("update_check"), data="not json",
                                    content_type="application/json")

        self.assertEqual(400, response.status_code)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.superuser)

        self.assertEqual(405, self.client.get(reverse("update_check")).status_code)


class AccountBlockTestCase(TestCase):
    """The link lives in base.html, so it is gated by the template rather than by a registry."""

    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r2@example.com", "pw")

    def test_a_superuser_is_offered_the_link(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("jobs"))

        self.assertContains(response, reverse("update"))

    def test_an_ordinary_user_is_not(self):
        """nav_registry has no per-user visibility concept, which is the whole reason this
        entry is written into the template; an entry that renders for everyone and 403s is
        the dead end this codebase refuses."""
        self.client.force_login(self.ordinary)

        response = self.client.get(reverse("jobs"))

        self.assertNotContains(response, reverse("update"))


class EnabledSettingTestCase(TestCase):
    """The old setting name still turns the feature off."""

    def test_the_old_name_is_honoured_when_the_new_one_is_absent(self):
        from django.test import override_settings
        from mutint_update.views import _enabled

        with override_settings(MUTINT_UPGRADE_ENABLED=False):
            self.assertFalse(_enabled())

    def test_the_new_name_wins(self):
        from django.test import override_settings
        from mutint_update.views import _enabled

        with override_settings(MUTINT_UPGRADE_ENABLED=False, MUTINT_UPDATE_ENABLED=True):
            self.assertTrue(_enabled())
