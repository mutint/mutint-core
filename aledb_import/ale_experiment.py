import os
import aledb_experiment.models
import aledb_import.util
from aledb_import import breseq_folder
import aledb_sample.models
import aledb_sample.views.common
from aledb_import.gdparse.gdparse import gdparse
from aledb_common.util import _find_between
import aledb_metadata.parser
import logging
from aledb_metadata.xpmdvalidator.validate import SCHEMA_PATH, is_valid
from aledb_experiment.models import Experiment, Project
from django.contrib.auth.models import User
from datetime import datetime
from aledb_experiment.permissions import set_primary_owner

WILD_TYPE_ALE_NUMBER = 0
WILD_TYPE_FLASK_NUMBER = 0
WILD_TYPE_ISOLATE_NUMBER = 1
WILD_TYPE_TECH_REP_NUMBER = 1
WILD_TYPE_USER_NAME = "BOP27"
BRESEQ_OUTPUT_REPORT_DIR = "output/"
BRESEQ_LOG_FILE = "log.txt"
METADATA_RELATIVE_PATH = 'metadata/'
REF_RELATIVE_PATH = 'ref/'

logger = logging.getLogger(__name__)


def remove_time_point(time_point_pk):
    """Delete one time point and every sample at it.

    Executed from Django ipython shell.
    """
    from aledb_common.rebuild_registry import request_rebuild

    time_point = aledb_experiment.models.TimePoint.objects.get(pk=time_point_pk)
    experiment_id = time_point.population.experiment_id
    time_point.delete()
    _delete_all_orphaned_mutations()
    # After the delete, not before: marking data stale that is about to change again would be
    # cleared by any rebuild that ran in between. Marked rather than rebuilt, because this is
    # a shell operation and the next reader of any of it will rebuild what it needs.
    request_rebuild(experiment_id, reason='time point removed')


def delete_experiments(experiment_ids):
    """Hard-delete experiments and everything below them.

    Executed from Django ipython shell, and by `./aledb delete` and `purge_deleted`.
    """
    from aledb_common.rebuild_registry import SITE_SCOPE, run_rebuilds

    for exp_id in experiment_ids:
        experiment_to_delete = aledb_experiment.models.Experiment.objects.get(pk=exp_id)
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
    # of something `aledb_dashboard` already declares. `force=True` because this deletion is
    # the change -- there is no earlier `request_rebuild` for it to answer, and running eagerly
    # is right for a command the operator is already waiting on.
    run_rebuilds(scope=SITE_SCOPE, force=True)


def _delete_all_orphaned_mutations():
    """Find the orphaned muations that don't have associated mutation calls.
    Retrieving mutation calls for each mutation to check if it is orphan is very expensive
    """
    orphans = aledb_sample.models.Mutation.objects.raw(
        'select * from aledb_sample_mutation m where not exists (select * from aledb_sample_mutationcall ob where ob.mutation_id = m.id)')
    for mutation in orphans:
        mutation.delete()


def delete_sample(experiment_pk, population_name, time_point_value, sample_name):
    """Delete the sample at a coordinate.

    Executed from Django ipython shell.

    It deleted an `Isolate` and took its replicates and their runs with it by cascade.
    There is one row now, so the cascade it relied on is the row itself -- and `sample_name`
    is the whole label (`1-2`), not the isolate half of a pair.
    """
    from aledb_common.rebuild_registry import request_rebuild

    for sample in aledb_sample.models.Sample.objects.filter(name=sample_name):
        if sample.time_point.population.experiment_id == experiment_pk and \
                sample.time_point.population.name == population_name and \
                sample.time_point.value == time_point_value:
            sample.delete()
            print("Successfully removed: ", population_name, time_point_value, sample_name)
    _delete_all_orphaned_mutations()
    # This marked nothing, where its sibling `remove_time_point` always has. Deleting a
    # sample changes both dashboard counts -- the sample count directly, the mutation counts
    # through the calls that go with it and the mutations left orphaned -- so without
    # this the Overview kept reporting a sample that was gone until some unrelated write
    # happened to mark the totals stale.
    request_rebuild(experiment_pk, reason='sample removed')


def upload_experiment(experiment_path):
    """Import one experiment directory: ``<path>/breseq/<sample>/`` plus ``<path>/metadata/``.

    The mutations go through ``breseq_folder``, the same importer a folder dropped on
    the Add page uses, so a CLI upload and a web upload produce identical rows --
    annotated against the experiment's reference, carrying their .gd record, with the
    alignment stored. This used to be a second implementation that read
    the sample's .gd plus the breseq HTML report and shared nothing with it.
    """
    parameters = _check_and_extract_parameters_from_metadata(
        os.path.join(experiment_path, "metadata"))
    if not parameters:
        return parameters

    person, experiment_name, project_name = parameters
    print(experiment_path, person, experiment_name, project_name)

    summary = breseq_folder.import_breseq_folders(
        os.path.join(experiment_path, "breseq"),
        project_name=project_name,
        experiment_name=experiment_name,
        person=person)

    for result in summary["files"]:
        if result["error"]:
            print("  %s: %s" % (result["file"], result["error"]))

    aledb_metadata.parser.parse_metadata_post_experiment_upload(
        os.path.join(experiment_path, "metadata"), summary["experiment_id"])
    return summary


def upload_collection(root_path):
    upload_experiments(find_experiment_paths(root_path))


def upload_experiments(exp_files_path_list):
    for each in exp_files_path_list:
        upload_experiment(each)


def find_experiment_paths(root_path):
    if not os.path.isdir(root_path):
        logger.info("invalid path:", root_path)
    paths = [x[0] for x in os.walk(root_path)]
    paths = set(paths)
    experiment_paths = []
    for path in paths:
        if path.endswith('/breseq'):
            root_path = path.replace('/breseq', '')
            if os.path.isdir(root_path+'/metadata'):
                experiment_paths.append(root_path)
    return experiment_paths


def _check_and_extract_parameters_from_metadata(metadata_path):
    if not os.path.isdir(metadata_path):
        logger.info("invalid metadata path")
        print("invalid path:", metadata_path)
        return False
    if not is_valid(metadata_path, SCHEMA_PATH):
        return False
    return aledb_metadata.parser.extract_experiment_parameters(metadata_path)


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

