"""Starting a new experiment from an experiment that already has a reference genome.

The point of the feature is that a deployment running ten ALEs against one genome should
not have to upload it ten times -- so what these tests pin is that the copy is *the same
reference*, byte for byte, rather than merely a genome with the same bases. Everything
downstream depends on that: `ReferenceSequences.matches_sequence` decides whether a sample
may be imported, and `gd_import._check_seq_ids` refuses a `.gd` whose contigs are not the
reference's.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_experiment.models import Experiment, Project
from mutint_import import annotation, reference as reference_io, reference_store
from mutint_import.tests import breseq_fixture
from mutint_import.tests.test_reference_upload import write_genbank
from mutint_sample import ncbi
from mutint_sample.models import DatabaseSequenceLink, ReferenceSequences

SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A)]
TWO_SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A),
                 ("plasmid", breseq_fixture.SEQUENCE_B)]


class ReferenceCopyTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)

        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.project = Project.objects.create(name="P", user=self.user)
        self.source = Experiment.objects.create(name="Source", project=self.project)
        self.target = Experiment.objects.create(name="Target", project=self.project)

    def _establish(self, experiment, sequences=SEQUENCES):
        """Give `experiment` a reference the way the Reference Sequence tab would."""
        path = write_genbank(os.path.join(self.tmp, "ref-%s.gbk" % experiment.id),
                            sequences=sequences)
        gff3_text, parsed = reference_io.normalize_reference(path)
        return reference_store.establish_or_check(experiment, gff3_text, parsed)

    def test_the_copy_is_the_same_reference_to_the_byte(self):
        with override_settings(MUTINT_STORE_DIR=self.store):
            self._establish(self.source, TWO_SEQUENCES)
            annotation.copy_reference(self.source, self.target)

            source = ReferenceSequences.objects.get(experiment=self.source)
            target = ReferenceSequences.objects.get(experiment=self.target)

            # All three digests, not only the identity one: the stored artifacts are handed
            # over verbatim, so equality here is by construction and a failure means the
            # copy started deriving something instead of copying it.
            self.assertEqual(target.gff3_sha256, source.gff3_sha256)
            self.assertEqual(target.fasta_sha256, source.fasta_sha256)
            self.assertEqual(target.sequence_sha256, source.sequence_sha256)
            self.assertEqual(target.total_length, source.total_length)
            self.assertEqual([(e["id"], e["length"], e["sha256"]) for e in target.seq_ids],
                             [(e["id"], e["length"], e["sha256"]) for e in source.seq_ids])

    def test_the_stored_files_are_identical(self):
        with override_settings(MUTINT_STORE_DIR=self.store):
            self._establish(self.source, TWO_SEQUENCES)
            annotation.copy_reference(self.source, self.target)

            for name in (store.REFERENCE_GFF3, store.REFERENCE_FASTA, store.REFERENCE_FAI):
                with open(store.experiment_reference_path(self.source.id, name)) as handle:
                    expected = handle.read()
                with open(store.experiment_reference_path(self.target.id, name)) as handle:
                    self.assertEqual(handle.read(), expected, name)

    def test_a_sample_of_the_source_genome_is_importable_against_the_copy(self):
        """The consequence that actually matters: the copy passes the shared-reference check."""
        with override_settings(MUTINT_STORE_DIR=self.store):
            self._establish(self.source, SEQUENCES)
            annotation.copy_reference(self.source, self.target)

            # Re-establishing the same genome on the target must verify rather than refuse.
            _row, created = self._establish(self.target, SEQUENCES)
            self.assertFalse(created)

    def test_aliases_are_not_carried_across(self):
        """An alias is the name *this* experiment's stored BAMs were built with."""
        with override_settings(MUTINT_STORE_DIR=self.store):
            row, _created = self._establish(self.source, SEQUENCES)
            entries = row.seq_ids
            entries[0]["aliases"] = ["REL606"]
            row.seq_ids = entries
            row.save(update_fields=["seq_ids"])

            annotation.copy_reference(self.source, self.target)

            target = ReferenceSequences.objects.get(experiment=self.target)
            self.assertEqual([e.get("aliases") or [] for e in target.seq_ids], [[]])

    def test_an_ncbi_verdict_follows_for_free(self):
        """`DatabaseSequenceLink` is keyed on the bases, so the copy is already confirmed."""
        with override_settings(MUTINT_STORE_DIR=self.store):
            row, _created = self._establish(self.source, SEQUENCES)
            digest = row.seq_ids[0]["sha256"]
            DatabaseSequenceLink.objects.create(
                sha256=digest, length=row.seq_ids[0]["length"],
                accession="NC_000913.3",
                status=DatabaseSequenceLink.VERIFIED, detail="")

            annotation.copy_reference(self.source, self.target)

            states = ncbi.contig_states(self.target)
            self.assertEqual([state["is_verified"] for state in states], [True])
            self.assertEqual(states[0]["accession"], "NC_000913.3")

    def test_a_source_with_no_reference_is_refused(self):
        with override_settings(MUTINT_STORE_DIR=self.store):
            with self.assertRaises(annotation.ReferenceUnavailable):
                annotation.copy_reference(self.source, self.target)
            self.assertFalse(ReferenceSequences.objects.filter(experiment=self.target).exists())

    def test_a_source_whose_files_are_gone_is_refused(self):
        """The row says there is a reference and the store cannot produce it."""
        with override_settings(MUTINT_STORE_DIR=self.store):
            self._establish(self.source, SEQUENCES)
            shutil.rmtree(store.experiment_reference_dir(self.source.id))

            with self.assertRaises(annotation.ReferenceUnavailable):
                annotation.copy_reference(self.source, self.target)
            self.assertFalse(ReferenceSequences.objects.filter(experiment=self.target).exists())

    def test_a_store_that_disagrees_with_its_row_is_refused(self):
        """Copying a corrupt store would establish a reference the source page does not show."""
        with override_settings(MUTINT_STORE_DIR=self.store):
            self._establish(self.source, SEQUENCES)
            path = store.experiment_reference_path(self.source.id, store.REFERENCE_GFF3)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("# edited by hand\n")

            with self.assertRaises(annotation.ReferenceUnavailable):
                annotation.copy_reference(self.source, self.target)
            self.assertFalse(ReferenceSequences.objects.filter(experiment=self.target).exists())


class ReferenceSourcesTestCase(TestCase):
    """What the new-experiment picker is offered."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)

    def _with_reference(self, project, name, sequences=SEQUENCES):
        experiment = Experiment.objects.create(name=name, project=project)
        path = write_genbank(os.path.join(self.tmp, "ref-%s.gbk" % experiment.id),
                             sequences=sequences)
        gff3_text, parsed = reference_io.normalize_reference(path)
        reference_store.establish_or_check(experiment, gff3_text, parsed)
        return experiment

    def test_only_experiments_with_a_reference_appear_and_they_are_grouped(self):
        with override_settings(MUTINT_STORE_DIR=self.store):
            beta = Project.objects.create(name="Beta", user=self.user)
            alpha = Project.objects.create(name="Alpha", user=self.user)
            self._with_reference(beta, "B one")
            self._with_reference(alpha, "A two", TWO_SEQUENCES)
            Experiment.objects.create(name="No reference", project=alpha)

            sources = reference_store.reference_sources(Experiment.objects.all())

            # Project name order, then experiment name order.
            self.assertEqual([group["name"] for group in sources], ["Alpha", "Beta"])
            self.assertEqual([e["name"] for e in sources[0]["experiments"]], ["A two"])
            self.assertEqual(sources[0]["experiments"][0]["contigs"], 2)
            self.assertEqual(
                sources[0]["experiments"][0]["bases"],
                len(breseq_fixture.SEQUENCE_A) + len(breseq_fixture.SEQUENCE_B))

    def test_an_experiment_with_no_project_is_left_out(self):
        """It is viewable by nobody, so it can never be an offer anybody may accept."""
        with override_settings(MUTINT_STORE_DIR=self.store):
            orphan = Experiment.objects.create(name="Orphan", project=None)
            path = write_genbank(os.path.join(self.tmp, "orphan.gbk"))
            gff3_text, parsed = reference_io.normalize_reference(path)
            reference_store.establish_or_check(orphan, gff3_text, parsed)

            self.assertEqual(reference_store.reference_sources(Experiment.objects.all()), [])
