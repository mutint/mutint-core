import os
import re
import csv
import json

from aledb_import.sample_names import sample_label
from aledb_sample.models import Sample
from aledb_experiment.models import Media
from aledb_metadata.xpmdvalidator.validate import SCHEMA_PATH, is_valid
from aledb_experiment import paths

__author__ = 'Denny Gosting, Patrick Phaneuf, Muyao'

DEFAULT_MEDIA_DESCRIPTION = "M9"
DEFAULT_TEMPERATURE = 37

PROJECT = "project"
OWNER = "owner"
EXPERIMENT_NAME = "experiment/subproject"

STRAIN = "taxonomy id"
STRAIN_DESCRIPTION = "starting strain"
LIBRARY_PREP_KIT_MANUFACTURER = "seqencing library prep kit manufacturer"
LIBRARY_PREP_KIT_CYCLES = "sequencing library prep kit cycles"
ALE_NUMBER = "A"
FLASK_NUMBER = "F"
ISOLATE_NUMBER = "I"
TECH_REP_NUMBER = "R"
EXPERIMENT_DETAILS = "medium description"

MEDIA_BASE_DESCRIPTION = "medium derived from"

ENVIRONMENTAL_CONDITIONS = "environmental conditions"
MEDIA_TEMPERATURE = "temp"
MEDIA_COMPONENTS = "media components"
MEDIA_CARBON_SOURCE = "carbon source"
MEDIA_NITROGEN_SOURCE = "nitrogen source"
MEDIA_PHOSPHORUS_SOURCE = "phosphorus source"
MEDIA_SULFUR_SOURCE = "sulfur source"
MEDIA_CALCIUM_SOURCE = "calcium source"
MEDIA_ELECTRON_ACCEPTOR = "electron acceptor"
MEDIA_SUPPLEMENT = "supplement"
MEDIA_DESCRIPTOR_LIST = [MEDIA_CARBON_SOURCE,
                         MEDIA_NITROGEN_SOURCE,
                         MEDIA_PHOSPHORUS_SOURCE,
                         MEDIA_SULFUR_SOURCE,
                         MEDIA_ELECTRON_ACCEPTOR,
                         MEDIA_CALCIUM_SOURCE]


def multiple_replace(text):
    word_dict = {
        ' ': "_"
    }
    for key in word_dict:
        text = text.replace(key, word_dict[key])
    text = re.sub(r'[^A-Za-z0-9_-]+', '', text)
    return text


def extract_experiment_parameters(metadata_path):
    # need to ensure all the csv files give the same parameters
    project = "N/A"
    creator = "N/A"
    experiment = "N/A"
    files = []

    for f in os.listdir(metadata_path):
        if f.endswith(".csv") or f.endswith(".CSV"):
            files.append(f)
            with open(os.path.join(metadata_path, f), 'rt') as csvfile:
                csv_reader = csv.reader(csvfile)

                experiment_details = []
                for row in csv_reader:
                    if row[0] == PROJECT:
                        if len(row[1]) > 0:
                            descr = row[1]
                            if project == "N/A":
                                project = descr
                            elif project != descr:
                                print("Project Mismatch: Please ensure all project fields within one experiment share the same value", f)
                                return False
                        else:
                            print("Field Missing: project in " + str(os.path.join(metadata_path, f)))
                    if row[0] == OWNER:
                        if len(row[1]) > 0:
                            descr = row[1]
                            if creator == "N/A":
                                creator = descr
                            elif creator != descr:
                                print("Creator Mismatch: Please ensure all creator fields within one experiment share the same value", f)
                                return False
                    elif row[0] == EXPERIMENT_NAME:
                        if len(row[1]) > 0:
                            label = row[1].split(",")
                            label = '-'.join(label)
                            experiment_details.append(label)
                curr_experiment = "-".join(experiment_details)
                curr_experiment = multiple_replace(curr_experiment)
                if experiment == "N/A":
                    experiment = curr_experiment
                elif experiment != curr_experiment:
                    print("Experiment Mismatch: Please ensure all experiment specific parameters share the same values", f)
                    return False
    if len(files) == 0:
        print("No Metadata: Please ensure there are csv metadata files in the metadata folder")
        return False
    return creator, experiment, project


def _get_media_supplement_description(metadata_dict):
    media_supplement_description = ''

    media_components_dict = json.loads(metadata_dict[MEDIA_COMPONENTS])
    if MEDIA_SUPPLEMENT not in media_components_dict.keys():
        media_components_dict[MEDIA_SUPPLEMENT] = ''

    for media_descriptor in media_components_dict.keys():
        if media_descriptor not in MEDIA_DESCRIPTOR_LIST and media_components_dict[media_descriptor] != '':
            if media_supplement_description != "":
                media_supplement_description += ', '
            if media_components_dict[media_descriptor] != "none":
                media_supplement_description += media_components_dict[media_descriptor]

    return media_supplement_description, media_components_dict


def parse_metadata_post_experiment_upload(metadata_path, ale_experiment_primary_key):
    if not os.path.isdir(metadata_path):
        return

    if not is_valid(metadata_path, SCHEMA_PATH):
        print ("Invalid metadata!", metadata_path)

    for f in os.listdir(metadata_path):
        if f.endswith(".csv") or f.endswith(".CSV"):

            with open(os.path.join(metadata_path, f), 'rt') as csvfile:
                metadata_dict = dict(csv.reader(csvfile, delimiter=','))
            metadata_keys = metadata_dict.keys()
            try:
                # The file still carries I and R separately -- its keys and their values are
                # the on-disk format and do not change -- but they name one sample now, so
                # they are joined the same way an imported filename is. See
                # `aledb_import.sample_names.sample_label`.
                tech_rep = Sample.objects.get(
                    **{paths.to_sample_label(): sample_label(
                           metadata_dict[ISOLATE_NUMBER], metadata_dict[TECH_REP_NUMBER]),
                       paths.to_time_point_value(): metadata_dict[FLASK_NUMBER],
                       paths.to_population_label(): metadata_dict[ALE_NUMBER],
                       paths.to_experiment_id(): ale_experiment_primary_key})
            except Exception as e:
                print("Error for " + metadata_dict[ALE_NUMBER] + "-" + metadata_dict[FLASK_NUMBER] + "-" + metadata_dict[ISOLATE_NUMBER] + '-' + metadata_dict[TECH_REP_NUMBER] + ": ", e)
                continue

            ale_id_description = ""
            if STRAIN_DESCRIPTION in metadata_keys:
                ale_id_description = metadata_dict[STRAIN_DESCRIPTION]

            strain = ""
            if STRAIN in metadata_keys:
                strain = metadata_dict[STRAIN]

            media_description = ""
            if MEDIA_BASE_DESCRIPTION in metadata_keys:
                media_description = metadata_dict[MEDIA_BASE_DESCRIPTION]

            library_prep = ""
            if LIBRARY_PREP_KIT_MANUFACTURER in metadata_keys:
                library_prep = metadata_dict[LIBRARY_PREP_KIT_MANUFACTURER]

            if MEDIA_CARBON_SOURCE in metadata_keys:
                carbon_source = metadata_dict[MEDIA_CARBON_SOURCE]


            if LIBRARY_PREP_KIT_CYCLES in metadata_keys:
                if library_prep != "":
                    library_prep += "/ "
                library_prep += metadata_dict[LIBRARY_PREP_KIT_CYCLES]

            media_temperature = DEFAULT_TEMPERATURE
            environmental_conditions_dict = json.loads(metadata_dict[ENVIRONMENTAL_CONDITIONS])
            if MEDIA_TEMPERATURE in environmental_conditions_dict.keys() and environmental_conditions_dict[MEDIA_TEMPERATURE] != "":
                media_temperature = environmental_conditions_dict[MEDIA_TEMPERATURE]

            experiment_details = ""
            if EXPERIMENT_DETAILS in metadata_dict.keys():
                experiment_details = metadata_dict[EXPERIMENT_DETAILS]

            media_supplement_description, media_components_dict = _get_media_supplement_description(metadata_dict)

            nitrogen_source = phosphorus_source = sulfur_source = calcium_source = "None"

            if MEDIA_NITROGEN_SOURCE in media_components_dict:
                nitrogen_source = media_components_dict[MEDIA_NITROGEN_SOURCE]
            if MEDIA_PHOSPHORUS_SOURCE in media_components_dict:
                phosphorus_source = media_components_dict[MEDIA_PHOSPHORUS_SOURCE]
            if MEDIA_SULFUR_SOURCE in media_components_dict:
                sulfur_source = media_components_dict[MEDIA_SULFUR_SOURCE]
            if MEDIA_CALCIUM_SOURCE in media_components_dict:
                calcium_source = media_components_dict[MEDIA_CALCIUM_SOURCE]

            supplement_keys = list(set(media_components_dict) - set(MEDIA_DESCRIPTOR_LIST))
            supplement_values = []
            for supplement_key in supplement_keys:
                supplement_values.append(media_components_dict[supplement_key])
            supplement = ",".join(supplement_values)

            population = tech_rep.time_point.population
            population.description = ale_id_description
            population.strain = strain
            if population.species is None:
                population.species = ""
            population.save()

            media, created = Media.objects.get_or_create(description=media_description,
                                                         temperature=media_temperature,
                                                         carbon_source=carbon_source,
                                                         nitrogen_source=nitrogen_source,
                                                         phosphorus_source=phosphorus_source,
                                                         sulfur_source=sulfur_source,
                                                         calcium_source=calcium_source,
                                                         supplement=supplement
                                                         )

            for component in MEDIA_DESCRIPTOR_LIST:
                if component in media_components_dict.keys():
                    remove_spaces = component.replace(' ', '_')
                    setattr(media, remove_spaces, media_components_dict[component])

            media.save()

            flask = tech_rep.time_point
            flask.media = media
            flask.save()

            tech_rep.library_prep = library_prep
            tech_rep.medium_description = experiment_details
            tech_rep.save(update_fields=["library_prep", "medium_description"])

            # There was a `Project.objects.get_or_create(name=metadata_dict[PROJECT])` here
            # whose result was never used. Its only effect was a side effect: creating a
            # Project with no `user` -- a column that is NOT NULL, so the create branch
            # raised IntegrityError -- and with no access grant, so nobody could have seen
            # it anyway. The experiment's project is already set by the import that called
            # this; metadata does not get to invent another one.


