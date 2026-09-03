"""Each metadata key holds the column it names.

**This app had no tests at all, and that is how the bug it now pins survived.**
`aledb_metadata.views.get_reseq_info_list` built an eighteen-value positional tuple; this
app unpacked it *by index* against a hand-written list of names, in a different app. The two
agreed only by position and had drifted: the API published `AleId.description` as
``knockouts`` and `Isolate.library_prep` as ``taxonomy_id``, and the Metadata page rendered
the library prep under a column headed *Taxonomy ID*.

Nothing raised, because nothing was wrong in a way a program can notice -- the values were
strings and the keys were strings. The only thing that could have caught it is an assertion
that a named key holds the value from the column it is named after, which is this module.

Each fixture value below is distinct and says where it comes from, so a mix-up shows up as
the wrong sentence rather than as a subtly wrong value.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (AleExperiment, AleId, Flask, Isolate, Media, Project,
                                     TechnicalReplicate)
from aledb_interop_query.views import _serialize_metadata
from aledb_metadata.views import get_reseq_info_list
from aledb_seq.models import ResequencingExperiment

#: What each column is set to, and what the key naming it must therefore hold.
VALUES = {
    "strain": "from AleId.strain",
    "ale_description": "from AleId.description",
    "library_prep": "from Isolate.library_prep",
    "reseq_reference": "from Isolate.reseq_reference",
    "breseq_version": "from Isolate.breseq_version",
    "reseq_date": "from Isolate.reseq_date",
    "carbon_source": "from Media.carbon_source",
    "supplement": "from Media.supplement",
    "tech_rep_description": "from TechnicalReplicate.description",
}


class MetadataKeyTestCase(TestCase):

    def setUp(self):
        user = User.objects.create(username="tester")
        project = Project.objects.create(name="P", user=user)
        self.experiment = AleExperiment.objects.create(name="E", project=project)
        ale = AleId.objects.create(ale_experiment=self.experiment, ale_id="1",
                                   strain=VALUES["strain"],
                                   description=VALUES["ale_description"])
        media = Media.objects.create(description="M9",
                                     carbon_source=VALUES["carbon_source"],
                                     supplement=VALUES["supplement"])
        flask = Flask.objects.create(ale_id=ale, flask_number=1, media=media)
        isolate = Isolate.objects.create(flask=flask, isolate_number="1",
                                         is_population=False,
                                         library_prep=VALUES["library_prep"],
                                         reseq_reference=VALUES["reseq_reference"],
                                         breseq_version=VALUES["breseq_version"],
                                         reseq_date=VALUES["reseq_date"])
        tech_rep = TechnicalReplicate.objects.create(
            isolate=isolate, tech_rep_number=1,
            description=VALUES["tech_rep_description"])
        self.sample = ResequencingExperiment.objects.create(tech_rep=tech_rep,
                                                            sample_name="s1")

    def rows(self):
        return get_reseq_info_list(ResequencingExperiment.objects.filter(pk=self.sample.pk))

    def serialized(self):
        payload = _serialize_metadata([{
            "reseq_info_list": self.rows(),
            "ale_experiment_id": self.experiment.id,
            "ale_experiment_name": self.experiment.name,
            "ale_project_id": self.experiment.project.id,
            "ale_project_name": self.experiment.project.name,
            "multiple": False,
        }])
        return payload[0]["reseq_info_list"][0]

    # --- the builder ---------------------------------------------------------------

    def test_every_key_holds_the_column_it_names(self):
        row = self.rows()[0]

        for key, expected in VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(expected, row[key])

    def test_the_sample_itself_is_reachable(self):
        self.assertEqual(self.sample, self.rows()[0]["sample"])

    def test_a_population_says_so(self):
        isolate = self.sample.tech_rep.isolate
        isolate.is_population = True
        isolate.save()

        self.assertEqual("population", self.rows()[0]["clonal_or_population"])

    # --- what the API publishes ----------------------------------------------------

    def test_the_api_publishes_each_column_under_its_own_name(self):
        entry = self.serialized()

        for key, expected in VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(expected, entry[key])

    def test_the_two_mislabelled_keys_are_gone(self):
        """`knockouts` was the ALE's description and `taxonomy_id` was the library prep.
        Anyone reading either was reading another column's value."""
        entry = self.serialized()

        self.assertNotIn("knockouts", entry)
        self.assertNotIn("taxonomy_id", entry)

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
        self.assertEqual(VALUES["reseq_date"], entry["reseq_date"])
        self.assertEqual(self.experiment.name, entry["experiment_name"])
