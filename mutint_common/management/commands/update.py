"""`./mutint update` -- move this installation onto a newer version of itself.

The terminal half of what `/update/` does from the sidebar. Both go through
`mutint_common/update.py`, so there is one definition of what an update is, what it refuses
and how a channel is resolved; this command adds the arguments and the sentences.

**It does not install dependencies and does not migrate.** Moving the working tree is the
whole of the job: the entry script's sentinels hash every component's `requirements.txt` and
`tools.txt`, so the next launch rebuilds whatever the new version changed, and `start.py`
migrates. So the last thing this prints is to run `./mutint start`, and that is not a
formality -- until it happens the code has moved and the database has not.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from mutint_common import pg, update


class Command(BaseCommand):
    help = "Check for a newer version of this installation, and move onto it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true",
            help="Ask what is available and stop; change nothing.")
        parser.add_argument(
            "--to", metavar="REF",
            help="Move to this tag or branch, whatever the channel offers.")
        parser.add_argument(
            "--channel", choices=update.CHANNELS,
            help="Remember which channel to follow: %s." % ", ".join(update.CHANNELS))
        parser.add_argument(
            "--adopt", metavar="URL", nargs="?", const=True,
            help="Turn a tree that has no git history into a checkout, in place. Takes the "
                 "remote URL; with no argument, the one already recorded.")
        parser.add_argument(
            "--no-backup", action="store_true",
            help="Skip the pg_dump taken before the working tree moves.")

    def handle(self, *args, **options):
        base_dir = update.project_root()
        if base_dir is None:
            # Every documented route sets it. Anything else is somebody running manage.py by
            # hand, where the working directory is not reliably the project.
            raise CommandError(
                "MUTINT_TOOLS_DIR is not set, so this cannot tell which installation to "
                "update. Run it as `./mutint update`.")

        if options["adopt"]:
            return self._adopt(base_dir, options)

        if options["to"]:
            return self._apply(base_dir, options["to"], options)

        state = update.check(base_dir, channel=options.get("channel"))
        self.stdout.write("Channel:   %s" % state.get("channel"))
        self.stdout.write("Installed: %s" % (update.current_ref(base_dir) or "unknown"))

        if state.get("error"):
            # Not a CommandError: "I could not check" is an answer, and one that should not
            # look like the command itself went wrong.
            self.stdout.write(self.style.WARNING(state["error"]))
            return

        available = state.get("available")
        if not available:
            self.stdout.write(self.style.SUCCESS("Up to date."))
            return

        self.stdout.write("Available: %s" % _describe(available))
        if options["check"]:
            self.stdout.write("Run `./mutint update` to install it.")
            return

        self._apply(base_dir, available["ref"], options)

    def _apply(self, base_dir, ref, options):
        self.stdout.write("Updating to %s..." % ref)
        try:
            result = update.apply(
                base_dir, ref,
                pg=pg if not options["no_backup"] else None,
                take_backup=not options["no_backup"])
        except update.UpdateError as exc:
            raise CommandError(str(exc))

        # The checkout has moved, so the stored verdict is void: without this, `/update/`
        # goes on offering the version this shell just installed. See `update.void_verdict`.
        update.void_verdict(base_dir)

        if result.get("backup"):
            self.stdout.write("Backed up the database to %s"
                              % os.path.relpath(result["backup"], base_dir))
        self.stdout.write(self.style.SUCCESS("Now on %s." % (result.get("now") or ref)))
        self.stdout.write(
            "\nRun `./mutint start` to finish: it installs whatever this version's "
            "requirements\nchanged and applies any new migrations. Until then the code has "
            "moved and the\ndatabase has not.")

    def _adopt(self, base_dir, options):
        url = options["adopt"]
        if url is True:
            url = update.origin_url(base_dir)
            if not url:
                raise CommandError(
                    "This tree has no git history and no recorded remote, so --adopt needs "
                    "the URL to clone from.")
        ref = options["to"]
        if not ref:
            raise CommandError("--adopt needs --to <tag> saying which release this tree is.")
        try:
            result = update.adopt(base_dir, url, ref)
        except update.UpdateError as exc:
            raise CommandError(str(exc))
        # Whatever was stored was recorded about a tree that was not a checkout -- an error
        # from a check that could not run, most likely. It says nothing about this one.
        update.void_verdict(base_dir)
        self.stdout.write(self.style.SUCCESS(
            "Adopted as a checkout of %s. Updates will work from here." % result["ref"]))


def _describe(available):
    if available.get("kind") == "branch":
        return "%s (%s)" % (available["ref"], available.get("sha") or "newer")
    return available["ref"]
