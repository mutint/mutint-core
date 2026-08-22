import os
from datetime import datetime
from django.contrib.auth.models import User
from django.test import TestCase
from aledb_seq.models import Mutation
from aledb_import.upload import _is_missing_coverage_type
from aledb_import.upload import _get_mutation_freq
from aledb_import.upload import add_breseq_results
from aledb_import.gdparse.gdparse.gdparse import GDParser
from aledb_experiment.models import (AleExperiment, Instrument, Project,
                                     AleId, Flask, Isolate, TechnicalReplicate,
                                     Media, FreezerBox)


__author__ = 'Patrick Phaneuf'


class TestUpload(TestCase):

    def setUp(self):
        self.current_location = os.path.dirname(os.path.realpath(__file__))
        user = User.objects.create(username='testuser', first_name='Test', last_name='User', email='t@t.com', date_joined=datetime.now())
        project = Project.objects.create(name='test', user=user, date=datetime.now(), status='In progress', is_public=False)
        instrument = Instrument.objects.create(name='default')
        experiment = AleExperiment.objects.create(name='test_exp', instrument=instrument, person='Test', project=project)
        media = Media.objects.create(description='default', substrate='', temperature=37.0, volume=20.0, stirring_speed=200)
        fbox = FreezerBox.objects.create(name='box1', number=1)
        ale_id = AleId.objects.create(ale_experiment=experiment, ale_id=1)
        flask = Flask.objects.create(flask_number=1, ale_id=ale_id, media=media)
        isolate = Isolate.objects.create(flask=flask, isolate_number=1, is_population=False, reseq_reference='', reseq_date='', breseq_version='', freezer_box=fbox, person='Test')
        TechnicalReplicate.objects.create(tech_rep_number=1, isolate=isolate)

    def test_add_breseq_results_no_HTML_in_DB(self):
        breseq_output_dir_path = self.current_location + "/no_HTML_in_DB_test/"
        with open(breseq_output_dir_path + "annotated.gd") as output_genomic_diff_file:
            mutation_gd_parser = GDParser(file_handle=output_genomic_diff_file)
        add_breseq_results(1,
                           "Patrick",
                           breseq_output_dir_path,
                           mutation_gd_parser,
                           None,
                           "bop27_1_4")
        mut = Mutation.objects.get(position = 4181791)
        expected_annotation = 'C→A'
        self.assertEqual(expected_annotation, mut.sequence_change)
        expected_annotation = "P1100Q (CCG→CAG)"
        self.assertEqual(expected_annotation, mut.protein_change)

    def test_add_breseq_results_no_mut_annotation_dict(self):
        breseq_output_dir_path = self.current_location+"/0-0-1-1/output/"
        with open(breseq_output_dir_path+"annotated.gd") as output_genomic_diff_file:
            mutation_gd_parser = GDParser(file_handle=output_genomic_diff_file)
        add_breseq_results(1,
                           "Patrick",
                           breseq_output_dir_path,
                           mutation_gd_parser,
                           None,
                           "NC_000913_3")
        mut_qryset = Mutation.objects.all()
        mut_pos_list = [mut.position for mut in mut_qryset]
        self.assertEqual(len(mut_qryset), 4)
        self.assertTrue(257908 in mut_pos_list)
        self.assertTrue(2173363 in mut_pos_list)
        self.assertTrue(3560455 in mut_pos_list)
        self.assertTrue(4296381 in mut_pos_list)

    # TODO: change unit test to get use annotated.gd rather than output.gd.
    def test_add_breseq_results_gd_file_only(self):
        breseq_output_dir_path = self.current_location + "/1-0-1-1/output/"
        with open(breseq_output_dir_path + "output.gd") as output_genomic_diff_file:
            mutation_gd_parser = GDParser(file_handle=output_genomic_diff_file)

        add_breseq_results(1,
                           "Patrick",
                           breseq_output_dir_path,
                           mutation_gd_parser,
                           None,
                           "NC_000913_3")
        mut_qryset = Mutation.objects.all()
        mut_pos_list = [mut.position for mut in mut_qryset]
        self.assertEqual(len(mut_qryset), 4)
        self.assertTrue(257908 in mut_pos_list)
        self.assertTrue(2173363 in mut_pos_list)
        self.assertTrue(3560455 in mut_pos_list)
        self.assertTrue(4296381 in mut_pos_list)

    # TODO: change unit test to get use annotated.gd rather than output.gd.
    def test_add_breseq_results_ltee_gd_file(self):
        breseq_output_dir_path = self.current_location + "/2-0-1-1/output/"
        with open(breseq_output_dir_path + "output.gd") as output_genomic_diff_file:
            mutation_gd_parser = GDParser(file_handle=output_genomic_diff_file)

        add_breseq_results(1,
                           "Patrick",
                           breseq_output_dir_path,
                           mutation_gd_parser,
                           None,
                           "NC_000913_3")

        mut_qryset = Mutation.objects.all()
        mut_pos_list = [mut.position for mut in mut_qryset]
        self.assertEqual(len(mut_qryset), 4)
        self.assertTrue(880542 in mut_pos_list)
        self.assertTrue(1733559 in mut_pos_list)
        self.assertTrue(2103918 in mut_pos_list)
        self.assertTrue(4141016 in mut_pos_list)

    def test_get_mutation_freq_89(self):

        mutation_dict = {'seq_id': 'NC_000913',
                         'parent_ids': [71],
                         'frequency_output': 0.89,
                         'position': 231861,
                         'new_seq': 'T',
                         'type': 'SNP'}

        expected_freq = 0.89

        output_freq = _get_mutation_freq(mutation_dict)

        self.assertEqual(expected_freq, output_freq[0])

    def test_get_mutation_freq_missing(self):

        mutation_dict = {'seq_id': 'NC_000913',
                         'parent_ids': [71],
                         'position': 231861,
                         'new_seq': 'T',
                         'type': 'SNP'}

        expected_freq = 1.0

        output_freq = _get_mutation_freq(mutation_dict)

        self.assertEqual(expected_freq, output_freq[0])

    def test_is_missing_coverage_type_True(self):

        evidence = {'end_range': 330,
                    'left_inside_cov': 184,
                    'end': 3422590,
                    'left_outside_cov': 193,
                    'seq_id': 'NC_000913',
                    'right_inside_cov': 188,
                    'right_outside_cov': 192,
                    'start': 3421757,
                    'start_range': 499,
                    'type': 'MC'}

        is_missing_coverage = _is_missing_coverage_type(evidence)

        self.assertTrue(is_missing_coverage)

    def test_is_missing_coverage_type_False(self):

        evidence = {'end_range': 330,
                    'left_inside_cov': 184,
                    'end': 3422590,
                    'left_outside_cov': 193,
                    'seq_id': 'NC_000913',
                    'right_inside_cov': 188,
                    'right_outside_cov': 192,
                    'start': 3421757,
                    'start_range': 499,
                    'type': 'RA'}

        is_missing_coverage = _is_missing_coverage_type(evidence)

        self.assertFalse(is_missing_coverage)
