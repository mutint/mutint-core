"""``./mutint import`` -- the shell's half of the Import data page.

The module is named for a keyword, which Python's `import` statement cannot spell. Django
loads commands through `importlib.import_module` on a string, so this is fine; nothing may
`from mutint_import.management.commands import import` it, and nothing needs to.
"""

from django.core.management import BaseCommand, CommandError

from mutint_import.experiments import import_paths, resolve_experiment


class Command(BaseCommand):

    help = ("Import breseq folders, .gd files and reference genomes into an experiment, "
            "the same way the Import data page does.")

    def add_arguments(self, parser):
        parser.add_argument('path(s)', nargs='+', type=str,
                            help='Path(s) to import. A directory is walked the way a '
                                 'dropped folder is.')
        parser.add_argument('--experiment-id', type=int,
                            help='Primary key of an existing experiment to add to. This is '
                                 'what the Import data page uses, and two experiments may share a '
                                 'name -- prefer it when the experiment exists.')
        parser.add_argument('--project', type=str,
                            help='Project name, created if it does not exist.')
        parser.add_argument('--experiment', type=str,
                            help='Experiment name within that project, created if it does '
                                 'not exist.')
        parser.add_argument('--owner', type=str,
                            help='Username or full name of the user who owns a newly '
                                 'created project. Required with --project; defaults to '
                                 'the project owner with --experiment-id.')
        parser.add_argument('--public', action='store_true',
                            help='Make a newly created project public.')

    def handle(self, *args, **options):
        try:
            experiment, user = resolve_experiment(
                experiment_id=options.get('experiment_id'),
                project_name=options.get('project'),
                experiment_name=options.get('experiment'),
                owner=options.get('owner'),
                is_public=options.get('public', False))
        except ValueError as error:
            raise CommandError(str(error))

        import_paths(options['path(s)'], experiment, user)
