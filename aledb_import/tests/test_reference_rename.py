"""Reference identity by sequence alone, and renaming an experiment's contigs.

The hashing tests need no database; the applying tests go through the real importer, because
what matters is that every place a contig name is persisted moves together -- and several of
those places are reached only by importing real records.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import plugin_registry
from aledb_import import breseq_folder, reference as reference_io, reference_rename
from aledb_import import reference_store
from aledb_import.tests import breseq_fixture
from aledb_sample.models import ReferenceSequences, Mutation


class SequenceIdentityTestCase(TestCase):
    """What makes two references the same reference."""

    def test_the_same_bases_under_different_names_are_the_same_genome(self):
        """The whole point of the change. Identity used to be the rendered FASTA, which
        carries `>seq_id` headers, so this pair used to be two different genomes."""
        a = [("REL606", "ACGTACGT")]
        b = [("NC_012967.1", "ACGTACGT")]

        self.assertEqual(reference_io.sequence_set_digest(a),
                         reference_io.sequence_set_digest(b))
        self.assertNotEqual(reference_io.render_fasta(a), reference_io.render_fasta(b))

    def test_order_does_not_matter(self):
        """The GenBank/GFF3 path sorts contigs and the bare-FASTA path keeps file order, so
        the same genome could otherwise hash two ways depending on the format it arrived in."""
        forward = [("a", "AAAA"), ("b", "CCCC")]
        backward = [("b", "CCCC"), ("a", "AAAA")]

        self.assertEqual(reference_io.sequence_set_digest(forward),
                         reference_io.sequence_set_digest(backward))

    def test_case_does_not_matter(self):
        self.assertEqual(reference_io.sequence_digest("acgt"),
                         reference_io.sequence_digest("ACGT"))

    def test_one_changed_base_is_a_different_genome(self):
        self.assertNotEqual(reference_io.sequence_set_digest([("x", "ACGT")]),
                            reference_io.sequence_set_digest([("x", "ACGA")]))

    def test_duplicate_contigs_are_a_multiset_not_a_set(self):
        """A genome carrying one copy of a sequence is not the same as one carrying two."""
        one = [("a", "ACGT")]
        two = [("a", "ACGT"), ("b", "ACGT")]

        self.assertNotEqual(reference_io.sequence_set_digest(one),
                            reference_io.sequence_set_digest(two))


class RewriteValueTestCase(TestCase):
    """Rewriting by value is what covers the names nobody enumerated."""

    MAPPING = {"old": "new"}

    def test_a_bare_name_is_rewritten(self):
        self.assertEqual("new", reference_rename.rewrite_value("old", self.MAPPING))

    def test_a_region_keeps_its_coordinates(self):
        """CON and INT carry `region`, and an annotated MOB carries `mob_region`, both
        `sequence:start-end`."""
        self.assertEqual("new:100-200",
                         reference_rename.rewrite_value("old:100-200", self.MAPPING))

    def test_an_unrelated_value_is_untouched(self):
        for value in ("older", "notold", "x:1-2", "", None, 5):
            self.assertEqual(value, reference_rename.rewrite_value(value, self.MAPPING))

    def test_every_embedded_name_in_a_record_moves(self):
        """By value, not by a list of keys: `gd_data` is contractually verbatim, so whatever
        breseq writes next carries names this has never been told about."""
        record = {"type": "MOB", "seq_id": "old", "region": "old:10-20",
                  "mob_region": "old:30-40", "gene_name": "old_gene", "position": 10}

        updated, changed = reference_rename.rewrite_genome_diff(record, self.MAPPING)

        self.assertTrue(changed)
        self.assertEqual("new", updated["seq_id"])
        self.assertEqual("new:10-20", updated["region"])
        self.assertEqual("new:30-40", updated["mob_region"])
        # A gene that merely starts with the old name is not a sequence reference.
        self.assertEqual("old_gene", updated["gene_name"])


class Gff3RenameTestCase(TestCase):

    def test_only_the_seqid_columns_move(self):
        text = ("##gff-version 3\n"
                "##sequence-region\told\t1\t100\n"
                "old\tbreseq\tgene\t1\t9\t.\t+\t.\tID=g1;Note=found in old by old\n"
                "##FASTA\n"
                ">old\nACGT\n")

        renamed = reference_rename.rename_gff3_text(text, {"old": "new"})

        self.assertIn("##sequence-region\tnew\t1\t100", renamed)
        self.assertIn("new\tbreseq\tgene", renamed)
        self.assertIn(">new", renamed)
        # Free text may legitimately mention the old name; a rename must not rewrite prose.
        self.assertIn("Note=found in old by old", renamed)

    def test_a_sequence_only_gff3_carries_no_annotation(self):
        """What a bare FASTA normalizes to, and what must never be installed as annotation."""
        self.assertFalse(reference_rename.has_annotation(
            "##gff-version 3\n##sequence-region\tx\t1\t4\n##FASTA\n>x\nACGT\n"))
        self.assertTrue(reference_rename.has_annotation(
            "##gff-version 3\nx\tbreseq\tgene\t1\t2\t.\t+\t.\tID=g\n##FASTA\n>x\nACGT\n"))


class RenamePlanningTestCase(TestCase):

    def _reference(self, entries):
        reference = ReferenceSequences(seq_ids=entries)
        reference.experiment_id = 1
        return reference

    def _entry(self, name, sequence):
        return {"id": name, "length": len(sequence),
                "sha256": reference_io.sequence_digest(sequence)}

    def test_names_are_paired_by_sequence(self):
        reference = self._reference([self._entry("REL606", "ACGT"),
                                     self._entry("pMG1", "GGTT")])

        plan = reference_rename.plan_rename(
            reference, [("pMG1", "GGTT"), ("NC_012967.1", "ACGT")])

        self.assertEqual([("REL606", "NC_012967.1")], plan.pairs)
        self.assertEqual(["pMG1"], plan.unchanged)

    def test_identical_names_are_not_a_rename(self):
        reference = self._reference([self._entry("x", "ACGT")])

        self.assertFalse(reference_rename.plan_rename(reference, [("x", "ACGT")]))

    def test_a_missing_sequence_is_refused_and_described(self):
        reference = self._reference([self._entry("chr", "ACGT"),
                                     self._entry("plasmid", "GGTT")])

        with self.assertRaises(reference_rename.SequenceSetMismatch) as caught:
            reference_rename.plan_rename(reference, [("chr", "ACGT")])

        message = str(caught.exception)
        self.assertIn("in this experiment but not in the upload", message)
        self.assertIn("plasmid", message)

    def test_an_extra_sequence_is_refused(self):
        reference = self._reference([self._entry("chr", "ACGT")])

        with self.assertRaises(reference_rename.SequenceSetMismatch) as caught:
            reference_rename.plan_rename(
                reference, [("chr", "ACGT"), ("extra", "GGTT")])

        self.assertIn("in the upload but not in this experiment", str(caught.exception))

    def test_a_different_multiplicity_is_counted(self):
        reference = self._reference([self._entry("a", "ACGT"), self._entry("b", "ACGT")])

        with self.assertRaises(reference_rename.SequenceSetMismatch) as caught:
            reference_rename.plan_rename(reference, [("a", "ACGT")])

        self.assertIn("this experiment has 2, the upload has 1", str(caught.exception))

    def test_identical_duplicate_contigs_are_refused_as_ambiguous(self):
        """Indistinguishable to the hash, entirely distinguishable to the mutations sitting
        on them, so guessing would silently move calls between contigs."""
        reference = self._reference([self._entry("a", "ACGT"), self._entry("b", "ACGT")])

        with self.assertRaises(reference_rename.RenameAmbiguous):
            reference_rename.plan_rename(reference, [("x", "ACGT"), ("y", "ACGT")])

    def test_a_name_with_a_colon_is_refused(self):
        """Region values are `sequence:start-end`; a colon in a name makes them unparseable."""
        reference = self._reference([self._entry("x", "ACGT")])

        with self.assertRaises(reference_rename.InvalidSequenceName):
            reference_rename.plan_rename(reference, [("a:b", "ACGT")])

    def test_a_name_too_long_for_the_column_is_refused(self):
        reference = self._reference([self._entry("x", "ACGT")])

        with self.assertRaises(reference_rename.InvalidSequenceName):
            reference_rename.plan_rename(reference, [("N" * 201, "ACGT")])

    def test_a_reference_with_no_per_sequence_hashes_names_the_repair(self):
        reference = self._reference([{"id": "x", "length": 4}])

        with self.assertRaises(reference_rename.RenameUnavailable) as caught:
            reference_rename.plan_rename(reference, [("y", "ACGT")])

        self.assertIn("rename_contigs", str(caught.exception))


class RenameApplicationTestCase(TestCase):
    """Through the real importer, because several name carriers only exist after an import."""

    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="p", experiment_name="e", owner_name="tester")
        self.reference = ReferenceSequences.objects.get()
        self.experiment = self.reference.experiment
        self.sequences = [("NC_TEST.1", breseq_fixture.SEQUENCE_A)]

    def _rename(self):
        plan = reference_rename.plan_rename(self.reference, self.sequences)
        return reference_rename.apply_rename(self.experiment, self.reference, plan)

    def test_mutations_carry_the_new_name_in_both_places(self):
        """`to_gd_line()` takes the name from `gd_data`, not from `seq_id`, so
        rewriting one and not the other makes exports silently disagree with the table."""
        self._rename()

        for mutation in Mutation.objects.filter(experiment=self.experiment):
            self.assertEqual("NC_TEST.1", mutation.seq_id)
            self.assertEqual("NC_TEST.1", mutation.genome_diff["seq_id"])
            self.assertIn("NC_TEST.1", mutation.to_gd_line())
            self.assertNotIn("test_ref", mutation.to_gd_line())

    def test_the_reference_records_the_old_name_as_an_alias(self):
        """The stored BAM and BigWig keep the names they were built with; the alias table is
        what lets igv resolve them."""
        self._rename()
        self.reference.refresh_from_db()

        entry = self.reference.seq_ids[0]
        self.assertEqual("NC_TEST.1", entry["id"])
        self.assertEqual(["test_ref"], entry["aliases"])
        self.assertEqual(reference_io.sequence_set_digest(self.sequences),
                         self.reference.sequence_sha256)

    def test_renaming_twice_keeps_every_former_name(self):
        """A BAM stored before the first rename still carries the first name."""
        self._rename()
        self.reference.refresh_from_db()
        self.sequences = [("NC_FINAL", breseq_fixture.SEQUENCE_A)]
        self._rename()
        self.reference.refresh_from_db()

        self.assertEqual(["test_ref", "NC_TEST.1"], self.reference.seq_ids[0]["aliases"])

    def test_a_swap_lands_correctly(self):
        """A→B, B→A is safe only because the rewrite is computed in memory from the original
        values. Two sequential UPDATEs per pair would collapse it -- and `Mutation` has no
        unique constraint, so the result would be silent duplicates, not an IntegrityError."""
        mutations = list(Mutation.objects.filter(experiment=self.experiment))
        self.assertTrue(mutations)
        first = mutations[0]
        first.seq_id = "other"
        first.set_record(Mutation.COMPONENT, Mutation.GENOME_DIFF,
                         dict(first.genome_diff, seq_id="other"), save=False)
        first.save(update_fields=["seq_id", "supplemental_data"])

        reference_rename._rename_mutations(
            Mutation, self.experiment, {"test_ref": "other", "other": "test_ref"})

        first.refresh_from_db()
        self.assertEqual("test_ref", first.seq_id)
        self.assertEqual("test_ref", first.genome_diff["seq_id"])
        for other in Mutation.objects.filter(experiment=self.experiment).exclude(
                pk=first.pk):
            self.assertEqual("other", other.seq_id)

    def test_plugins_are_told_what_moved(self):
        """Derived data keyed on a contig name is core's blind spot -- aledb-phylogeny keys
        its matrix columns on `seq_id` -- so the mapping is published."""
        seen = []
        plugin_registry.register_sequence_rename_hook(
            lambda experiment_id, renames: seen.append((experiment_id, renames)))
        self.addCleanup(plugin_registry._sequence_rename_hooks.pop)

        self._rename()

        self.assertEqual([(self.experiment.id, {"test_ref": "NC_TEST.1"})], seen)

    def test_a_failing_hook_does_not_undo_a_committed_rename(self):
        def explode(experiment_id, renames):
            raise RuntimeError("plugin is broken")

        plugin_registry.register_sequence_rename_hook(explode)
        self.addCleanup(plugin_registry._sequence_rename_hooks.pop)

        with self.assertLogs("aledb_common.plugin_registry", level="ERROR"):
            self._rename()

        self.assertEqual("NC_TEST.1",
                         Mutation.objects.filter(
                             experiment=self.experiment).first().seq_id)


class EstablishOrCheckRenameTestCase(TestCase):
    """The gate: a rename never happens as a side effect of an upload."""

    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="p", experiment_name="e", owner_name="tester")
        self.experiment = ReferenceSequences.objects.get().experiment
        self.renamed = [("NC_TEST.1", breseq_fixture.SEQUENCE_A)]
        self.gff3 = breseq_fixture.gff3_text(self.renamed)

    def test_a_rename_is_refused_until_it_is_allowed(self):
        with self.assertRaises(reference_store.RenameRequired) as caught:
            reference_store.establish_or_check(
                self.experiment, self.gff3, self.renamed, update_annotation=True)

        self.assertEqual([("test_ref", "NC_TEST.1")], caught.exception.plan.pairs)
        self.assertEqual("test_ref",
                         Mutation.objects.filter(
                             experiment=self.experiment).first().seq_id)

    def test_allowing_it_performs_the_rename(self):
        reference_store.establish_or_check(
            self.experiment, self.gff3, self.renamed, update_annotation=True,
            allow_rename=True)

        self.assertEqual("NC_TEST.1",
                         Mutation.objects.filter(
                             experiment=self.experiment).first().seq_id)

    def test_the_stored_files_carry_the_new_name(self):
        reference_store.establish_or_check(
            self.experiment, self.gff3, self.renamed, update_annotation=True,
            allow_rename=True)

        from aledb_common import store
        for filename in (store.REFERENCE_FASTA, store.REFERENCE_GFF3, store.REFERENCE_FAI):
            path = store.experiment_reference_path(self.experiment.id, filename)
            with open(path) as handle:
                body = handle.read()
            self.assertIn("NC_TEST.1", body, filename)
            self.assertNotIn("test_ref", body, filename)

    def test_a_fasta_rename_keeps_the_existing_annotation(self):
        """A bare FASTA normalizes to a feature-less GFF3. Installing it would replace the
        gene table with nothing, so the stored annotation is carried across instead."""
        gff3_text, sequences = reference_io.normalize_reference(
            self._write_fasta(), "renamed.fasta")

        reference_store.establish_or_check(
            self.experiment, gff3_text, sequences, update_annotation=True,
            allow_rename=True)

        from aledb_common import store
        path = store.experiment_reference_path(self.experiment.id, store.REFERENCE_GFF3)
        with open(path) as handle:
            stored = handle.read()
        self.assertIn("NC_TEST.1", stored)
        self.assertNotIn("test_ref", stored)
        self.assertTrue(reference_rename.has_annotation(stored))

    def _write_fasta(self):
        path = os.path.join(self.drop, "renamed.fasta")
        with open(path, "w") as handle:
            handle.write(breseq_fixture.fasta_text(self.renamed))
        return path

    def test_a_different_genome_is_still_refused(self):
        other = [("test_ref", breseq_fixture.SEQUENCE_B)]

        with self.assertRaises(reference_store.ReferenceMismatch):
            reference_store.establish_or_check(
                self.experiment, breseq_fixture.gff3_text(other), other,
                update_annotation=True, allow_rename=True)

    def test_seq_ids_are_refreshed_on_the_same_sequence_path(self):
        """The latent bug this feature makes live: `seq_ids` used to be written only on the
        create/replace branch, so a same-sequence re-upload left it stale."""
        reference = ReferenceSequences.objects.get()
        reference.seq_ids = [{"id": "test_ref", "length": 1}]
        reference.sequence_sha256 = ""
        reference.save(update_fields=["seq_ids", "sequence_sha256"])

        same = [("test_ref", breseq_fixture.SEQUENCE_A)]
        reference_store.establish_or_check(
            self.experiment, breseq_fixture.gff3_text(same), same, update_annotation=True)

        reference.refresh_from_db()
        self.assertEqual(len(breseq_fixture.SEQUENCE_A), reference.seq_ids[0]["length"])
        self.assertTrue(reference.seq_ids[0]["sha256"])
        self.assertTrue(reference.sequence_sha256)
