"""The keys `_sample_info_list` publishes, and where their values come from.

This payload has an **external consumer**, so its key set is a contract the suite cannot see
being broken. Five of these keys now name fields that live in `Sample.supplemental_data`
rather than in columns, and two of them are spelled differently in there -- `breseq_version`
is `breseq.version`, `sequencing_date` is `sequencing.date`. A dict `.get` on a key that does
not exist answers `""` rather than raising, which is exactly the shape of a silent rename:
the payload keeps its key and quietly stops carrying anything.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import Experiment, Population, Project
from aledb_interop_query.views import _sample_info_list
from aledb_sample.models import Sample

PUBLISHED_KEYS = {
    "label", "sample_type", "sample_medium_description", "strain",
    "population_description", "library_prep", "reference_genome",
    "breseq_version", "sequencing_date", "experiment_name",
}


class SampleInfoTestCase(TestCase):
    def setUp(self):
        user = User.objects.create(username="owner", email="o@e.com")
        project = Project.objects.create(name="P", user=user)
        experiment = Experiment.objects.create(name="E", project=project)
        self.population = Population.objects.create(
            experiment=experiment, name=1, strain="MG1655", description="glucose")

    def _publish(self, **kwargs):
        sample = Sample.objects.create(
            population=self.population, time_point=1000, name="1-1", is_clonal=True,
            source_name="1-1000-1-1", **kwargs)
        rows = _sample_info_list(Sample.objects.filter(pk=sample.pk))
        self.assertEqual(1, len(rows))
        return rows[0]

    def test_the_published_keys_are_exactly_these(self):
        self.assertEqual(PUBLISHED_KEYS, set(self._publish().keys()))

    def test_a_sample_with_nothing_recorded_publishes_every_key_empty(self):
        """The stated requirement for the move: a sample whose groups were never written
        has no key at all in the JSON, and must still publish rather than raise."""
        row = self._publish()

        for key in ("sample_medium_description", "library_prep", "reference_genome",
                    "breseq_version", "sequencing_date"):
            self.assertEqual("", row[key], key)

    def test_each_value_comes_from_its_group(self):
        """The guard against a shortened name being read under the wrong spelling."""
        row = self._publish(supplemental_data={Sample.COMPONENT: {
            Sample.BRESEQ: {"version": "0.38.1"},
            Sample.SEQUENCING: {"date": "2024-01-02", "library_prep": "Nextera",
                                "reference_genome": "REL606"},
            Sample.CURATION: {"medium_description": "M9 + 2 g/L glucose"},
        }})

        self.assertEqual("0.38.1", row["breseq_version"])
        self.assertEqual("2024-01-02", row["sequencing_date"])
        self.assertEqual("Nextera", row["library_prep"])
        self.assertEqual("REL606", row["reference_genome"])
        self.assertEqual("M9 + 2 g/L glucose", row["sample_medium_description"])
