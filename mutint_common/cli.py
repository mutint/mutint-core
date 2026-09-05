import os
import sys


def manage():
    """Entry point — equivalent to manage.py with settings_local pre-set."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_local')

    # Django's ManagementUtility answers `version` itself, with Django's version,
    # before it ever looks at an app's commands -- the name is reserved and cannot
    # be overridden the usual way. `./mutint version` should report mutint-core, so
    # dispatch that one command directly. It is still listed under mutint_common in
    # `./mutint help`, and still reachable as a normal command elsewhere.
    if sys.argv[1:2] == ['version']:
        import django
        django.setup()
        from mutint_common.management.commands.version import Command
        # run_from_argv exits itself on CommandError, and returns on success --
        # same contract as execute_from_command_line below.
        Command().run_from_argv(sys.argv[:1] + ['version'] + sys.argv[2:])
        return

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
