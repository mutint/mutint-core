"""
``./aledb upload`` and the shell helpers around it.

The upload itself is now a thin wrapper: it reads the experiment's metadata for
the project / experiment / person, hands the breseq folders to the same importer
a web drop uses, and then parses the metadata. So what is worth asserting here is
that a CLI upload really does produce what a web upload produces -- annotated
mutations, stored .gd, stored alignment -- rather than re-testing the importer.
"""

import io
import os
import shutil
import sys
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_experiment.models import AleExperiment, Project
from aledb_import import annotation
from aledb_import.ale_experiment import (
    delete_ale_experiments,
    find_experiment_paths,
    find_user,
    try_creating_project,
    upload_ale_collection,
)
from aledb_import.tests import breseq_fixture
from aledb_seq.models import (
    ExperimentReference,
    Mutation,
    ObservedMutation,
    ResequencingExperiment,
)

METADATA_FIXTURE = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "test_file_structure", "messy", "metadata")


class UploadCommandTestCase(TestCase):
    """A collection on disk, imported the way ``./aledb upload <path>`` does."""

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(
            username="pphaneuf", password="test123", first_name="Patrick",
            last_name="Phaneuf", email="email@email.com", is_active=True,
            is_staff=True, date_joined=datetime.now())

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.experiment_dir = os.path.join(self.root, "SSW Glu Ac")
        breseq_fixture.write_sample(
            os.path.join(self.experiment_dir, "breseq"), "1-10000-1-1")
        shutil.copytree(METADATA_FIXTURE, os.path.join(self.experiment_dir, "metadata"))

    def upload(self):
        upload_ale_collection(self.root)

    def test_it_finds_the_experiment_and_imports_it(self):
        self.upload()
        self.assertEqual(1, AleExperiment.objects.count())
        self.assertEqual(1, ResequencingExperiment.objects.count())
        self.assertEqual(2, Mutation.objects.count())

    def test_mutations_land_the_same_way_a_web_upload_leaves_them(self):
        self.upload()

        # Owned by the experiment, attributed to breseq, and carrying the .gd
        # record -- none of which the old CLI importer produced.
        experiment = AleExperiment.objects.get()
        self.assertEqual(2, Mutation.objects.filter(ale_experiment=experiment).count())
        self.assertEqual(2, ObservedMutation.objects.filter(source="breseq").count())
        self.assertFalse(Mutation.objects.filter(gd_data__isnull=True).exists())

    def test_the_reference_is_established_from_the_sample(self):
        self.upload()
        reference = ExperimentReference.objects.get()
        self.assertEqual(AleExperiment.objects.get(), reference.ale_experiment)
        self.assertTrue(reference.fasta_sha256)

    def test_mutations_are_annotated(self):
        self.upload()
        # breseq_fixture's reference carries one gene, thrA, spanning 50..150.
        mutation = Mutation.objects.get(position=100)
        self.assertEqual("thrA", mutation.gene_name)
        self.assertTrue(mutation.mutation_category)
        self.assertIsNotNone(mutation.annotation)

    def test_the_alignment_is_stored(self):
        self.upload()
        self.assertTrue(ResequencingExperiment.objects.get().bam_stored)

    def test_metadata_is_applied_after_the_import(self):
        self.upload()
        isolate = ResequencingExperiment.objects.get().tech_rep.isolate
        self.assertTrue(isolate.flask.ale_id.ale_experiment.project)

    def test_derived_data_is_available_after_the_import(self):
        """This asserted that `StaticData` had a row. Nothing is stored now, so what the
        import has to leave behind is an *answer* -- one computed from the rows the upload
        just wrote.

        It used to ask the needle plot for that answer, which core can no longer do: the plot
        is the aledb-needle component and is not installed here. The Overview's counts are the
        same question asked of something core owns."""
        from aledb_stats.util import get_experiment_summary

        self.upload()
        experiment = AleExperiment.objects.get()

        summary = get_experiment_summary(experiment.ale_id)
        self.assertTrue(sum(summary.mutation_type_counts.values()))

    def test_a_directory_with_no_metadata_is_skipped(self):
        shutil.rmtree(os.path.join(self.experiment_dir, "metadata"))
        self.upload()
        self.assertEqual(0, Mutation.objects.count())


class DeleteExperimentsTestCase(TestCase):

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(
            username="pphaneuf", password="test123", first_name="Patrick",
            last_name="Phaneuf", email="email@email.com", is_active=True,
            is_staff=True, date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        experiment_dir = os.path.join(self.root, "SSW Glu Ac")
        breseq_fixture.write_sample(os.path.join(experiment_dir, "breseq"), "1-10000-1-1")
        shutil.copytree(METADATA_FIXTURE, os.path.join(experiment_dir, "metadata"))
        upload_ale_collection(self.root)

    def test_deleting_an_experiment_takes_its_mutations_with_it(self):
        self.assertEqual(2, Mutation.objects.count())
        experiment = AleExperiment.objects.get()

        delete_ale_experiments([experiment.ale_id])

        self.assertEqual(0, AleExperiment.objects.count())
        self.assertEqual(0, ObservedMutation.objects.count())
        self.assertEqual(0, Mutation.objects.count())


class FindExperimentPathsTestCase(TestCase):

    def test_a_directory_needs_both_breseq_and_metadata(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)

        complete = os.path.join(root, "complete")
        os.makedirs(os.path.join(complete, "breseq"))
        os.makedirs(os.path.join(complete, "metadata"))
        os.makedirs(os.path.join(root, "no-metadata", "breseq"))
        os.makedirs(os.path.join(root, "no-breseq", "metadata"))

        self.assertEqual([complete], find_experiment_paths(root))


class ShellHelpersTestCase(TestCase):
    """find_user and try_creating_project, which only the CLI reaches."""

    def setUp(self):
        self.user = User.objects.create(
            username="pphaneuf", password="test123", first_name="Patrick",
            last_name="Phaneuf", email="email@email.com", is_active=True,
            is_staff=True, date_joined=datetime.now())
        Project.objects.create(name="test_project", user=self.user,
                               date=datetime.now(), status="In progress",
                               is_public=False)

    def test_find_user_by_username_and_by_first_name(self):
        self.assertEqual(self.user, find_user("pphaneuf"))
        self.assertEqual(self.user, find_user("Patrick"))

    def test_find_user_prompts_when_the_name_matches_nobody(self):
        # It reads stdin, which is why nothing web-facing may call it.
        original = sys.stdin
        sys.stdin = io.StringIO("who\npatrick")
        try:
            self.assertEqual(self.user, find_user("Krusty Krab"))
        finally:
            sys.stdin = original

    def test_find_user_disambiguates_between_two_matches(self):
        User.objects.create(username="krusty", password="test123",
                            first_name="Patrick", last_name="Faneuph",
                            email="email2@email2.com", is_active=True,
                            is_staff=True, date_joined=datetime.now())
        original = sys.stdin
        sys.stdin = io.StringIO("patrick\n-1\n0\nY\n0\nY")
        try:
            self.assertEqual(self.user, find_user("Krusty Krab"))
        finally:
            sys.stdin = original

    def test_try_creating_project(self):
        try_creating_project("Created Project", "Patrick Phaneuf", is_pub=False)

        created = Project.objects.get(name="Created Project")
        self.assertEqual("Created Project", created.name)
        self.assertEqual(self.user, created.user)
        self.assertEqual(2, Project.objects.count())
