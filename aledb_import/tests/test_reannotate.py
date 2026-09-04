"""
./aledb reannotate

The case it exists for: an experiment was imported against a poor reference -- or
none -- and a better one arrives later. Everything already stored has to be
recomputed against it.
"""

import os
import shutil
import tempfile
from datetime import datetime
from io import StringIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import annotation, gd_import, reference, reference_store
from aledb_sample.models import ReferenceSequences, Mutation

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "annotate", "tests", "fixtures")
SYNTHETIC_GFF3 = os.path.join(FIXTURES, "synthetic.gff3")
SYNTHETIC_GBK = os.path.join(FIXTURES, "synthetic.gbk")
SYNTHETIC_GD = os.path.join(FIXTURES, "synthetic.gd")


def _uploaded_as(path, name):
    with open(path, "rb") as handle:
        return SimpleUploadedFile(name, handle.read())


class ReannotateTestCase(TestCase):
    """Imported against a bare FASTA, then re-annotated against the real thing."""

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]

        # A sequence-only reference: enough to import against, no genes at all.
        self.bare_fasta = os.path.join(self.store, "bare.fasta")
        _, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        with open(self.bare_fasta, "w") as handle:
            handle.write(reference.render_fasta(sequences))
        self._establish(self.bare_fasta)

        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name="syn exp", owner_name="tester")

    def _establish(self, path):
        gff3_text, sequences = reference.normalize_reference(path)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences,
                                           update_annotation=True)

    def run_command(self, *args, **options):
        out = StringIO()
        call_command("reannotate", self.experiment.id, *args,
                     stdout=out, stderr=out, **options)
        return out.getvalue()

    def categories(self):
        return set(Mutation.objects.values_list("mutation_category", flat=True))

    def mutation_at(self, position):
        return Mutation.objects.get(experiment=self.experiment, start_position=position)

    def gene_names(self):
        return set(Mutation.objects.values_list("gene_name", flat=True))

    def test_the_import_started_out_without_gene_context(self):
        """A FASTA carries sequence but no genes.

        Categorisation still works -- it only needs the mutation's type and size
        -- so every mutation has a mutation_category. What is missing is
        everything that needs a gene: names, codons, amino acids. Every SNP falls
        back to snp_intergenic because there is no gene anywhere to be inside.
        """
        self.assertEqual(36, Mutation.objects.count())
        self.assertLessEqual(self.gene_names(), {None, "", "–/–"})
        self.assertIn("large_deletion", self.categories())
        snp_types = set(Mutation.objects.filter(mutation_type="SNP")
                        .values_list("snp_type", flat=True))
        self.assertEqual({"intergenic"}, snp_types)

    def test_a_new_reference_annotates_everything(self):
        output = self.run_command(reference_path=SYNTHETIC_GFF3, skip_rebuilds=True)

        self.assertIn("annotated:  36 (36 changed)", output)
        self.assertNotIn(None, self.categories())
        self.assertNotIn("", self.categories())

        mutation = self.mutation_at(130)
        self.assertEqual("thrA", mutation.gene_name)
        self.assertEqual("nonsense", mutation.snp_type)
        self.assertEqual("snp_nonsense", mutation.mutation_category)
        self.assertEqual("Y10* (TAC→TAA)", mutation.protein_change)
        self.assertEqual("TAC", mutation.annotation["codon_ref_seq"])

    def test_the_new_reference_is_stored(self):
        before = ReferenceSequences.objects.get().gff3_sha256
        self.run_command(reference_path=SYNTHETIC_GFF3, skip_rebuilds=True)

        stored = ReferenceSequences.objects.get()
        self.assertNotEqual(before, stored.gff3_sha256)
        expected, _sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        self.assertEqual(reference_store.digest(expected), stored.gff3_sha256)
        # And it is on disk, ready for the next re-annotation.
        self.assertTrue(os.path.isfile(store.experiment_reference_path(
            self.experiment.id, store.REFERENCE_GFF3)))

    def test_genbank_gives_the_same_result_as_gff3(self):
        self.run_command(reference_path=SYNTHETIC_GBK, skip_rebuilds=True)
        from_genbank = {m.start_position: (m.gene_name, m.snp_type, m.annotation)
                        for m in Mutation.objects.all()}

        self.run_command(reference_path=SYNTHETIC_GFF3, skip_rebuilds=True)
        from_gff3 = {m.start_position: (m.gene_name, m.snp_type, m.annotation)
                     for m in Mutation.objects.all()}

        self.assertEqual(from_genbank, from_gff3)

    def test_running_again_changes_nothing(self):
        self.run_command(reference_path=SYNTHETIC_GFF3, skip_rebuilds=True)
        output = self.run_command(skip_rebuilds=True)

        self.assertIn("annotated:  36 (0 changed)", output)
        self.assertIn("Already up to date", output)

    def test_dry_run_reports_but_writes_nothing(self):
        before = ReferenceSequences.objects.get().gff3_sha256
        output = self.run_command(reference_path=SYNTHETIC_GFF3, dry_run=True)

        self.assertIn("36 (36 changed)", output)
        self.assertIn("Dry run", output)
        self.assertEqual(before, ReferenceSequences.objects.get().gff3_sha256)
        self.assertLessEqual(self.gene_names(), {None, "", "–/–"})

    def test_gd_data_is_left_verbatim(self):
        self.run_command(reference_path=SYNTHETIC_GFF3, skip_rebuilds=True)
        gd_data = self.mutation_at(130).genome_diff
        self.assertEqual("A", gd_data["new_seq"])
        for key in ("gene_name", "snp_type", "html_mutation"):
            self.assertNotIn(key, gd_data)

    def test_a_different_genome_is_refused_without_replace(self):
        other = os.path.join(self.store, "other.fasta")
        with open(other, "w") as handle:
            handle.write(">SYN001\nACGTACGTACGT\n")

        with self.assertRaises(CommandError) as caught:
            self.run_command(reference_path=other, skip_rebuilds=True)
        self.assertIn("--replace", str(caught.exception))

    def test_a_different_genome_is_accepted_with_replace(self):
        other = os.path.join(self.store, "other.fasta")
        with open(other, "w") as handle:
            handle.write(">SYN001\nACGTACGTACGT\n")

        self.run_command(reference_path=other, replace=True, skip_rebuilds=True)
        self.assertEqual(reference_store.digest(reference.render_fasta(
            [("SYN001", "ACGTACGTACGT")])),
            ReferenceSequences.objects.get().fasta_sha256)

    def test_unknown_experiment(self):
        with self.assertRaises(CommandError):
            call_command("reannotate", 9999, stdout=StringIO())

    def test_missing_reference_file(self):
        with self.assertRaises(CommandError) as caught:
            self.run_command(reference_path="/nonexistent/ref.gbk")
        self.assertIn("not found", str(caught.exception))


class ReannotateWithoutAReferenceTestCase(TestCase):
    """An experiment with no reference at all cannot be re-annotated silently."""

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]

    def test_it_says_so_rather_than_doing_nothing(self):
        with self.assertRaises(CommandError) as caught:
            call_command("reannotate", self.experiment.id, stdout=StringIO())
        self.assertIn("--ref", str(caught.exception))


class ReannotateOtherExperimentsTestCase(TestCase):
    """Re-annotating one experiment must not touch another's mutations."""

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.first = self._import_experiment("exp one")
        self.second = self._import_experiment("exp two")

    def _import_experiment(self, name):
        context = gd_import._prepare_experiment("syn project", name, "tester", False)
        gff3_text, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        reference_store.establish_or_check(context["experiment"], gff3_text, sequences)
        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name=name, owner_name="tester")
        return context["experiment"]

    def test_the_other_experiment_is_untouched(self):
        untouched = {m.pk: (m.gene_name, m.snp_type)
                     for m in Mutation.objects.filter(experiment=self.second)}
        self.assertEqual(36, len(untouched))

        other = os.path.join(self.store, "other.fasta")
        with open(other, "w") as handle:
            handle.write(">SYN001\n%s\n" % ("ACGT" * 40))
        call_command("reannotate", self.first.id, reference_path=other,
                     replace=True, skip_rebuilds=True, stdout=StringIO())

        for pk, before in untouched.items():
            mutation = Mutation.objects.get(pk=pk)
            self.assertEqual(before, (mutation.gene_name, mutation.snp_type))
