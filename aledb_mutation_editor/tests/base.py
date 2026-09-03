"""A small experiment with samples and mutations, built without going through an import.

`gd_import` needs a reference genome, fixture files and a store directory to produce a sample,
which is the right fixture for testing the importer and far too much apparatus for testing what
happens when a row is deleted. The chain below is what `gd_import._get_or_create_chain` builds,
made directly: AleId -> Flask -> Isolate -> TechnicalReplicate -> ResequencingExperiment.

The project is created through the view rather than with `Project.objects.create`, because that
is what issues the ProjectAccess row `can_add_experiment_filter` consults -- the same reason
`aledb_seq.tests.test_table_actions` does it.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_import.gd_import import prepare_experiment_by_id
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment
from aledb_experiment import paths


class EditorTestCase(TestCase):
    """One experiment, two samples, three mutations each."""

    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        self.context = prepare_experiment_by_id(self.experiment.id)
        self.ale = AleId.objects.create(ale_experiment=self.experiment, ale_id=1)

        self.sample_a = self.make_sample(flask_number=1)
        self.sample_b = self.make_sample(flask_number=2)

        self.mut_1 = self.make_mutation(position=100, sequence_change="A>T")
        self.mut_2 = self.make_mutation(position=200, sequence_change="C>G")
        self.mut_3 = self.make_mutation(position=300, sequence_change="G>A")

        for mutation in (self.mut_1, self.mut_2, self.mut_3):
            self.observe(self.sample_a, mutation)
        self.observe(self.sample_b, self.mut_1)

    # --- fixture builders -----------------------------------------------------------------

    def make_sample(self, flask_number, isolate_number=1, is_population=False):
        flask = Flask.objects.create(ale_id=self.ale, flask_number=flask_number,
                                     media=self.context["media"])
        isolate = Isolate.objects.create(flask=flask, isolate_number=isolate_number,
                                         is_population=is_population,
)
        replicate = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        return ResequencingExperiment.objects.create(
            tech_rep=replicate, sample_name="A1 F%d I%d R1" % (flask_number, isolate_number))

    def make_mutation(self, position, sequence_change, gene="thrA", experiment=None):
        return Mutation.objects.create(
            ale_experiment=experiment or self.experiment,
            mutation_type="SNP",
            position=position,
            reseq_reference="NC_000913",
            feature_length=None,
            sequence_change=sequence_change,
            gene=gene,
            product="a product",
            gd_data={"type": "SNP", "id": position, "parent_ids": None,
                     "seq_id": "NC_000913", "position": position},
            annotation={"gene_name": gene})

    def observe(self, sample, mutation, frequency="0.7500", present=True):
        return ObservedMutation.objects.create(
            sequencing_experiment=sample,
            mutation=mutation,
            present=present,
            source="breseq",
            wt_reads=10,
            mutated_reads=30,
            frequency=Decimal(frequency))

    # --- assertions the suites share ------------------------------------------------------

    def observed_ids(self, sample):
        return set(ObservedMutation.objects
                   .filter(sequencing_experiment=sample)
                   .values_list("mutation_id", flat=True))

    def observation_count(self):
        return ObservedMutation.objects.filter(
            **{paths.to_experiment(paths.FROM_OBSERVATION): self.experiment}).count()
