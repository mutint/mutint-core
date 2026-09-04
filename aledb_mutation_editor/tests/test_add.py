"""Adding a mutation nothing in the experiment carries yet.

The assertion that matters most is the dull one: `test_sequence_change_matches_an_import`.
`sequence_change` is one of the seven fields `Mutation.objects.get_or_create` keys on, so if
this page derives it even slightly differently from `gd_import`, a hand-entered mutation and a
later re-import of the same call quietly become two rows for one mutation.
"""

import json

from aledb_import.gd_import import synthesize_sequence_change
from aledb_mutation_editor import history, record_builder
from aledb_mutation_editor.models import KIND_ADD, MutationEdit, MutationEditSet
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_sample.models import Mutation, MutationCall
from genomediff.records import Record

ADD = "/mutation-editor/add/apply"


class AddTestCase(EditorTestCase):
    """The fixture experiment has no stored reference, so sequence checks are off here.

    That is the right default for these: they are about what gets written, and a reference
    would only mean every SNP had to dodge the actual bases. `AnnotatedAddTestCase` below
    establishes one for the cases that need it.
    """

    #: `targets=[]` has to mean "none selected", so the default cannot be an `or`.
    DEFAULT_TARGETS = object()

    def add(self, mutation_type="SNP", targets=DEFAULT_TARGETS, **fields):
        if targets is self.DEFAULT_TARGETS:
            targets = [self.sample_a]
        payload = {
            "experiment_id": self.experiment.id,
            "mutation_type": mutation_type,
            "target_sample_ids": json.dumps([s.id for s in targets]),
        }
        payload.update(fields)
        return self.client.post(ADD, payload)

    def snp(self, position=5000, new_seq="T", **kwargs):
        return self.add(seq_id="NC_000913", position=position, new_seq=new_seq, **kwargs)

    # --- what it writes -------------------------------------------------------------------

    def test_the_mutation_appears_on_the_sample(self):
        response = self.snp()

        self.assertEqual(200, response.status_code, response.content)
        mutation = Mutation.objects.get(start_position=5000)
        self.assertEqual("SNP", mutation.mutation_type)
        self.assertEqual("NC_000913", mutation.seq_id)
        self.assertIn(mutation.id, self.call_ids(self.sample_a))

    def test_it_is_scoped_to_the_experiment(self):
        self.snp()
        self.assertEqual(self.experiment, Mutation.objects.get(start_position=5000).experiment)

    def test_adding_to_three_samples_is_one_edit_set(self):
        third = self.make_sample(flask_number=3)
        response = self.add(seq_id="NC_000913", position=5000, new_seq="T",
                            targets=[self.sample_a, self.sample_b, third])

        self.assertEqual(3, response.json()["added"])
        self.assertEqual(1, MutationEditSet.objects.count())
        self.assertEqual(KIND_ADD, MutationEditSet.objects.get().kind)
        self.assertEqual(3, MutationEdit.objects.count())

    def test_one_mutation_row_serves_every_sample(self):
        self.add(seq_id="NC_000913", position=5000, new_seq="T",
                 targets=[self.sample_a, self.sample_b])
        self.assertEqual(1, Mutation.objects.filter(start_position=5000).count())

    def test_the_call_is_marked_manual(self):
        """Distinguishes a typed call from a called one everywhere `source` is read. Null
        would mean 'imported before the column existed', which is a different thing."""
        self.snp()
        call = MutationCall.objects.get(mutation__start_position=5000)
        self.assertEqual("manual", call.source)
        self.assertTrue(call.present,
                        "a typed mutation is in the sample, and every cross-sample table "
                        "decides that by asking `present`")

    def test_the_frequency_is_applied_to_every_sample(self):
        self.add(seq_id="NC_000913", position=5000, new_seq="T", frequency="0.25",
                 targets=[self.sample_a, self.sample_b])
        for call in MutationCall.objects.filter(mutation__start_position=5000):
            self.assertEqual(0.25, call.frequency)

    def test_frequency_defaults_to_one(self):
        self.snp()
        self.assertEqual(1.0,
                         MutationCall.objects.get(mutation__start_position=5000).frequency)

    def test_the_edit_set_says_what_was_added(self):
        note = MutationEditSet.objects.get().note if self.snp() else None
        self.assertIn("SNP", note)
        self.assertIn("5000", note)

    # --- the dedup key --------------------------------------------------------------------

    def test_sequence_change_matches_an_import(self):
        """The whole reason `synthesize_sequence_change` stopped being private.

        It is part of the get_or_create key, so a second rule for it would fork the mutation
        the next time the same call arrived through the importer.
        """
        cases = (
            ("SNP", {"new_seq": "T"}),
            ("DEL", {"size": 12}),
            ("INS", {"new_seq": "GGA"}),
            ("AMP", {"size": 30, "new_copy_number": 3}),
            ("INV", {"size": 40}),
            ("MOB", {"repeat_name": "IS150", "strand": 1, "duplication_size": 9}),
        )
        # A distinct position each, rather than deleting between rounds: `MutationCall`
        # holds its Mutation with DO_NOTHING, so removing the mutation first leaves a dangling
        # row and SQLite's constraint check fails at the end of the test.
        for index, (mutation_type, fields) in enumerate(cases):
            position = 7000 + index
            with self.subTest(mutation_type=mutation_type):
                self.add(mutation_type, seq_id="NC_000913", position=position, **fields)

                expected = synthesize_sequence_change(
                    Record(mutation_type, 1, parent_ids=None,
                           seq_id="NC_000913", position=position, **fields))
                self.assertEqual(
                    expected, Mutation.objects.get(start_position=position).sequence_change)

    def test_an_existing_mutation_is_reused_not_duplicated(self):
        """Adding a call another sample already carries should link the row, as a re-import
        would, not fork it."""
        self.add(seq_id="NC_000913", position=5000, new_seq="T", targets=[self.sample_a])
        self.add(seq_id="NC_000913", position=5000, new_seq="T", targets=[self.sample_b])

        self.assertEqual(1, Mutation.objects.filter(start_position=5000).count())
        self.assertEqual(2, MutationCall.objects.filter(mutation__start_position=5000).count())

    def test_a_sample_that_already_has_it_is_skipped(self):
        self.snp()
        response = self.snp()

        self.assertEqual(0, response.json()["added"])
        self.assertEqual([self.sample_a.label],
                         response.json()["already"])
        self.assertEqual(1, MutationCall.objects.filter(mutation__start_position=5000).count())

    def test_skipping_everything_writes_no_edit_set(self):
        self.snp()
        MutationEditSet.objects.all().delete()
        self.snp()
        self.assertEqual(0, MutationEditSet.objects.count())

    # --- the gd record --------------------------------------------------------------------

    def test_gd_data_is_the_verbatim_record(self):
        self.add("MOB", seq_id="NC_000913", position=5000,
                 repeat_name="IS150", strand=-1, duplication_size=9)
        gd_data = Mutation.objects.get(start_position=5000).genome_diff

        self.assertEqual("MOB", gd_data["type"])
        self.assertEqual(-1, gd_data["strand"], "integers stay integers")
        self.assertEqual("IS150", gd_data["repeat_name"])
        self.assertIsNone(gd_data["parent_ids"], "a typed mutation cites no evidence")

    def test_gd_data_carries_no_id(self):
        """`to_gd_line` falls back to the row's own pk, which exists and is unique. A record
        built before its row does not have one to give."""
        self.snp()
        self.assertNotIn("id", Mutation.objects.get(start_position=5000).genome_diff)

    def test_it_round_trips_to_a_gd_line(self):
        self.snp()
        mutation = Mutation.objects.get(start_position=5000)
        line = mutation.to_gd_line().split("\t")

        self.assertEqual("SNP", line[0])
        self.assertEqual(str(mutation.id), line[1])
        self.assertEqual(".", line[2], "no parent ids")
        self.assertEqual(["NC_000913", "5000", "T"], line[3:6])

    def test_frequency_is_not_in_gd_data(self):
        """gd_data lives on the Mutation, which every sample observing it shares; a frequency
        is one sample's."""
        self.add(seq_id="NC_000913", position=5000, new_seq="T", frequency="0.5")
        self.assertNotIn("frequency", Mutation.objects.get(start_position=5000).genome_diff)

    # --- refusals --------------------------------------------------------------------------

    def test_a_bad_field_comes_back_named(self):
        response = self.add("DEL", seq_id="NC_000913", position=5000, size="wide")

        self.assertEqual(400, response.status_code)
        self.assertIn("size", response.json()["errors"])
        self.assertEqual(0, Mutation.objects.filter(start_position=5000).count())

    def test_several_bad_fields_all_come_back(self):
        response = self.add("MOB", seq_id="NC_000913", position=5000,
                            repeat_name="", strand=9, duplication_size="x")
        errors = response.json()["errors"]
        for field in ("repeat_name", "strand", "duplication_size"):
            self.assertIn(field, errors)

    def test_an_out_of_range_frequency_is_refused(self):
        response = self.add(seq_id="NC_000913", position=5000, new_seq="T", frequency="2")
        self.assertEqual(400, response.status_code)
        self.assertIn("frequency", response.json()["errors"])

    def test_no_samples_selected_is_refused(self):
        response = self.add(seq_id="NC_000913", position=5000, new_seq="T", targets=[])
        self.assertEqual(400, response.status_code)

    def test_a_sample_from_another_experiment_is_refused(self):
        """Targets are resolved through this experiment's own sample list, so an id from
        elsewhere matches nothing and a hand-built POST cannot reach across projects."""
        from aledb_experiment.models import Experiment, Population
        from aledb_sample.models import Sample

        created = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        other = Experiment.objects.get(pk=created["experiment_id"])
        ale = Population.objects.create(experiment=other, name=1)
        stranger = Sample.objects.create(
            population=ale, time_point=1, name=1, is_clonal=True)

        response = self.add(seq_id="NC_000913", position=5000, new_seq="T",
                            targets=[stranger])

        self.assertEqual(404, response.status_code)
        self.assertEqual(0, MutationCall.objects.filter(
            sample=stranger).count())

    # --- it undoes ---------------------------------------------------------------------------

    def test_an_addition_restores_away(self):
        self.add(seq_id="NC_000913", position=5000, new_seq="T",
                 targets=[self.sample_a, self.sample_b])
        self.assertEqual(2, MutationCall.objects.filter(mutation__start_position=5000).count())

        history.restore(self.experiment, self.owner, None)

        self.assertEqual(0, MutationCall.objects.filter(mutation__start_position=5000).count())
        self.assertEqual(4, self.call_count(), "the fixture's own rows are untouched")


class NoReferenceTestCase(EditorTestCase):
    """The fixture experiment has none, which is a real state, not a broken one."""

    def test_the_page_says_the_sequence_checks_are_off(self):
        response = self.client.get("/mutation-editor/add",
                                   {"experiment_id": self.experiment.id})
        self.assertContains(response, "no reference genome")

    def test_a_mutation_that_would_change_nothing_is_accepted(self):
        """Nothing can know, and pretending otherwise would be worse than saying so."""
        response = self.client.post(ADD, {
            "experiment_id": self.experiment.id,
            "mutation_type": "SNP",
            "seq_id": "NC_000913",
            "position": 1,
            "new_seq": "A",
            "target_sample_ids": json.dumps([self.sample_a.id])})
        self.assertEqual(200, response.status_code)

    def test_shape_is_still_enforced(self):
        response = self.client.post(ADD, {
            "experiment_id": self.experiment.id,
            "mutation_type": "MOB",
            "seq_id": "NC_000913",
            "position": 1,
            "repeat_name": "IS150",
            "strand": 7,
            "duplication_size": 0,
            "target_sample_ids": json.dumps([self.sample_a.id])})
        self.assertEqual(400, response.status_code)
        self.assertIn("strand", response.json()["errors"])


class AnnotatedAddTestCase(EditorTestCase):
    """With a real reference established, so the sequence checks and the annotator both run.

    `synthetic.gff3` is 6000 bases of repeating ATCGGC with genes over it; thrA is 101-400 on
    the plus strand. The base at position p is `"ATCGGC"[(p - 1) % 6]`, which is what lets a
    no-op be constructed exactly rather than guessed at.
    """

    SEQ = "SYN001"

    def setUp(self):
        super().setUp()

        import os
        import shutil
        import tempfile

        from django.test import override_settings

        from aledb_import import annotation, reference, reference_store

        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)

        store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, store, True)
        patcher = override_settings(ALEDB_STORE_DIR=store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        fixture = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "aledb_import", "annotate", "tests", "fixtures", "synthetic.gff3")
        gff3_text, sequences = reference.normalize_reference(fixture)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences)

    def base_at(self, position):
        return "ATCGGC"[(position - 1) % 6]

    def add(self, mutation_type="SNP", targets=None, **fields):
        payload = {
            "experiment_id": self.experiment.id,
            "mutation_type": mutation_type,
            "target_sample_ids": json.dumps(
                [s.id for s in (targets if targets is not None else [self.sample_a])]),
        }
        payload.setdefault("seq_id", self.SEQ)
        payload.update(fields)
        return self.client.post(ADD, payload)

    # --- the sequence checks, for real ------------------------------------------------------

    def test_a_snp_to_the_base_already_there_is_refused(self):
        current = self.base_at(150)
        response = self.add(position=150, new_seq=current)

        self.assertEqual(400, response.status_code)
        self.assertIn("already %s" % current, response.json()["errors"]["new_seq"])
        self.assertEqual(0, Mutation.objects.filter(start_position=150).count())

    def test_a_snp_to_a_different_base_is_accepted(self):
        current = self.base_at(150)
        other = "A" if current != "A" else "T"
        self.assertEqual(200, self.add(position=150, new_seq=other).status_code)

    def test_a_position_past_the_end_of_the_contig_is_refused(self):
        response = self.add(position=99999, new_seq="A")
        self.assertEqual(400, response.status_code)
        self.assertIn("6000", response.json()["errors"]["position"])

    def test_an_unknown_contig_is_refused(self):
        response = self.add(seq_id="NOPE", position=150, new_seq="A")
        self.assertIn("not a sequence", response.json()["errors"]["position"])

    # --- annotation --------------------------------------------------------------------------

    def test_the_new_mutation_is_annotated(self):
        """Without this it would render through the unannotated fallback, which is what a
        mutation imported before a reference existed looks like."""
        self.add(position=150, new_seq="A")

        mutation = Mutation.objects.get(start_position=150)
        self.assertEqual("thrA", mutation.gene_name)
        self.assertTrue(mutation.annotation, "the annotation blob is populated")
        self.assertTrue(mutation.snp_type, "a promoted column apply_to fills")

    def test_the_gene_column_is_the_gene_not_the_string_None(self):
        self.add(position=150, new_seq="A")
        self.assertEqual("thrA", Mutation.objects.get(start_position=150).gene)

    def test_an_intergenic_position_still_annotates(self):
        """Position 450 sits between thrA (101-400) and thrB (501-800)."""
        self.add(position=450, new_seq="A")
        self.assertTrue(Mutation.objects.get(start_position=450).gene_name)

    # --- the page ------------------------------------------------------------------------------

    def test_the_page_offers_the_reference_contigs(self):
        response = self.client.get("/mutation-editor/add",
                                   {"experiment_id": self.experiment.id})
        self.assertContains(response, 'id="me-f-seq_id"')
        self.assertContains(response, self.SEQ)
        self.assertNotContains(response, "no reference genome")


class RecordBuilderTestCase(EditorTestCase):
    """The pieces, without a request."""

    def test_an_unannotated_record_still_produces_a_usable_identity(self):
        gd_data = record_builder.build_genome_diff(
            "SNP", {"seq_id": "NC_000913", "position": 42, "new_seq": "G"})
        record, annotated = record_builder.annotate(gd_data, self.experiment)

        self.assertFalse(annotated, "the fixture experiment has no reference")

        identity = record_builder.build_identity("SNP", gd_data, record)
        self.assertEqual(42, identity["start_position"])
        self.assertEqual("NC_000913", identity["seq_id"])
        self.assertEqual("G", identity["sequence_change"])
        self.assertIsNone(identity["annotation"])

        # The literal string "None", not "". `get_annotated_gene_list(None)` stringifies its
        # argument, so `gd_import` has always written "None" into `gene` for a mutation with
        # no annotation. `gene` is part of the get_or_create key, so this path has to agree
        # with it -- writing "" here would fork every unannotated mutation the next time the
        # same call was imported. Worth fixing one day, in both places at once and with a
        # data migration; not worth diverging over now.
        self.assertEqual("None", identity["gene"])

    def test_the_identity_has_exactly_the_keys_history_expects(self):
        gd_data = record_builder.build_genome_diff(
            "DEL", {"seq_id": "NC_000913", "position": 42, "size": 3})
        record, _ = record_builder.annotate(gd_data, self.experiment)
        identity = record_builder.build_identity("DEL", gd_data, record)

        expected = set(history.MUTATION_KEY_FIELDS) | {
            "supplemental_data", "annotation", "product", "protein_change"}
        self.assertEqual(expected, set(identity))

    def test_size_becomes_feature_length(self):
        gd_data = record_builder.build_genome_diff(
            "DEL", {"seq_id": "NC_000913", "position": 42, "size": 3})
        record, _ = record_builder.annotate(gd_data, self.experiment)
        self.assertEqual(3, record_builder.build_identity("DEL", gd_data, record)["feature_length"])

    def test_a_type_without_a_size_has_no_feature_length(self):
        gd_data = record_builder.build_genome_diff(
            "SNP", {"seq_id": "NC_000913", "position": 42, "new_seq": "G"})
        record, _ = record_builder.annotate(gd_data, self.experiment)
        self.assertIsNone(
            record_builder.build_identity("SNP", gd_data, record)["feature_length"])
