from django.test import TestCase
from django.contrib.auth.models import User
from aledb_experiment.models import Experiment,\
    Media,\
    TimePoint,\
    Population,\
    Project
from aledb_sample.models import Sample
from aledb_metadata.parser import parse_metadata_post_experiment_upload, _get_media_supplement_description
from datetime import datetime
import os
from aledb_metadata.xpmdvalidator.validate import is_valid
import csv

__author__ = 'Patrick Phaneuf'


class TestParser(TestCase):

    # There was a module constant here -- `ALE_EXP_PRIMARY_EXP = 1`, with the comment "I'm
    # assuming will always be 1 due to rebuild of DB with the unit testing." It was true on
    # SQLite and is false on PostgreSQL, whose sequences are not rewound by a TestCase's
    # rollback: ids keep climbing across tests, so experiment 1 stops existing after the
    # first one. The parser then targeted an experiment that was not there and changed
    # nothing, and every assertion about media counts failed. Each test uses the experiment
    # it created.

    def setUp(self):
        self.user = User.objects.create(username="Troy", password="test123",
                                        first_name="Troy", last_name="Sandberg", email="email@email.com",
                                        is_active=True, is_staff=True, date_joined=datetime.now())
        self.project = Project.objects.create(name="SSW Glu Ac", user = self.user)
        self.ale_exp = Experiment.objects.create()

    def test_metadata_change(self):
        media = Media.objects.create(carbon_source="nothing")
        ale_id = Population.objects.create(experiment=self.ale_exp,
                                      name=7)
        flask = TimePoint.objects.create(media=media,
                                     population=ale_id,
                                     value=90)
        # `7-90-0-1` is one sample now, labelled `0-1`: the file's I and R keys and their
        # values are unchanged, and `sample_label` is what joins them.
        Sample.objects.create(time_point=flask, name="0-1",
                                              is_clonal=True)

        path = os.path.dirname(os.path.realpath(__file__)) + "/"
        parse_metadata_post_experiment_upload(path + "test1/", self.ale_exp.pk)
        self.assertEqual(2, Media.objects.all().count())
        tech_rep_queryset = Sample.objects.all()
        self.assertEqual(1, tech_rep_queryset.count())
        test1_media = tech_rep_queryset[0].time_point.media.carbon_source
        self.assertEqual(test1_media, "Glucose(4)")

        # Tries to change the media of the same sample's flask.
        parse_metadata_post_experiment_upload(path + "test2/", self.ale_exp.pk)
        self.assertEqual(3, Media.objects.all().count())
        tech_rep_queryset = Sample.objects.all()
        self.assertEqual(1, len(tech_rep_queryset))
        test2_media = tech_rep_queryset[0].time_point.media.carbon_source
        self.assertEqual(test2_media, "Acetate(4)")

    def test_get_media_supplement_description(self):
        """It returns (description, components_dict) -- the caller at parser.py needs both.

        The assertion used to pass the whole tuple to assertRegexpMatches, and to expect a
        leading space, from the pre-2019 format where this joined every media field with
        spaces. It now returns the supplements alone, comma-joined.
        """
        path = os.path.dirname(os.path.realpath(__file__)) + "/"
        metadata_path = path + "test3/"
        for f in os.listdir(metadata_path):
            if f.endswith(".csv") or f.endswith(".CSV"):
                with open(os.path.join(metadata_path, f), 'rt') as csvfile:
                    metadata_dict = dict(csv.reader(csvfile, delimiter=','))
                description, components = _get_media_supplement_description(metadata_dict)
                self.assertRegex(description, r'^Pimelic acid\(21\)$')
                self.assertEqual(components["supplement"], "Pimelic acid(21)")

    def test_creating_media_with_metadata_upload(self):
        media = Media.objects.create(carbon_source="nothing")
        ale_id = Population.objects.create(experiment=self.ale_exp,
                                      name=7)
        flask = TimePoint.objects.create(media=media,
                                     population=ale_id,
                                     value=90)
        # Two replicates of one isolate are two samples in one flask -- `0-1` and `0-2`.
        # The file's I and R keys and their values are unchanged; `sample_label` joins them.
        Sample.objects.create(time_point=flask, name="0-1",
                                              is_clonal=True)
        Sample.objects.create(time_point=flask, name="0-2",
                                              is_clonal=True)
        # The metadata uploading should be creating 2 different types of media.
        path = os.path.dirname(os.path.realpath(__file__)) + "/"
        parse_metadata_post_experiment_upload(path + "test3/", self.ale_exp.pk)
        media_queryset = Media.objects.all()
        print("here")
        print(media_queryset[0].description)
        media_present_dict = {"nothing": False, "Glucose(4)": False, "Acetate(4)": False}
        for media in media_queryset:
            media_present_dict[media.carbon_source] = True
        for media_present_value in media_present_dict.values():
            self.assertEqual(True, media_present_value)

    def test_reuse_media_with_metadata_upload(self):
        media = Media.objects.create(carbon_source="nothing")
        ale_id = Population.objects.create(experiment=self.ale_exp,
                                      name=7)
        flask = TimePoint.objects.create(media=media,
                                     population=ale_id,
                                     value=90)
        # Two replicates of one isolate are two samples in one flask -- `0-1` and `0-2`.
        # The file's I and R keys and their values are unchanged; `sample_label` joins them.
        Sample.objects.create(time_point=flask, name="0-1",
                                              is_clonal=True)
        Sample.objects.create(time_point=flask, name="0-2",
                                              is_clonal=True)
        path = os.path.dirname(os.path.realpath(__file__)) + "/"
        parse_metadata_post_experiment_upload(path + "test_reuse_media_with_metadata_upload/", self.ale_exp.pk)
        media_queryset = Media.objects.all()
        media_present_dict = {"nothing": 0, "Glucose(4)": 0}
        for media in media_queryset:
            media_present_dict[media.carbon_source] += 1
        for media_name, media_count in media_present_dict.items():
            expected_count = 0
            if media_name == "nothing":
                expected_count = 1
            if media_name == "Glucose(4)":
                expected_count = 1
            self.assertEqual(expected_count, media_count)

    # Only way to change foreign key flask media is to change the media and save this change
    def test_metadata_two_tech_reps_change_media(self):
        media = Media.objects.create(carbon_source="nothing")
        ale_id = Population.objects.create(experiment=self.ale_exp,
                                      name=7)
        flask = TimePoint.objects.create(media=media,
                                     population=ale_id,
                                     value=90)
        # Two replicates of one isolate are two samples in one flask -- `0-1` and `0-2`.
        # The file's I and R keys and their values are unchanged; `sample_label` joins them.
        Sample.objects.create(time_point=flask, name="0-1",
                                              is_clonal=True)
        Sample.objects.create(time_point=flask, name="0-2",
                                              is_clonal=True)

        path = os.path.dirname(os.path.realpath(__file__)) + "/"

        # Will populate all tech_reps to have Glucose(4) carbon source, even though 7-90-0-1 only being set.
        parse_metadata_post_experiment_upload(path + "test1/", self.ale_exp.pk)
        tech_rep_queryset = Sample.objects.all()
        self.assertEqual(2, tech_rep_queryset.count())
        for tech_rep in tech_rep_queryset:
            self.assertEqual("Glucose(4)", tech_rep.time_point.media.carbon_source)

        parse_metadata_post_experiment_upload(path + "7-90-0-2_acetate/", self.ale_exp.pk)
        tech_rep_queryset = Sample.objects.all()
        self.assertEqual(2, tech_rep_queryset.count())
        for tech_rep in tech_rep_queryset:
            self.assertEqual("Acetate(4)", tech_rep.time_point.media.carbon_source)

    def test_xpmd_validator(self):
        path = os.path.dirname(os.path.realpath(__file__)) + "/"
        self.assertTrue(is_valid(path + "test1/", path + "../xpmdvalidator/Json_schema.json"))
        self.assertFalse(is_valid(path + "test_bad_metadata/", path + "../xpmdvalidator/Json_schema.json"))

