"""Stopping MutInt and starting it again from the page that stages an update.

**Nothing here sends a signal or execs.** `restart._signal_later` is patched in every test
that reaches it, because the pid it would signal under the test runner is the test runner's
own parent; `os.execve` is patched wherever the exec hook is driven, because a real one
would replace the test process with a server. What is asserted is the decision each would
have been handed.

`MUTINT_UPDATE_ENABLED` is pinned on for the same reason the rest of this app's tests pin
it: ALEdb sets it False, and an assertion about the enabled behaviour would otherwise pass
here and fail there.
"""

import os
import re
import shutil
import signal
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from mutint_common import update
from mutint_update import restart

ARMED = {restart.ARMED_ENV: "1"}


def _unarmed():
    return {k: v for k, v in os.environ.items() if k != restart.ARMED_ENV}


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


class ReloaderFlagTestCase(TestCase):
    """The name is spelled in three modules, and `server_pid` reading one Django does not
    set would signal the test runner's parent in production too."""

    def test_it_is_the_name_django_sets(self):
        from django.utils import autoreload

        self.assertEqual(autoreload.DJANGO_AUTORELOAD_ENV, restart.RELOADER_CHILD_ENV)

    def test_it_is_the_name_the_launch_gate_reads(self):
        """`start.py` spells it again rather than have a view import a management command;
        this is what keeps the two copies one fact."""
        from mutint_common.management.commands import start

        self.assertEqual(start.RELOADER_CHILD_ENV, restart.RELOADER_CHILD_ENV)


class ArmedTestCase(TestCase):
    def test_absent_means_it_cannot_start_itself_again(self):
        with mock.patch.dict(os.environ, _unarmed(), clear=True):
            self.assertFalse(restart.armed())

    def test_the_mark_is_exactly_one(self):
        with mock.patch.dict(os.environ, {restart.ARMED_ENV: "yes"}):
            self.assertFalse(restart.armed())
        with mock.patch.dict(os.environ, ARMED):
            self.assertTrue(restart.armed())


class RelaunchArgvTestCase(TestCase):
    """What `./mutint start` is started again as."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.script = os.path.join(self.root, "mutint")
        open(self.script, "w").close()

    def _managed_python(self):
        path = os.path.join(self.root, "env", "python", "bin", "python3")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def test_the_managed_interpreter_is_preferred(self):
        """The one the entry script provisioned for itself: the host `python3` the shebang
        names may be a stub, or may have changed since this process started."""
        python = self._managed_python()
        argv = restart.relaunch_argv([self.script, "start"], project_root=self.root)
        self.assertEqual([python, self.script, "start"], argv)

    def test_without_one_the_script_runs_itself(self):
        argv = restart.relaunch_argv(["./mutint", "start"], project_root=self.root)
        self.assertEqual([os.path.abspath("./mutint"), "start"], argv)

    def test_the_launch_arguments_are_carried(self):
        """`--workers 3` was this launch's; the restarted one is the same launch."""
        argv = restart.relaunch_argv([self.script, "start", "--workers", "3"],
                                     project_root=self.root)
        self.assertEqual(["start", "--workers", "3"], argv[-3:])

    def test_the_root_comes_from_the_entry_scripts_export_when_there_is_one(self):
        with mock.patch.object(update, "project_root", return_value=self.root):
            self._managed_python()
            argv = restart.relaunch_argv(["/elsewhere/mutint", "start"])
        self.assertTrue(argv[0].startswith(self.root))

    def test_and_from_the_script_otherwise(self):
        with mock.patch.object(update, "project_root", return_value=None):
            self._managed_python()
            argv = restart.relaunch_argv([self.script, "start"])
        self.assertTrue(argv[0].startswith(self.root))


class ArmTestCase(TestCase):
    """What arming the process does, with nothing allowed to actually happen."""

    def _arm(self):
        with mock.patch.object(restart.signal, "signal") as sig, \
                mock.patch.object(restart.atexit, "register") as register, \
                mock.patch.dict(os.environ, _unarmed(), clear=True):
            argv = restart.arm()
            marked = restart.armed()
        return sig, register, marked, argv

    def test_it_marks_the_environment_for_the_child_to_read(self):
        self.assertTrue(self._arm()[2])

    def test_it_takes_the_restart_signal(self):
        sig = self._arm()[0]
        self.assertEqual((restart.RESTART_SIGNAL, restart._on_restart_signal), sig.call_args.args)
        self.assertEqual(signal.SIGUSR1, restart.RESTART_SIGNAL)

    def test_it_registers_the_exec_hook(self):
        register = self._arm()[1]
        register.assert_called_once_with(restart._relaunch_if_requested)

    def test_it_decides_the_relaunch_now_and_says_what_it_decided(self):
        argv = self._arm()[3]
        self.assertEqual(argv, restart._relaunch)
        self.assertTrue(argv[-1].endswith(os.path.basename(argv[-1])))


class RestartSignalTestCase(TestCase):
    """The handler exits the way Ctrl-C does, and remembers that it was asked to."""

    def tearDown(self):
        restart._requested = False

    def test_it_raises_system_exit_zero(self):
        with self.assertRaises(SystemExit) as raised:
            restart._on_restart_signal(restart.RESTART_SIGNAL, None)
        self.assertEqual(0, raised.exception.code)

    def test_and_marks_the_request(self):
        self.assertFalse(restart._requested)
        with self.assertRaises(SystemExit):
            restart._on_restart_signal(restart.RESTART_SIGNAL, None)
        self.assertTrue(restart._requested)


class RelaunchHookTestCase(TestCase):
    """The atexit hook: an exec over this process, only when the signal asked for it."""

    def setUp(self):
        self.argv = ["/env/python/bin/python3", "/checkout/mutint", "start"]
        self.addCleanup(setattr, restart, "_requested", False)
        self.addCleanup(setattr, restart, "_relaunch", None)
        restart._relaunch = list(self.argv)

    def _run(self, requested, env=None):
        restart._requested = requested
        with mock.patch.object(restart.os, "execve") as execve, \
                mock.patch.dict(os.environ, env or {}), \
                mock.patch.object(restart.sys, "stdout"), \
                mock.patch.object(restart.sys, "stderr") as stderr:
            restart._relaunch_if_requested()
        return execve, stderr

    def test_a_plain_exit_execs_nothing(self):
        """Ctrl-C, a SIGTERM, a crash: the hook returns and the cluster's own hook runs
        after it as it always did."""
        execve, _stderr = self._run(requested=False)
        execve.assert_not_called()

    def test_a_requested_restart_execs_the_decided_command(self):
        execve, _stderr = self._run(requested=True)
        execve.assert_called_once()
        self.assertEqual(self.argv[0], execve.call_args.args[0])
        self.assertEqual(self.argv, execve.call_args.args[1])

    def test_nothing_decided_means_nothing_to_exec(self):
        restart._relaunch = None
        execve, _stderr = self._run(requested=True)
        execve.assert_not_called()

    def test_the_new_launch_is_a_first_launch_with_a_fresh_database_answer(self):
        """No `RUN_MAIN` -- the parent never has one, and this is the belt to that -- and
        none of what the entry script exports about the cluster, which the exec'd entry
        script re-derives. Everything else rides along: `MUTINT_WORKERS`, `PATH`."""
        execve, _stderr = self._run(requested=True, env={
            restart.RELOADER_CHILD_ENV: "true", "MUTINT_DB_HOST": "/tmp/sock",
            "MUTINT_DB_MANAGED": "1", "MUTINT_WORKERS": "3", restart.ARMED_ENV: "1"})
        env = execve.call_args.args[2]
        self.assertNotIn(restart.RELOADER_CHILD_ENV, env)
        self.assertNotIn("MUTINT_DB_HOST", env)
        self.assertNotIn("MUTINT_DB_MANAGED", env)
        self.assertNotIn(restart.ARMED_ENV, env)
        self.assertEqual("3", env["MUTINT_WORKERS"])
        self.assertIn("PATH", env)

    def test_a_failed_exec_is_reported_and_not_raised(self):
        """atexit swallows exceptions anyway; the person at the terminal should read why
        MutInt stopped instead of coming back, and what to type."""
        restart._requested = True
        with mock.patch.object(restart.os, "execve", side_effect=OSError(2, "No such file")), \
                mock.patch.object(restart.sys, "stdout"), \
                mock.patch.object(restart.sys, "stderr") as stderr:
            restart._relaunch_if_requested()
        written = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("Could not start MutInt again", written)
        self.assertIn("/checkout/mutint start", written)


class SignalLaterTestCase(TestCase):
    def test_it_waits_and_then_sends_the_restart_signal(self):
        """The response to the request asking for this has to reach the browser first --
        the process writing it is the one about to be killed."""
        with mock.patch.object(restart.threading, "Timer") as timer:
            restart._signal_later(4242)
        self.assertEqual((restart.RESPONSE_GRACE_SECONDS, os.kill,
                          (4242, restart.RESTART_SIGNAL)), timer.call_args.args)
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()


class RequestRestartTestCase(TestCase):
    def setUp(self):
        # See RestartEndpointTestCase.setUp: the note must not land in this checkout's own
        # state file, where another test reads and clears it.
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        root = mock.patch.object(update, "project_root", return_value=self.base)
        root.start()
        self.addCleanup(root.stop)

    def test_it_refuses_where_nothing_armed_it(self):
        with mock.patch.dict(os.environ, _unarmed(), clear=True):
            with self.assertRaises(RuntimeError):
                restart.request_restart()

    def test_it_leaves_a_note_for_the_launch_it_is_asking_for(self):
        """So the relaunched MutInt does not open a browser over the window that is polling
        for it. On disk before the signal, so a relaunch that fails or is retried still
        finds it."""
        with mock.patch.dict(os.environ, ARMED), \
                mock.patch.object(restart, "_signal_later"):
            restart.request_restart()

        self.assertTrue(update.take_restart_note(self.base))

    def test_it_signals_the_server_process_not_itself(self):
        with mock.patch.dict(os.environ, {**ARMED, restart.RELOADER_CHILD_ENV: "true"}), \
                mock.patch.object(restart, "_signal_later") as later:
            pid = restart.request_restart()

        self.assertEqual(os.getppid(), pid)
        later.assert_called_once_with(pid)


@override_settings(MUTINT_UPDATE_ENABLED=True)
class RestartEndpointTestCase(TestCase):
    def setUp(self):
        # **The state file is patched for the whole class, not per test.** `request_restart`
        # leaves a note in it, and unpatched that is *this checkout's* `data/update.json` --
        # which another test then reads and clears, so the damage shows up somewhere else, once,
        # depending on the order tests ran in.
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        root = mock.patch.object(update, "project_root", return_value=self.base)
        root.start()
        self.addCleanup(root.stop)

        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.ordinary = User.objects.create_user("reader", "r@example.com", "pw")

    def _post(self):
        return self.client.post(reverse("update_restart"), data="{}",
                                content_type="application/json")

    def test_it_refuses_an_ordinary_user(self):
        """The gate on a write endpoint is not the gate on the page that offered it, and this
        one stops the server every reader of the deployment is being served by."""
        self.client.force_login(self.ordinary)
        self.assertEqual(403, self._post().status_code)

    def test_it_refuses_a_copy_not_started_by_the_entry_script(self):
        """A bare `runserver` or a WSGI server: nothing armed it, so stopping it would be
        the whole of what a restart did. Refusing beats calling that a restart."""
        self.client.force_login(self.superuser)

        with mock.patch.dict(os.environ, _unarmed(), clear=True):
            response = self._post()

        self.assertEqual(409, response.status_code)
        self.assertIn("./mutint start", response.json()["error"])

    def test_it_answers_before_it_happens(self):
        self.client.force_login(self.superuser)

        with mock.patch.dict(os.environ, ARMED), \
                mock.patch.object(restart, "_signal_later") as later:
            response = self._post()

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["restarting"])
        self.assertEqual(1, later.call_count)

    @override_settings(MUTINT_UPDATE_ENABLED=False)
    def test_a_deployment_that_declined_updates_declines_this(self):
        self.client.force_login(self.superuser)

        with mock.patch.dict(os.environ, ARMED):
            response = self._post()

        self.assertEqual(409, response.status_code)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.superuser)
        self.assertEqual(405, self.client.get(reverse("update_restart")).status_code)


@override_settings(MUTINT_UPDATE_ENABLED=True)
class RestartButtonTestCase(TestCase):
    """**Offered only when there is an update to install: one a check found, or one staged.**

    Restarting is what installs an update, so where MutInt can start itself again the
    button stages and restarts in one click. With nothing to install it is on the page but
    hidden -- the check's script is what reveals it -- because a visible one would read as a
    general "restart the server", which is not what this page is for.

    The endpoint stays willing either way. It is a legitimate thing to ask for -- `./mutint
    update` from a shell moves the checkout and leaves the running server on the old code,
    and restarting is exactly the fix -- so what narrows here is the offer, not the operation.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        root = mock.patch("mutint_update.views.update_root", return_value=self.base)
        root.start()
        self.addCleanup(root.stop)
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(self.superuser)

    def _html(self):
        return self.client.get(reverse("update")).content.decode("utf-8")

    def _button(self, html):
        return re.search(r'<button[^>]*id="update-restart"[^>]*>', html).group(0)

    def test_the_button_is_hidden_with_nothing_to_install(self):
        with mock.patch.dict(os.environ, ARMED):
            html = self._html()

        self.assertIn("display: none", self._button(html))

    def test_the_button_shows_for_a_staged_update_and_says_so_to_the_script(self):
        update.request(self.base, "v1.2.3", by="root")

        with mock.patch.dict(os.environ, ARMED):
            html = self._html()

        button = self._button(html)
        self.assertNotIn("display: none", button)
        # Already staged, so the click must restart without staging again.
        self.assertIn('data-staged="1"', button)
        self.assertIn("Restart to install updates", html)
        self.assertIn("Restart MutInt to install it", html)

    def test_no_button_for_a_copy_that_cannot_start_itself(self):
        """Staged, but not running under `./mutint start`: the page says to quit and start
        it again, which is the same two steps by hand."""
        update.request(self.base, "v1.2.3", by="root")

        with mock.patch.dict(os.environ, _unarmed(), clear=True):
            html = self._html()

        self.assertNotIn('id="update-restart"', html)
        self.assertIn("Quit MutInt and start it again", html)
