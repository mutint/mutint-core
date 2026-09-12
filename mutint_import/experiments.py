import mutint_experiment.models
from mutint_import import breseq_folder
import mutint_sample.models
import mutint_sample.views.common
import logging
from mutint_experiment.models import Experiment, Project
from django.contrib.auth.models import User
from datetime import datetime
from mutint_experiment.permissions import set_primary_owner

BRESEQ_OUTPUT_REPORT_DIR = "output/"
BRESEQ_LOG_FILE = "log.txt"
METADATA_RELATIVE_PATH = 'metadata/'
REF_RELATIVE_PATH = 'ref/'

logger = logging.getLogger(__name__)


def remove_time_point(population_pk, time_point):
    """Delete every sample at one time point of one population.

    Executed from Django ipython shell.

    It took a `TimePoint` primary key and deleted the row, taking its samples by cascade.
    There is no row now, so the coordinate is the argument and the samples are deleted
    directly -- which is what the operation always meant.
    """
    from mutint_common.rebuild_registry import request_rebuild

    population = mutint_experiment.models.Population.objects.get(pk=population_pk)
    experiment_id = population.experiment_id
    mutint_sample.models.Sample.objects.filter(
        population=population, time_point=time_point).delete()
    _delete_all_orphaned_mutations()
    # After the delete, not before: marking data stale that is about to change again would be
    # cleared by any rebuild that ran in between. Marked rather than rebuilt, because this is
    # a shell operation and the next reader of any of it will rebuild what it needs.
    request_rebuild(experiment_id, reason='time point removed')


def delete_experiments(experiment_ids):
    """Hard-delete experiments and everything below them.

    Executed from Django ipython shell, and by `./mutint delete` and `purge_deleted`.
    """
    from mutint_common.rebuild_registry import SITE_SCOPE, run_rebuilds

    for exp_id in experiment_ids:
        experiment_to_delete = mutint_experiment.models.Experiment.objects.get(pk=exp_id)
        print("Deleting Experiment #" + str(exp_id) + ":", experiment_to_delete.name)
        message = "Experiment %s was deleted" % experiment_to_delete.name
        experiment_to_delete.delete()
        # The `StaticData` sweep that stood here is gone with the table. It was needed because
        # that row had no FK to the experiment -- only the convention that its pk *was* the
        # experiment's -- so a cascade could not reach it, and `get()` raised on an experiment
        # that had never been post-processed, leaving the delete half-finished. Nothing
        # derived from this experiment outlives it now, because nothing derived is stored.
        print(message)
    _delete_all_orphaned_mutations()
    print("deleted orphaned mutations")
    # Through the registry rather than calling `rebuild_dashboard_data()` directly, which is
    # what stood here. Both produce the right numbers; only this one *clears the flag*. The
    # direct call left `stale_since` set and `rebuilt_at` unwritten, so the next reader of
    # /dashboard recomputed the same answer over again -- the mechanism bypassed rather than
    # used.
    #
    # `scope=SITE_SCOPE` rather than naming the two dashboard rebuilders: the installation-wide
    # totals are exactly the site-scoped ones, and a list of names here would be a second copy
    # of something `mutint_dashboard` already declares. `force=True` because this deletion is
    # the change -- there is no earlier `request_rebuild` for it to answer, and running eagerly
    # is right for a command the operator is already waiting on.
    run_rebuilds(scope=SITE_SCOPE, force=True)
    # The rows are gone and the files are not: nothing here touches the store, so every
    # sample directory these experiments had is now owned by nobody. That is the dashboard's
    # unattributed line, marked rather than walked -- this is a shell command, and the
    # dashboard recounts on its next view. (`purge_deleted` removes the files afterwards and
    # marks it again.)
    from mutint_common.rebuild_registry import request_rebuild
    from mutint_common.storage_registry import UNATTRIBUTED_REBUILD
    request_rebuild(only=[UNATTRIBUTED_REBUILD], reason='experiments hard-deleted')


def _delete_all_orphaned_mutations():
    """Find the orphaned muations that don't have associated mutation calls.
    Retrieving mutation calls for each mutation to check if it is orphan is very expensive
    """
    orphans = mutint_sample.models.Mutation.objects.raw(
        'select * from mutint_sample_mutation m where not exists (select * from mutint_sample_mutationcall ob where ob.mutation_id = m.id)')
    for mutation in orphans:
        mutation.delete()


def delete_sample(experiment_pk, population_name, time_point, sample_name):
    """Delete the sample at a coordinate.

    Executed from Django ipython shell.

    It deleted an `Isolate` and took its replicates and their runs with it by cascade.
    There is one row now, so the cascade it relied on is the row itself -- and `sample_name`
    is the whole label (`1-2`), not the isolate half of a pair.
    """
    from mutint_common.rebuild_registry import request_rebuild

    for sample in mutint_sample.models.Sample.objects.filter(name=sample_name):
        if sample.population.experiment_id == experiment_pk and \
                sample.population.name == population_name and \
                sample.time_point == time_point:
            sample.delete()
            print("Successfully removed: ", population_name, time_point, sample_name)
    _delete_all_orphaned_mutations()
    # This marked nothing, where its sibling `remove_time_point` always has. Deleting a
    # sample changes both dashboard counts -- the sample count directly, the mutation counts
    # through the calls that go with it and the mutations left orphaned -- so without
    # this the Overview kept reporting a sample that was gone until some unrelated write
    # happened to mark the totals stale.
    request_rebuild(experiment_pk, reason='sample removed')


def import_paths(paths, experiment, user):
    """Import each path into `experiment`, exactly the way a web drop is imported.

    Named for what it does rather than for how the bytes arrived: nothing is uploaded from a
    shell. The registry is `import_registry`, the handlers are import handlers and the page
    is `/import/`; `upload` was the one word in that chain saying something else.

    One `run_import` per path with **no import type named**, so auto-detect routes every
    file the way the Import data page does. That is the whole of the unification, and it is worth
    saying what the CLI gains by it: it used to call `breseq_folder.import_breseq_folders`
    directly, which is one handler of four, so a `.gd` sitting loose beside the folders was
    imported only by the flag that told `import_breseq_folders` to report it, a reference
    genome dropped in was ignored, and an import type registered by a *plugin* was
    unreachable from the shell entirely. All of that now works here because none of it is
    known here.

    What went with the old path is the metadata directory. `<exp>/metadata/*.csv` carried
    the project name, the experiment name and the person, and `find_experiment_paths` would
    only walk a directory that had one -- so identity came out of a file format nothing else
    in the suite read. It is options now, which is what the web has always done: the Add
    page is scoped to an experiment by primary key before a byte is uploaded.
    """
    from mutint_common.import_registry import run_import

    summaries = []
    for path in paths:
        print("Importing", path, "into", experiment.name)
        summary = run_import(experiment, path, user)
        for result in summary["files"]:
            if result["error"]:
                print("  %s: %s" % (result["file"], result["error"]))
            else:
                count = result.get("mutations")
                print("  %s: %s" % (result["file"],
                                    "reference" if count is None
                                    else "%d mutations" % count))
            for warning in result.get("warnings") or []:
                print("    warning: %s" % warning)
        print("  %d mutations from %d file(s)"
              % (summary["total_mutations"], len(summary["files"])))
        summaries.append(summary)
    return summaries


def resolve_experiment(*, experiment_id=None, project_name=None, experiment_name=None,
                       owner=None, is_public=False):
    """The experiment an upload targets, and the user who owns it.

    Two ways in, and they are the two the product already had:

    - `experiment_id` is the web's form. `/import/?experiment_id=<pk>` is scoped by
      primary key precisely so two experiments may share a name and two people may add to
      one, and a shell upload should not be weaker than that.
    - `project_name` + `experiment_name` get-or-create, which is what the metadata file used
      to supply. It reaches one experiment per name per project, so `--experiment-id` is
      still the way to add to the second of two that share a name.

    `owner` is a username or a real name, resolved the way the CLI has always resolved one.
    It is required when creating, because a new project needs an owner; with `experiment_id`
    it defaults to the project's owner, so the common case needs no flag. **It names a real
    `User`** -- it is not the free-text attribution `Experiment.person` used to hold, which
    is gone.
    """
    from mutint_import.gd_import import _prepare_experiment

    if experiment_id is not None:
        experiment = Experiment.objects.get(pk=experiment_id)
        user = find_user(owner) if owner else experiment.project.user
        return experiment, user

    if not (project_name and experiment_name):
        raise ValueError(
            "give --experiment-id, or both --project and --experiment")
    if not owner:
        raise ValueError("--owner is required when naming a project and experiment")

    user = find_user(owner)
    context = _prepare_experiment(project_name, experiment_name, owner, is_public)
    return context["experiment"], user


def find_user(user):
    potential_user_list = []
    while len(potential_user_list) == 0:
        try:
            return User.objects.get(username=user)
        except User.DoesNotExist:
            name_parts = user.split(' ')

            potential_user_list = User.objects.filter(first_name__iexact=name_parts[0]).filter(last_name__iexact=name_parts[len(name_parts)-1])
            if len(potential_user_list) == 1:
                return potential_user_list[0]
            potential_user_list = []

            print("User", user, "can't be found. Querying name parts individually.")

            for each in name_parts:
                potential_user_list=potential_user_list+list(User.objects.filter(first_name__icontains=each))
                potential_user_list=potential_user_list+list(User.objects.filter(last_name__icontains=each))
            # Deduplicated *and ordered*. This was `list(set(...))`, and a set of model
            # instances iterates in hash order -- which for a Django model is its primary
            # key's -- so the numbered menu below came out in an order nobody chose and that
            # moved as soon as the primary keys did. This prompt decides which person an
            # experiment is attributed to; "type 0" meaning somebody different between two
            # runs is the last thing it should do. Ordered by primary key, so the menu reads
            # oldest account first -- an arbitrary rule, but a fixed one, which is the whole
            # requirement.
            potential_user_list = sorted(set(potential_user_list), key=lambda found: found.pk)
            if len(potential_user_list) == 1:
                return potential_user_list[0]
            if len(potential_user_list) == 0:
                user = input("No matches found for " + user + ". Please enter another name:")

    print("Multiple matches found for", user)
    while True:
        nb = input(
            "Please select one of the following users:\n" + str(dict(enumerate(potential_user_list))).replace(",",
                                                                                                               "\n").replace("{"," ").replace("}","\n"))
        try:
            selected = potential_user_list[int(nb)]
            print("you've selected:", selected)
            nb = input("is that correct? Yn\n")
            if nb == "Y" or nb == "y" or nb == "\n":
                return selected
            continue
        except (IndexError, ValueError) as e:
            print(e.__class__.__name__ + ": please select a value between 0 and", len(potential_user_list))
            continue


def try_creating_project(project, owner_name, is_pub=False):
    print("Creating", project, "project with owner", owner_name, "public:", is_pub)
    owner = find_user(owner_name)
    new_project = Project.objects.create(name=project, user=owner, date=datetime.now(),
                           status="In progress", is_public=is_pub)
    set_primary_owner(new_project, owner)
    return new_project

