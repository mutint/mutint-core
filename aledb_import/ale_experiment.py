import os
import aledb_experiment.models
import aledb_import.util
from aledb_import import breseq_folder
import aledb_seq.models
import aledb_seq.views.common
from aledb_import.gdparse.gdparse import gdparse
from aledb_common.util import _find_between
import aledb_metadata.parser
from aledb_dashboard.timeline_util import create_event
from aledb_dashboard.util import rebuild_dashboard_data
import logging
from aledb_metadata.xpmdvalidator.validate import SCHEMA_PATH, is_valid
from aledb_experiment.models import AleExperiment, Project
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


def integrate_metadata(ale_exp_path, ref_file_name, ale_exp_primary_key):
    """
    Executed from Django ipython shell
    """
    metadata_path = ale_exp_path + METADATA_RELATIVE_PATH
    aledb_metadata.parser.parse_metadata_post_experiment_upload(metadata_path, ale_exp_primary_key)

    ref_file_path = ale_exp_path + REF_RELATIVE_PATH + ref_file_name
    create_functional_annotations(ref_file_path, ale_exp_primary_key)


def remove_flask(flask_primary_key):
    """
    Executed from Django ipython shell
    """
    from aledb_common.rebuild_registry import request_rebuild

    flask_to_delete = aledb_experiment.models.Flask.objects.get(pk=flask_primary_key)
    experiment_id = flask_to_delete.ale_id.ale_experiment_id
    flask_to_delete.delete()
    _delete_all_orphaned_mutations()
    # After the delete, not before: marking data stale that is about to change again would be
    # cleared by any rebuild that ran in between. Marked rather than rebuilt, because this is
    # a shell operation and the next reader of any of it will rebuild what it needs.
    request_rebuild(experiment_id, reason='flask removed')


def delete_ale_experiments(ale_experiment_primary_key_list):
    """
    Executed from Django ipython shell.
    """
    for exp_id in ale_experiment_primary_key_list:
        ale_experiment_to_delete = aledb_experiment.models.AleExperiment.objects.get(pk=exp_id)
        print("Deleting Experiment #" + str(exp_id) + ":", ale_experiment_to_delete.name)
        message = "Experiment %s was deleted" % ale_experiment_to_delete.name
        ale_experiment_to_delete.delete()
        # The `StaticData` sweep that stood here is gone with the table. It was needed because
        # that row had no FK to the experiment -- only the convention that its pk *was* the
        # experiment's -- so a cascade could not reach it, and `get()` raised on an experiment
        # that had never been post-processed, leaving the delete half-finished. Nothing
        # derived from this experiment outlives it now, because nothing derived is stored.
        create_event(title="Experiment Deleted",
                     message=message,
                     icon='<i class="fa fa-times" aria-hidden="true"></i>',
                     color="danger")
        print(message)
    _delete_all_orphaned_mutations()
    print("deleted orphaned mutations")
    rebuild_dashboard_data()


def _delete_all_orphaned_mutations():
    """Find the orphaned muations that don't have associated observed mutations.
    Retrieving observed mutations for each mutation to check if it is orphan is very expensive
    """
    orphans = aledb_seq.models.Mutation.objects.raw(
        'select * from aledb_seq_mutation m where not exists (select * from aledb_seq_observedmutation ob where ob.mutation_id = m.id)')
    for mutation in orphans:
        mutation.delete()


def delete_isolate(ale_experiment_primary_key, ale_number, flask_number, isolate_number):
    isolate_to_delete = aledb_experiment.models.Isolate.objects.filter(isolate_number=isolate_number)
    for isolate in isolate_to_delete:
        if isolate.flask.ale_id.ale_experiment_id == ale_experiment_primary_key and \
                isolate.flask.ale_id.ale_id == ale_number and \
                isolate.flask.flask_number == flask_number:
            isolate.delete()
            print("Successfully removed: ", ale_number, flask_number, isolate_number)
    _delete_all_orphaned_mutations()


def upload_ale_experiment(experiment_path):
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


def upload_ale_collection(root_path):
    upload_ale_experiments(find_experiment_paths(root_path))


def upload_ale_experiments(exp_files_path_list):
    for each in exp_files_path_list:
        upload_ale_experiment(each)


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
            potential_user_list = (list(set(potential_user_list)))
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


def create_functional_annotations(genbank_path, ale_experiment_id):
    gene_dict = _parse_genbank(genbank_path)

    observed_mutations = aledb_seq.models.ObservedMutation.objects.filter(
        sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment=ale_experiment_id)

    for observed_mutation in observed_mutations:

        mutation = observed_mutation.mutation
        mutation_genes = ""
        if mutation.gene is not None:
            mutation_genes = mutation.gene.replace("[", "").replace("]", "").replace(u"\u2013", "/").replace("-",
                                                                                                             "/").split(
                "/")

        gene_info_start = {"product": "", "function": "", "go_process": "", "go_component": ""}

        gene_info = gene_info_start

        for gene in mutation_genes:

            gene_info = gene_info_start

            try:
                gene_info['function'] += "(" + gene_dict[gene]['function'] + ")"

                gene_info['product'] += "(" + gene_dict[gene]['product'] + ")"

                gene_info['go_component'] += "(" + gene_dict[gene]['go_component'] + ")"

                gene_info['go_process'] += "(" + gene_dict[gene]['go_process'] + ")"

            except Exception as e:
                # print(e, " Does not exits in ", os.path.basename(genbank_path))
                pass

        mutation.function = gene_info['function']

        mutation.product = gene_info['product']

        mutation.go_component = gene_info['go_component']

        mutation.go_process = gene_info['go_process']

        mutation.save()

    return


def _parse_genbank(genbank_path):
    gene_info_start = {"product": "", "function": "", "go_process": "", "go_component": ""}

    gene_dict = {}

    current_gene = ""

    with open(genbank_path, "rt") as genbank:

        record = False

        gene_info = gene_info_start

        for line in genbank:

            line = line.strip()

            if line.startswith("CDS ") or line.startswith("tRNA ") or line.startswith("rRNA"):

                record = True

            elif line.startswith("gene ") and current_gene != "":

                gene_dict[current_gene] = dict(gene_info)

                record = False

                gene_info = gene_info_start

            elif line.startswith("ORIGIN"):

                if record is True:
                    gene_dict[current_gene] = gene_info

                break

            else:

                if record is not False:

                    if line.startswith("/gene="):

                        current_gene = _find_between(line, "\"", "\"")

                    elif line.startswith("/product="):

                        gene_info['product'] = _find_between(line, "\"", "\"")

                    elif line.startswith("/function="):

                        gene_info['function'] = _find_between(line, "\"", "\"")

                    elif line.startswith("/GO_process="):

                        gene_info['go_process'] = _find_between(line, "\"", "\"")

                    elif line.startswith("/GO_component="):

                        gene_info['go_component'] = _find_between(line, "\"", "\"")

    return gene_dict


