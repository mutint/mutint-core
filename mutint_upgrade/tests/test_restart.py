"""Stopping MutInt and starting it again from the page that stages an upgrade.

**Nothing here starts a helper.** `restart._spawn` is patched in every test that reaches it,
because the helper's whole job is to signal a process this suite is running inside -- the pid
it would be handed under the test runner is the test runner. What is asserted instead is the
script it would have been given, which is where every decision actually lives.

`MUTINT_UPGRADE_ENABLED` is pinned on for the same reason the rest of this app's tests pin
it: ALEdb sets it False, and an assertion about the enabled behaviour would otherwise pass
here and fail there.
"""

import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from mutint_common import upgrade
from mutint_upgrade import restart

RELAUNCH = "/usr/bin/open '/Applications/MutInt.app'"


class ServerPidTestCase(TestCase):
    """Which process stopping stops MutInt.

    Under `runserver` there are two, and view code runs in the one that gets restarted
    whenever it dies -- so a view that kills its own pid achieves a reload, not a stop.
    """

    def test_the_reloaders_child_names_its_parent(self):
        with mock.patch.dict(os.environ, {restart.RELOADER_CHILD_ENV: "true"}):
            self.assertEqual(os.getppid(), restart.server_pid())

    def test_without_the_reloader_it_is_us(self):
        """`DEBUG` false turns the reloader off, which is what an installed deployment runs.
        There is no child then, and no parent of ours worth signalling."""
        env = {k: v for k, v in os.environ.items() if k != restart.RELOADER_CHILD_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(os.getpid(), restart.server_pid())


class RelaunchCommandTestCase(TestCase):
    def test_absent_means_nothing_can_start_it_again(self):
        env = {k: v for k, v in os.environ.items() if k != restart.RELAUNCH_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(restart.relaunch_command())

    def test_blank_is_absent_rather_than_a_command(self):
        """A launcher exporting an empty string has said nothing, and running it would be
        running `sh -c ''` after killing the server."""
        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: "   "}):
            self.assertIsNone(restart.relaunch_command())

    def test_it_is_taken_verbatim(self):
        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: RELAUNCH}):
            self.assertEqual(RELAUNCH, restart.relaunch_command())


class ScriptTestCase(TestCase):
    """The helper is shell, so this is where its decisions can be read."""

    def setUp(self):
        self.script = restart._script(4242, RELAUNCH)

    def test_it_waits_before_signalling(self):
        """The response to the request that asked for this has to reach the browser first --
        the process writing it is one of the ones about to be stopped."""
        self.assertTrue(self.script.startswith("sleep %d\n" % restart.RESPONSE_GRACE_SECONDS))

    def test_it_asks_politely_and_then_insists(self):
        self.assertIn("kill -TERM 4242", self.script)
        self.assertIn("kill -KILL 4242", self.script)

    def test_it_waits_for_the_process_to_be_gone_before_relaunching(self):
        """Not for the port: `runserver` sets SO_REUSEADDR. For the deadman supervisor, which
        is stopping the worker and the database cluster in that window."""
        self.assertLess(self.script.index("kill -0 4242"), self.script.index(RELAUNCH))
        self.assertIn("sleep %d" % restart.SETTLE_SECONDS, self.script)

    def test_the_relaunch_is_the_last_thing(self):
        self.assertTrue(self.script.rstrip().endswith(RELAUNCH))


class RequestRestartTestCase(TestCase):
    def test_it_refuses_when_nothing_said_how_to_start_it_again(self):
        env = {k: v for k, v in os.environ.items() if k != restart.RELAUNCH_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                restart.request_restart()

    def test_it_spawns_the_helper_with_the_pid_it_will_signal(self):
        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: RELAUNCH}), \
                mock.patch.object(restart, "_spawn") as spawn:
            pid = restart.request_restart()

        self.assertEqual(restart.server_pid(), pid)
        self.assertIn("kill -TERM %d" % pid, spawn.call_args[0][0])


@override_settings(MUTINT_UPGRADE_ENABLED=True)
class RestartEndpointTestCase(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r@example.com", "pw")

    def test_it_refuses_an_ordinary_user(self):
        """The gate on a write endpoint is not the gate on the page that offered it, and this
        one stops the server every reader of the deployment is being served by."""
        self.client.force_login(self.ordinary)

        response = self.client.post(reverse("upgrade_restart"),
                                    data="{}", content_type="application/json")

        self.assertEqual(403, response.status_code)

    def test_it_refuses_when_nothing_can_start_it_again(self):
        """Started from a terminal, the process that would run the relaunch command is the
        one being killed. Refusing beats stopping the server and calling it a restart."""
        env = {k: v for k, v in os.environ.items() if k != restart.RELAUNCH_ENV}
        self.client.force_login(self.superuser)

        with mock.patch.dict(os.environ, env, clear=True):
            response = self.client.post(reverse("upgrade_restart"),
                                        data="{}", content_type="application/json")

        self.assertEqual(409, response.status_code)
        self.assertIn("start it again", response.json()["error"])

    def test_it_answers_before_it_happens(self):
        self.client.force_login(self.superuser)

        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: RELAUNCH}), \
                mock.patch.object(restart, "_spawn") as spawn:
            response = self.client.post(reverse("upgrade_restart"),
                                        data="{}", content_type="application/json")

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["restarting"])
        self.assertEqual(1, spawn.call_count)

    @override_settings(MUTINT_UPGRADE_ENABLED=False)
    def test_a_deployment_that_declined_upgrades_declines_this(self):
        self.client.force_login(self.superuser)

        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: RELAUNCH}):
            response = self.client.post(reverse("upgrade_restart"),
                                        data="{}", content_type="application/json")

        self.assertEqual(409, response.status_code)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.superuser)
        self.assertEqual(405, self.client.get(reverse("upgrade_restart")).status_code)


@override_settings(MUTINT_UPGRADE_ENABLED=True)
class RestartButtonTestCase(TestCase):
    """**Offered in one state only: an upgrade is staged.**

    Restarting is what finishes that upgrade, and staged is the one moment where it is the
    obvious next thing to do. A button sitting in the row above permanently would read as a
    general "restart the server" -- which is not what this page is for, and is a strange thing
    to put a click away on a page that is otherwise an inventory.

    The endpoint stays willing either way. It is a legitimate thing to ask for -- `./mutint
    upgrade` from a shell moves the checkout and leaves the running server on the old code,
    and restarting is exactly the fix -- so what narrows here is the offer, not the operation.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        root = mock.patch("mutint_upgrade.views.upgrade_root", return_value=self.base)
        root.start()
        self.addCleanup(root.stop)
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(self.superuser)

    def _html(self):
        return self.client.get(reverse("upgrade")).content.decode("utf-8")

    def test_no_button_with_nothing_staged(self):
        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: RELAUNCH}):
            html = self._html()

        # `id="..."`, not the bare name: the script looks the button up by id whether or not
        # it was rendered, so the bare string is in the page either way.
        self.assertNotIn('id="upgrade-restart"', html)

    def test_the_button_appears_with_the_staged_message(self):
        upgrade.request(self.base, "v1.2.3", by="root")

        with mock.patch.dict(os.environ, {restart.RELAUNCH_ENV: RELAUNCH}):
            html = self._html()

        self.assertIn('id="upgrade-restart"', html)
        self.assertIn("Restart MutInt", html)
        # Beside the message it finishes, not somewhere else on the page.
        self.assertIn("v1.2.3 is staged", html)
        self.assertLess(html.index("is staged"), html.index('id="upgrade-restart"'))

    def test_no_button_when_nothing_can_start_it_again(self):
        """Staged, but launched from a terminal: the page says to quit and start it again,
        which is the same two steps by hand."""
        upgrade.request(self.base, "v1.2.3", by="root")
        env = {k: v for k, v in os.environ.items() if k != restart.RELAUNCH_ENV}

        with mock.patch.dict(os.environ, env, clear=True):
            html = self._html()

        self.assertNotIn('id="upgrade-restart"', html)
        self.assertIn("Quit MutInt and start it again", html)
