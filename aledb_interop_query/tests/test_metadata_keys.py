"""Each metadata key holds the column it names.

**This app had no tests at all, and that is how the bug it now pins survived.**
`aledb_metadata.views.get_sample_info_list` built an eighteen-value positional tuple; this
app unpacked it *by index* against a hand-written list of names, in a different app. The two
agreed only by position and had drifted: the API published `Population.description` as
``knockouts`` and the sample's ``library_prep`` as ``taxonomy_id``, and the Metadata page rendered
the library prep under a column headed *Taxonomy ID*.

Nothing raised, because nothing was wrong in a way a program can notice -- the values were
strings and the keys were strings. The only thing that could have caught it is an assertion
that a named key holds the value from the column it is named after, which is this module.

Each fixture value below is distinct and says where it comes from, so a mix-up shows up as
the wrong sentence rather than as a subtly wrong value.

**Three more keys were wrong in the same way**, and the vocabulary rename is what made them
visible: `clonal_or_population` listed two possible answers of which one is retired,
`tech_rep_description` named a model the schema no longer has, and -- in the *mutation*
payload rather than this one -- `genotype` held a formatted frequency. None of the old names
is emitted alongside the new one, so a consumer reading one gets a `KeyError` rather than a
value that quietly means something else. `test_no_retired_key_is_still_emitted` is the guard.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import Experiment, Population, TimePoint, Media, Project
from aledb_interop_query.views import _serialize_metadata
from aledb_metadata.views import get_sample_info_list
from aledb_seq.models import Sample

#: What each column is set to, and what the key naming it must therefore hold.
VALUES = {
    "strain": "from Population.strain",
    "population_description": "from Population.description",
    "library_prep": "from the sample's library_prep",
    "reference_genome": "from the sample's reference_genome",
    "breseq_version": "from the sample's breseq_version",
    "sequencing_date": "from the sample's sequencing_date",
    "carbon_source": "from Media.carbon_source",
    "supplement": "from Media.supplement",
    "sample_medium_description": "from the sample's medium_description",
    "media_description": "from Media.description",
}


class MetadataKeyTestCase(TestCase):

    def setUp(self):
        user = User.objects.create(username="tester")
        project = Project.objects.create(name="P", user=user)
        self.experiment = Experiment.objects.create(name="E", project=project)
        ale = Population.objects.create(experiment=self.experiment, name="1",
                                   strain=VALUES["strain"],
                                   description=VALUES["population_description"])
        media = Media.objects.create(description=VALUES["media_description"],
                                     carbon_source=VALUES["carbon_source"],
                                     supplement=VALUES["supplement"])
        flask = TimePoint.objects.create(population=ale, value=1, media=media)
        self.sample = Sample.objects.create(
            time_point=flask, name="1", is_clonal=True,
            library_prep=VALUES["library_prep"],
            reference_genome=VALUES["reference_genome"],
            breseq_version=VALUES["breseq_version"],
            sequencing_date=VALUES["sequencing_date"],
            medium_description=VALUES["sample_medium_description"],
            source_name="s1")

    def rows(self):
        return get_sample_info_list(Sample.objects.filter(pk=self.sample.pk))

    def serialized(self):
        payload = _serialize_metadata([{
            "sample_info_list": self.rows(),
            "experiment_id": self.experiment.id,
            "experiment_name": self.experiment.name,
            "project_id": self.experiment.project.id,
            "project_name": self.experiment.project.name,
            "multiple": False,
        }])
        return payload[0]["sample_info_list"][0]

    # --- the builder ---------------------------------------------------------------

    def test_every_key_holds_the_column_it_names(self):
        row = self.rows()[0]

        for key, expected in VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(expected, row[key])

    def test_the_sample_itself_is_reachable(self):
        self.assertEqual(self.sample, self.rows()[0]["sample"])

    def test_a_mixed_sample_says_so(self):
        self.sample.is_clonal = False
        self.sample.save()

        # `sample_type`, holding one of the two words `?sample_type=` accepts. It was
        # `clonal_or_population`, which named its two possible answers -- and one of them
        # stopped being one of them.
        self.assertEqual("mixed", self.rows()[0]["sample_type"])
        self.sample.is_clonal = True
        self.sample.save()
        self.assertEqual("clonal", self.rows()[0]["sample_type"])

    # --- what the API publishes ----------------------------------------------------

    def test_the_api_publishes_each_column_under_its_own_name(self):
        entry = self.serialized()

        for key, expected in VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(expected, entry[key])

    def test_the_two_mislabelled_keys_are_gone(self):
        """`knockouts` was the population's description and `taxonomy_id` was the library
        prep. Anyone reading either was reading another column's value."""
        entry = self.serialized()

        self.assertNotIn("knockouts", entry)
        self.assertNotIn("taxonomy_id", entry)

    def test_no_retired_key_is_still_emitted(self):
        """No key is published under both its old and its new name.

        Emitting both would have let a consumer keep reading a name that no longer describes
        what it holds -- which is the exact failure `knockouts` and `taxonomy_id` were. A
        `KeyError` on the next request is the honest answer.
        """
        entry = self.serialized()

        for retired in ("clonal_or_population", "tech_rep_description", "reseq_reference",
                        "ale_description", "reseq_date"):
            with self.subTest(key=retired):
                self.assertNotIn(retired, entry)

    def test_the_two_medium_columns_stay_apart(self):
        """The sample's own note and the medium's description are different columns and
        were a letter apart in the payload if both were called "medium"."""
        entry = self.serialized()

        self.assertEqual(VALUES["sample_medium_description"],
                         entry["sample_medium_description"])
        self.assertEqual(VALUES["media_description"], entry["media_description"])
        self.assertNotEqual(entry["sample_medium_description"], entry["media_description"])

    def test_the_misspelled_key_is_gone(self):
        """The column is `phosphorus_source`; the API said `phosphorous_source`."""
        entry = self.serialized()

        self.assertNotIn("phosphorous_source", entry)
        self.assertIn("phosphorus_source", entry)

    def test_the_three_dropped_values_are_published(self):
        """The builder produced these on every request and the serializer read none of
        them, because the tuple simply ran out of indices it knew about."""
        entry = self.serialized()

        self.assertEqual(VALUES["breseq_version"], entry["breseq_version"])
        self.assertEqual(VALUES["sequencing_date"], entry["sequencing_date"])
        self.assertEqual(self.experiment.name, entry["experiment_name"])
