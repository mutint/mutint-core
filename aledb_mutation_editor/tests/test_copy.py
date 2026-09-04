"""Batch-copying a mutation onto sibling samples.

The case worth pinning is the skip: "make sure this call is on these samples too" is what the
button means, so a target that already carries the mutation must be left alone rather than
given a second observation of it, which would silently double that sample's count.
"""

import json

from aledb_mutation_editor.models import KIND_COPY, MutationChange, MutationChangeSet
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import ObservedMutation

COPY = "/mutation-editor/copy/apply"


class CopyTestCase(EditorTestCase):

    def _copy(self, mutations, targets, source=None):
        return self.client.post(COPY, {
            "experiment_id": self.experiment.id,
            "source_sample_id": (source or self.sample_a).id,
            "mutation_ids": json.dumps([m.id for m in mutations]),
            "target_sample_ids": json.dumps([t.id for t in targets]),
        })

    def test_a_mutation_lands_on_the_target(self):
        response = self._copy([self.mut_2], [self.sample_b])

        self.assertEqual(200, response.status_code)
        self.assertEqual({self.mut_1.id, self.mut_2.id}, self.observed_ids(self.sample_b))

    def test_the_copy_carries_the_sources_values(self):
        source = ObservedMutation.objects.get(sample=self.sample_a,
                                              mutation=self.mut_2)
        self._copy([self.mut_2], [self.sample_b])

        copied = ObservedMutation.objects.get(sample=self.sample_b,
                                              mutation=self.mut_2)
        self.assertEqual(source.frequency, copied.frequency)
        self.assertEqual(source.wt_reads, copied.wt_reads)
        self.assertEqual(source.source, copied.source)

    def test_a_batch_is_one_changeset(self):
        third = self.make_sample(flask_number=3)
        self._copy([self.mut_2, self.mut_3], [self.sample_b, third])

        self.assertEqual(1, MutationChangeSet.objects.count())
        self.assertEqual(KIND_COPY, MutationChangeSet.objects.get().kind)
        self.assertEqual(4, MutationChange.objects.count())

    def test_it_records_where_the_copy_came_from(self):
        self._copy([self.mut_2], [self.sample_b])

        change = MutationChange.objects.get()
        self.assertEqual(self.sample_a.id, change.source_sample_id)
        self.assertEqual(self.sample_b.id, change.sample_id)

    def test_a_target_that_already_has_it_is_skipped(self):
        response = self._copy([self.mut_1], [self.sample_b])

        body = response.json()
        self.assertEqual(0, body["added"])
        self.assertEqual([self.sample_b.label], body["already"])
        self.assertEqual(1, ObservedMutation.objects.filter(
            sample=self.sample_b, mutation=self.mut_1).count())

    def test_skipping_everything_writes_no_changeset(self):
        self._copy([self.mut_1], [self.sample_b])
        self.assertEqual(0, MutationChangeSet.objects.count())

    def test_a_copy_can_be_restored_away(self):
        from aledb_mutation_editor import history

        self._copy([self.mut_2], [self.sample_b])
        self.assertEqual({self.mut_1.id, self.mut_2.id}, self.observed_ids(self.sample_b))

        history.restore(self.experiment, self.owner, None)

        self.assertEqual({self.mut_1.id}, self.observed_ids(self.sample_b))

    def test_it_refuses_a_mutation_from_another_experiment(self):
        """Scoped through the experiment rather than taken on trust, so a hand-built POST
        cannot reach across projects."""
        created = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        from aledb_experiment.models import Experiment
        other = Experiment.objects.get(pk=created["experiment_id"])
        stranger = self.make_mutation(position=999, sequence_change="T>A", experiment=other)

        response = self._copy([stranger], [self.sample_b])

        self.assertEqual(404, response.status_code)
        self.assertEqual({self.mut_1.id}, self.observed_ids(self.sample_b))

    def test_it_asks_for_something_to_do(self):
        for payload in ({"mutation_ids": "[]", "target_sample_ids": "[1]"},
                        {"mutation_ids": "[1]", "target_sample_ids": "[]"}):
            with self.subTest(payload=payload):
                data = {"experiment_id": self.experiment.id,
                        "source_sample_id": self.sample_a.id}
                data.update(payload)
                self.assertEqual(400, self.client.post(COPY, data).status_code)

    def test_a_malformed_list_is_a_400_not_a_500(self):
        response = self.client.post(COPY, {
            "experiment_id": self.experiment.id,
            "source_sample_id": self.sample_a.id,
            "mutation_ids": "not json",
            "target_sample_ids": "[]"})
        self.assertEqual(400, response.status_code)
        self.assertIn("JSON", response.json()["error"])
