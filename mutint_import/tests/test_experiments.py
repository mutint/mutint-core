"""
``./mutint import`` and the shell helpers around it.

The command is a thin wrapper over `import_registry.run_import` -- the same funnel the Add
Data page posts into -- so what is worth asserting here is that a shell import really does
produce what a web drop produces: annotated mutations owned by the experiment, the .gd
record kept, the reference established and the alignment stored. Not the importer again.

It was `./mutint upload`, and it read the project, experiment and owner out of
`<exp>/metadata/*.csv`. That directory and the app that parsed it are gone; identity is
options now, which is what the web always did.
"""

import io
import os
import shutil
import sys
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common.rebuild_registry import (
    SITE_SCOPE, is_stale, request_rebuild, run_rebuilds,
)
from mutint_dashboard.models import InstallationCounts
from mutint_dashboard.util import counts
from mutint_experiment.models import Experiment, Project
from mutint_import import annotation
from mutint_import.experiments import (
    delete_experiments,
    delete_sample,
    find_user,
    import_paths,
    remove_time_point,
    resolve_experiment,
    try_creating_project,
)
from mutint_import.tests import breseq_fixture
from mutint_sample.models import (
    ReferenceSequences,
    Mutation,
    MutationCall,
    Sample,
)

class ImportCommandTestCase(TestCase):
    """A breseq tree on disk, imported the way ``./mutint import <path>`` does."""

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(
            username="pphaneuf", password="test123", first_name="Patrick",
            last_name="Phaneuf", email="email@email.com", is_active=True,
            is_staff=True, date_joined=datetime.now())

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        breseq_fixture.write_sample(self.root, "1-10000-1-1")

    def upload(self):
        experiment, user = resolve_experiment(
            project_name="SSW Glu Ac", experiment_name="SSW Glu Ac", owner="pphaneuf")
        import_paths([self.root], experiment, user)

    def test_it_creates_the_experiment_and_imports_it(self):
        self.upload()
        self.assertEqual(1, Experiment.objects.count())
        self.assertEqual(1, Sample.objects.count())
        self.assertEqual(2, Mutation.objects.count())

    def test_mutations_land_the_same_way_a_web_upload_leaves_them(self):
        self.upload()

        # Owned by the experiment, attributed to breseq, and carrying the .gd
        # record -- none of which the old CLI importer produced.
        experiment = Experiment.objects.get()
        self.assertEqual(2, Mutation.objects.filter(experiment=experiment).count())
        self.assertEqual(2, MutationCall.objects.filter(source="breseq").count())
        self.assertFalse(Mutation.objects.filter(supplemental_data__isnull=True).exists())

    def test_the_reference_is_established_from_the_sample(self):
        self.upload()
        reference = ReferenceSequences.objects.get()
        self.assertEqual(Experiment.objects.get(), reference.experiment)
        self.assertTrue(reference.fasta_sha256)

    def test_mutations_are_annotated(self):
        self.upload()
        # breseq_fixture's reference carries one gene, thrA, spanning 50..150.
        mutation = Mutation.objects.get(start_position=100)
        self.assertEqual("thrA", mutation.gene_name)
        self.assertTrue(mutation.mutation_category)
        self.assertIsNotNone(mutation.annotation)

    def test_the_alignment_is_stored(self):
        self.upload()
        self.assertTrue(Sample.objects.get().bam_stored)

    def test_the_sample_lands_under_the_named_project_and_experiment(self):
        self.upload()
        sample = Sample.objects.get()

        self.assertEqual("SSW Glu Ac", sample.population.experiment.name)
        self.assertEqual("SSW Glu Ac", sample.population.experiment.project.name)
        self.assertEqual(self.user, sample.population.experiment.project.user)

    def test_derived_data_is_available_after_the_import(self):
        """This asserted that `StaticData` had a row. Nothing is stored now, so what the
        import has to leave behind is an *answer* -- one computed from the rows the upload
        just wrote.

        It used to ask the needle plot for that answer, which core can no longer do: the plot
        is the mutint-needle component and is not installed here. The Overview's counts are the
        same question asked of something core owns."""
        from mutint_stats.util import get_experiment_summary

        self.upload()
        experiment = Experiment.objects.get()

        summary = get_experiment_summary(experiment.id)
        self.assertTrue(sum(summary.mutation_type_counts.values()))

    def test_importing_twice_adds_to_the_same_experiment(self):
        """`--experiment-id` is the web's form and the one to prefer, because the name-based
        path reaches only one experiment of a given name in a given project."""
        self.upload()
        experiment = Experiment.objects.get()

        again, user = resolve_experiment(experiment_id=experiment.id)

        self.assertEqual(experiment, again)
        # No `--owner`: with an experiment named by pk it comes from the project.
        self.assertEqual(self.user, user)

    def test_naming_a_project_without_an_owner_is_refused(self):
        """A new project needs an owner, and `Project.user` is NOT NULL. The old path took
        one out of the metadata file; there is nowhere else to get it."""
        with self.assertRaises(ValueError):
            resolve_experiment(project_name="P", experiment_name="E")

    def test_neither_form_of_target_is_refused(self):
        with self.assertRaises(ValueError):
            resolve_experiment(owner="pphaneuf")


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
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        breseq_fixture.write_sample(self.root, "1-10000-1-1")
        experiment, user = resolve_experiment(
            project_name="SSW Glu Ac", experiment_name="SSW Glu Ac", owner="pphaneuf")
        import_paths([self.root], experiment, user)

    def test_deleting_an_experiment_takes_its_mutations_with_it(self):
        self.assertEqual(2, Mutation.objects.count())
        experiment = Experiment.objects.get()

        delete_experiments([experiment.id])

        self.assertEqual(0, Experiment.objects.count())
        self.assertEqual(0, MutationCall.objects.count())
        self.assertEqual(0, Mutation.objects.count())

    def test_the_dashboard_totals_are_left_settled_and_not_merely_rewritten(self):
        """The delete goes through the registry, so the flag clears as well as the numbers.

        It called `rebuild_dashboard_data()` directly, which writes the same totals and
        settles nothing -- `stale_since` stayed set, so the next reader of /dashboard paid
        for the identical recomputation over again. Asserting the counts alone cannot see
        that: both spellings write the right numbers, and only one of them clears the mark.

        The `request_rebuild` is what makes the difference visible, and it is not contrived
        -- it is the ordinary state of these rows. An import leaves them fresh, and anything
        that has happened since (an edit, a `delete_sample`) marks them again, so a delete
        arriving at a stale dashboard is the common case rather than the corner.
        """
        request_rebuild(reason='test setup')
        self.assertEqual(1, counts(InstallationCounts.INVENTORY)["sample"])

        delete_experiments([Experiment.objects.get().id])

        self.assertEqual(0, counts(InstallationCounts.INVENTORY)["sample"])
        self.assertEqual(0, counts(InstallationCounts.MUTATION_CALLS)["total"])
        for name in ("sample_counts", "mutation_counts"):
            self.assertFalse(is_stale(name), "%s was left marked stale" % name)


class DeleteSampleTestCase(TestCase):
    """`delete_sample` and `remove_time_point`, the two shell deletes below an experiment.

    Both change what the dashboard counts, so both have to mark it. Only the second did.
    """

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        # The metadata fixture names this user, and `find_user` *prompts on stdin* for one
        # it cannot resolve -- which under the runner hangs rather than failing.
        User.objects.create(
            username="pphaneuf", password="test123", first_name="Patrick",
            last_name="Phaneuf", email="email@email.com", is_active=True,
            is_staff=True, date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        breseq_fixture.write_sample(self.root, "1-10000-1-1")
        breseq_fixture.write_sample(self.root, "1-20000-1-1")
        experiment, user = resolve_experiment(
            project_name="SSW Glu Ac", experiment_name="SSW Glu Ac", owner="pphaneuf")
        import_paths([self.root], experiment, user)
        self.experiment = Experiment.objects.get()
        # Deliberately after the upload, which marks everything stale itself: what these tests
        # assert is that the *delete* marks it, and a row already stale would say nothing.
        run_rebuilds(scope=SITE_SCOPE, force=True)
        self.assertFalse(is_stale("sample_counts"))

    def test_deleting_a_sample_marks_the_derived_data_stale(self):
        delete_sample(self.experiment.id, "1", 10000, "1-1")

        self.assertEqual(["1-1"], [s.name for s in Sample.objects.all()])
        self.assertEqual([20000], [s.time_point for s in Sample.objects.all()])
        self.assertTrue(is_stale("sample_counts"))

    def test_removing_a_time_point_marks_the_derived_data_stale(self):
        sample = Sample.objects.get(time_point=10000)

        remove_time_point(sample.population_id, sample.time_point)

        self.assertEqual([20000], [s.time_point for s in Sample.objects.all()])
        self.assertTrue(is_stale("sample_counts"))

    def test_a_coordinate_naming_no_sample_deletes_nothing(self):
        delete_sample(self.experiment.id, "1", 30000, "1-1")

        self.assertEqual(2, Sample.objects.count())


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
