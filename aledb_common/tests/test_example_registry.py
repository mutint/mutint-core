"""Example datasets: the registry, and the command that loads one.

Core ships no dataset of its own -- a component that needs particular data owns it -- so
these register a throwaway one, which also proves the registry works for a component core
knows nothing about.
"""

import os
import shutil
import tempfile
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from aledb_common import example_registry
from aledb_common.example_registry import (
    get_example_dataset, get_example_datasets, register_example_dataset,
)
from aledb_experiment import paths

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "aledb_import", "annotate", "tests", "fixtures")


class ExampleRegistryTestCase(TestCase):
    def setUp(self):
        # Module state the real apps populated at startup. addCleanup is LIFO, so this has
        # to be one callable: registering clear() and update() separately runs them in the
        # wrong order and leaves the registry empty for every later test.
        self._saved = dict(example_registry._example_datasets)

        def restore():
            example_registry._example_datasets.clear()
            example_registry._example_datasets.update(self._saved)

        self.addCleanup(restore)

    def test_a_registered_dataset_can_be_looked_up(self):
        register_example_dataset("thing-example", "/tmp/nowhere", description="A thing.")

        dataset = get_example_dataset("thing-example")
        self.assertEqual("/tmp/nowhere", dataset["directory"])
        self.assertEqual("A thing.", dataset["description"])

    def test_the_component_defaults_to_the_name_before_example(self):
        """So the listing says who owns it without every caller repeating itself."""
        register_example_dataset("aledb-thing-example", "/tmp/nowhere")

        self.assertEqual("aledb-thing", get_example_dataset("aledb-thing-example")["component"])

    def test_an_explicit_component_wins(self):
        register_example_dataset("odd-name", "/tmp/nowhere", component="aledb-thing")

        self.assertEqual("aledb-thing", get_example_dataset("odd-name")["component"])

    def test_a_duplicate_name_is_refused(self):
        """One component silently shadowing another's example would be worse than a
        startup error -- import_registry takes the same line."""
        register_example_dataset("thing-example", "/tmp/nowhere")

        with self.assertRaises(ValueError):
            register_example_dataset("thing-example", "/tmp/elsewhere")

    def test_an_unknown_name_is_none_rather_than_an_error(self):
        self.assertIsNone(get_example_dataset("no-such-example"))

    def test_datasets_are_listed_by_name(self):
        register_example_dataset("b-example", "/tmp/b")
        register_example_dataset("a-example", "/tmp/a")

        names = [d["name"] for d in get_example_datasets()]
        self.assertLess(names.index("a-example"), names.index("b-example"))

    def test_registering_does_not_read_the_directory(self):
        """A component may ship data conditionally and still register unconditionally."""
        register_example_dataset("absent-example", "/tmp/definitely/not/here")

        self.assertEqual("/tmp/definitely/not/here",
                         get_example_dataset("absent-example")["directory"])


class LoadExampleCommandTestCase(TestCase):
    """The command, against a dataset built here rather than any component's."""

    def setUp(self):
        # Module state the real apps populated at startup. addCleanup is LIFO, so this has
        # to be one callable: registering clear() and update() separately runs them in the
        # wrong order and leaves the registry empty for every later test.
        self._saved = dict(example_registry._example_datasets)

        def restore():
            example_registry._example_datasets.clear()
            example_registry._example_datasets.update(self._saved)

        self.addCleanup(restore)
        example_registry._example_datasets.clear()

        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        shutil.copy(os.path.join(FIXTURES, "synthetic.gbk"),
                    os.path.join(self.directory, "synthetic.gbk"))
        self._write_sample("1-100-1-1.gd", [(150, "T")])
        self._write_sample("1-200-1-1.gd", [(150, "T")])
        register_example_dataset("test-example", self.directory,
                                 description="Two flasks, one shared mutation.")

        self.admin = User.objects.create(username="admin", email="a@e.com",
                                         is_active=True, is_superuser=True)

    def _write_sample(self, filename, mutations):
        lines = ["#=GENOME_DIFF\t1.0",
                 "#=COMMAND\tbreseq -r synthetic.gbk -o %s r.fastq" % filename[:-3],
                 "#=REFSEQ\tsynthetic.gbk"]
        for i, (position, base) in enumerate(mutations, start=1):
            lines.append("SNP\t%d\t.\tSYN001\t%d\t%s\tfrequency=1" % (i, position, base))
        with open(os.path.join(self.directory, filename), "w") as handle:
            handle.write("\n".join(lines) + "\n")

    def _load(self, *args):
        out, err = StringIO(), StringIO()
        call_command("load_example", *args, stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    # --- listing --------------------------------------------------------------------

    def test_a_bare_call_lists_what_is_registered(self):
        output = self._load()

        self.assertIn("test-example", output)
        self.assertIn("Two flasks, one shared mutation.", output)

    def test_the_listing_needs_no_data(self):
        """Someone asking what data they can get is often on a fresh checkout, so the
        listing must not touch the database."""
        from django.db import connection

        User.objects.all().delete()
        with self.assertNumQueries(0, using=connection.alias):
            self._load()

    def test_an_unknown_name_says_what_is_available(self):
        with self.assertRaises(CommandError) as caught:
            self._load("no-such-example")

        self.assertIn("test-example", str(caught.exception))

    def test_a_registered_directory_that_is_missing_is_reported(self):
        register_example_dataset("gone-example", "/tmp/definitely/not/here")

        with self.assertRaises(CommandError) as caught:
            self._load("gone-example")

        self.assertIn("not there", str(caught.exception))

    # --- loading --------------------------------------------------------------------

    def test_loading_creates_a_project_the_owner_can_actually_see(self):
        """Project.objects.create alone leaves the owner without the guardian grant and
        unable to view what they own -- the trap in CLAUDE.md. The command must issue it."""
        from aledb_experiment.utils import get_user_projects

        self._load("test-example")

        self.assertIn("Examples", [p.name for p in get_user_projects(self.admin)])

    def test_loading_imports_the_data(self):
        from aledb_experiment.models import Experiment
        from aledb_sample.models import Mutation

        self._load("test-example")

        experiment = Experiment.objects.get(name="test-example")
        from aledb_sample.models import Sample
        self.assertEqual(
            {100, 200},
            set(Sample.objects.filter(**{paths.to_experiment(): experiment})
                .values_list(paths.to_time_point_value(), flat=True)))
        self.assertTrue(Mutation.objects.filter(experiment=experiment).exists())

    def test_prose_beside_the_data_is_not_treated_as_a_failed_import(self):
        """A dataset wants its README next to the files that produce its answer."""
        with open(os.path.join(self.directory, "README.md"), "w") as handle:
            handle.write("# notes\n")

        self._load("test-example")   # must not raise

    def test_a_second_load_is_refused_and_names_the_flag(self):
        self._load("test-example")

        with self.assertRaises(CommandError) as caught:
            self._load("test-example")

        self.assertIn("--replace", str(caught.exception))

    def test_replace_rebuilds_rather_than_duplicating(self):
        from aledb_experiment.models import Experiment, live

        self._load("test-example")
        first = Experiment.objects.get(name="test-example")

        self._load("test-example", "--replace")

        alive = live(Experiment.objects.filter(name="test-example"))
        self.assertEqual(1, alive.count(), "exactly one live copy")
        self.assertNotEqual(first.id, alive.first().id, "a fresh experiment")
        first.refresh_from_db()
        self.assertIsNotNone(first.deleted_at, "the old one is soft-deleted, not destroyed")

    def test_a_named_user_owns_it(self):
        from aledb_experiment.models import Experiment

        other = User.objects.create(username="someone", email="s@e.com", is_active=True)

        self._load("test-example", "--user", "someone")

        experiment = Experiment.objects.get(name="test-example")
        self.assertEqual(other.id, experiment.project.user_id)

    def test_an_unknown_user_is_refused(self):
        with self.assertRaises(CommandError) as caught:
            self._load("test-example", "--user", "nobody")

        self.assertIn("no such user", str(caught.exception))

    def test_with_no_superuser_it_says_so_rather_than_inventing_one(self):
        User.objects.all().delete()

        with self.assertRaises(CommandError) as caught:
            self._load("test-example")

        self.assertIn("superuser", str(caught.exception))
