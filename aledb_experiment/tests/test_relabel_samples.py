"""`./aledb relabel_samples` clears a description that is a coordinate in the old format.

The whole risk of the command is that it is a bulk delete of somebody's text, so what is
tested is mostly what it *leaves*: a description is only cleared when it is an exact,
anchored match for the retired shape, and never when it merely mentions one.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from aledb_experiment.models import Experiment, Population, Project
from aledb_sample.models import Sample


class RelabelSamplesTestCase(TestCase):

    def setUp(self):
        user = User.objects.create(username="owner", email="o@e.com")
        project = Project.objects.create(name="P", user=user)
        experiment = Experiment.objects.create(name="E", project=project)
        self.population = Population.objects.create(experiment=experiment, name="1")

    def sample(self, name, description):
        return Sample.objects.create(population=self.population, time_point=30000,
                                     name=name, description=description)

    def run_command(self, *args):
        out = StringIO()
        call_command("relabel_samples", *args, stdout=out)
        return out.getvalue()

    def test_it_clears_a_retired_coordinate(self):
        sample = self.sample("1-1", "A1 F30000 I1-1")

        self.run_command()

        sample.refresh_from_db()
        self.assertEqual("", sample.description)
        self.assertEqual("1 / 30000 / 1-1", sample.label,
                         "the computed coordinate takes over once the description is gone")

    def test_it_clears_the_four_part_form_too(self):
        """`A1 F30000 I1 R1`, from before the replicate folded into the label."""
        sample = self.sample("1-1", "A1 F30000 I1 R1")
        self.run_command()
        sample.refresh_from_db()
        self.assertEqual("", sample.description)

    def test_it_leaves_a_real_description_alone(self):
        for description in ("Ara-1_500gen_762B",
                            "colony picked from A1 F30000 I1 on day 30",
                            "A1 F30000 I1 -- contaminated?",
                            "the ancestor"):
            with self.subTest(description=description):
                sample = self.sample("x", description)
                self.run_command()
                sample.refresh_from_db()
                self.assertEqual(description, sample.description)
                sample.delete()

    def test_dry_run_changes_nothing(self):
        sample = self.sample("1-1", "A1 F30000 I1-1")

        output = self.run_command("--dry-run")

        sample.refresh_from_db()
        self.assertEqual("A1 F30000 I1-1", sample.description)
        self.assertIn("A1 F30000 I1-1", output)
        self.assertIn("1 / 30000 / 1-1", output, "it shows what the label would become")
        self.assertIn("1 would be cleared", output)

    def test_it_says_so_when_there_is_nothing_to_do(self):
        self.sample("1-1", "")
        self.assertIn("No sample carries a description in the retired format",
                      self.run_command())
