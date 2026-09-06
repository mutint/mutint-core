"""Load a component's example dataset.

    ./mutint load_example                            # what is available
    ./mutint load_example mutint-compare-fixation-example     # load it
    ./mutint load_example mutint-compare-fixation-example --replace

A component that needs particular data to show its feature ships that data and registers it
(``mutint_common.example_registry``); this loads it. The dataset is a directory laid out as it
would be dropped on the Import data page, so loading runs the real import handlers in priority
order -- reference first, then the mutation files -- and ends in the post-experiment hooks.
Whatever the example exists to demonstrate is therefore computed here, not shipped.

The command is `load_example`, not `load-example`: Django takes a command's name from its
module filename and a module name cannot contain a hyphen. `purge_deleted`, `load_projects`
and `rebuild_stats` are the same. Dataset *names* are registry strings and keep their hyphens.
"""

import os
import shutil
import tempfile

from django.core.management.base import BaseCommand, CommandError

from mutint_common.example_registry import get_example_dataset, get_example_datasets

# One project holds every example, so they are easy to find and easy to delete.
EXAMPLE_PROJECT = "Examples"


def _is_documentation(entry):
    """Whether a file in a dataset directory is prose rather than data.

    A dataset wants its README beside its data -- the expected answer belongs next to the
    files that produce it. But `run_import` reports anything no handler claims as a failed
    file, so an unfiltered copy makes every dataset with a README look half-imported. Dotfiles
    go the same way for the same reason.
    """
    lowered = entry.lower()
    return entry.startswith(".") or lowered.startswith("readme")


class Command(BaseCommand):

    help = "Load a component's example dataset. With no name, list what is registered."

    def add_arguments(self, parser):
        parser.add_argument("name", nargs="?", default=None,
                            help="dataset to load; omit to list what is registered")
        parser.add_argument("--user", dest="username", default=None,
                            help="who owns the created project (default: a superuser)")
        parser.add_argument("--replace", action="store_true",
                            help="rebuild the dataset if it has already been loaded")

    def handle(self, *args, **options):
        name = options.get("name")
        if not name:
            # Deliberately no database access: this has to work on a checkout that has
            # never been migrated, which is exactly when someone asks what data they can get.
            self._list()
            return

        dataset = get_example_dataset(name)
        if dataset is None:
            raise CommandError(
                "no such example dataset: %s\n\nRegistered:\n%s"
                % (name, self._listing() or "  (none)"))

        if not os.path.isdir(dataset["directory"]):
            raise CommandError(
                "%s registers a directory that is not there: %s"
                % (name, dataset["directory"]))

        self._load(dataset, options)

    # --- listing ------------------------------------------------------------------------

    def _listing(self):
        rows = []
        for dataset in get_example_datasets():
            rows.append("  %-28s %-16s %s" % (dataset["name"], dataset["component"],
                                              dataset["description"]))
        return "\n".join(rows)

    def _list(self):
        datasets = get_example_datasets()
        if not datasets:
            self.stdout.write(
                "No example datasets are registered.\n"
                "A component registers one from AppConfig.ready() -- see "
                "mutint_common/example_registry.py.")
            return
        self.stdout.write("%-30s %-16s %s" % ("NAME", "COMPONENT", "DESCRIPTION"))
        self.stdout.write(self._listing())
        self.stdout.write("\nLoad one with: ./mutint load_example <name>")

    # --- loading ------------------------------------------------------------------------

    def _load(self, dataset, options):
        from mutint_common.import_registry import run_import
        from mutint_experiment.models import Experiment, Project, live
        from mutint_experiment.permissions import set_primary_owner
        from mutint_experiment.views import _create_experiment

        user = self._resolve_user(options.get("username"))
        name = dataset["name"]

        existing = live(Experiment.objects.filter(name=name)).first()
        if existing is not None:
            if not options.get("replace"):
                raise CommandError(
                    "%s is already loaded as experiment %s. "
                    "Re-run with --replace to rebuild it."
                    % (name, existing.id))
            # Soft: the row survives for purge_deleted, and its mutations go with it out of
            # every list. Rebuilding into the old experiment instead would mix two imports.
            existing.soft_delete(user)
            self.stdout.write("Replacing experiment %s." % existing.id)

        project = live(Project.objects.filter(name=EXAMPLE_PROJECT, user=user)).first()
        if project is None:
            project = Project.objects.create(
                name=EXAMPLE_PROJECT, user=user, is_public=False, status="in progress",
                description="Example datasets loaded by ./mutint load_example.")
            # `set_primary_owner` writes the owner grant alongside `Project.user`, which
            # is what the web view does. The lookup above finds the project by `user`, so
            # the two have to stay in step.
            set_primary_owner(project, user)

        experiment = _create_experiment(project, name, user)

        # Copy before importing: the handlers consume what they are given, and the dataset
        # ships in a component's checkout.
        staged_root = tempfile.mkdtemp(prefix="mutint-example-")
        try:
            for entry in sorted(os.listdir(dataset["directory"])):
                if _is_documentation(entry):
                    continue
                source = os.path.join(dataset["directory"], entry)
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(staged_root, entry))
            summary = run_import(experiment, staged_root, user)
        finally:
            shutil.rmtree(staged_root, ignore_errors=True)

        self._report(dataset, experiment, summary)

    def _resolve_user(self, username):
        from django.contrib.auth.models import User

        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError("no such user: %s" % username)

        user = User.objects.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError(
                "no superuser to own the example. Create one, or name a user with --user. "
                "`./mutint start` creates an admin.")
        return user

    def _report(self, dataset, experiment, summary):
        """`run_import` reports per file, not a top-level error list -- an unrecognized
        file is a `files` entry carrying an `error`. Surface those individually: a dataset
        whose reference failed to establish still imports its .gd files and would otherwise
        look like a success with suspiciously few mutations."""
        files = (summary or {}).get("files") or []
        failed = [entry for entry in files if entry.get("error")]

        self.stdout.write("Loaded %s as experiment %s (%s)."
                          % (dataset["name"], experiment.id, experiment.name))
        self.stdout.write("  %d file(s), %d mutation(s)."
                          % (len(files), (summary or {}).get("total_mutations") or 0))
        for entry in failed:
            self.stderr.write("  %s: %s" % (entry.get("file"), entry["error"]))
        if failed:
            raise CommandError(
                "%d of %d file(s) were not imported; the example is incomplete."
                % (len(failed), len(files)))
        self.stdout.write("  /stats?experiment_id=%s" % experiment.id)
