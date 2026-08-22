"""
The per-sample breseq report page.

What is worth asserting is that the stored annotation round-trips back into
breseq's own markup -- the colouring, the arrows, the underlined codon base --
because that is the whole reason the page exists rather than being another
rendering of the cross-sample table.
"""

import os
import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_experiment.models import AleExperiment
from aledb_import import annotation, gd_import, reference, reference_store
from aledb_import.tests.test_annotation import _uploaded_as
from aledb_seq.breseq_report import build_rows, gd_entry
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "aledb_import", "annotate", "tests", "fixtures")
SYNTHETIC_GFF3 = os.path.join(FIXTURES, "synthetic.gff3")
SYNTHETIC_GD = os.path.join(FIXTURES, "synthetic.gd")

PAGE = "/mutations/breseq"


class BreseqTablePageTestCase(TestCase):

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]
        gff3_text, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences)
        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name="syn exp", person="tester")

        self.reseq = ResequencingExperiment.objects.get()
        self.client.force_login(self.user)

    def get_page(self, **params):
        params.setdefault("ale_experiment_id", self.experiment.ale_id)
        return self.client.get(PAGE, params)

    def content(self, **params):
        return self.get_page(**params).content.decode()

    # --- the page itself ------------------------------------------------------

    def test_it_renders(self):
        response = self.get_page()
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "breseq-table")

    def test_every_mutation_of_the_sample_is_a_row(self):
        rows = build_rows(list(ObservedMutation.objects.all()))
        self.assertEqual(36, len(rows))
        self.assertTrue(all(row["annotated"] for row in rows))

    def test_it_names_the_columns_breseq_uses(self):
        content = self.content()
        for column in ("evidence", "position", "mutation", "annotation", "gene",
                       "description"):
            self.assertIn("<th>%s</th>" % column, content.replace("seq&nbsp;id", "seq id"))

    # --- breseq's markup ------------------------------------------------------

    def test_a_nonsense_snp_is_coloured_by_effect(self):
        self.assertIn("snp_type_nonsense", self.content())

    def test_the_changed_base_in_a_codon_is_marked(self):
        self.assertIn("mutation_in_codon", self.content())

    def test_gene_names_are_italic_with_a_strand_arrow(self):
        content = self.content()
        self.assertIn("<i>thrA</i>", content)
        self.assertIn("&rarr;", content)

    def test_a_deletion_renders_breseq_style(self):
        # Δ1,000 bp, non-breaking, as breseq writes it.
        self.assertIn("&Delta;1,000&nbsp;bp", self.content())

    def test_an_intergenic_mutation_shows_both_flanking_genes(self):
        content = self.content()
        self.assertIn("<i>thrA</i>", content)
        self.assertIn("<i>thrB</i>", content)

    # --- the sample picker ----------------------------------------------------

    def test_the_picker_lists_the_experiment_samples(self):
        response = self.get_page()
        self.assertContains(response, "reseq_picker")
        self.assertContains(response, self.reseq.ale_flask_isolate_str)

    def test_selecting_a_sample(self):
        response = self.get_page(reseq_id=self.reseq.id)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, self.reseq.ale_flask_isolate_str)

    def test_an_unknown_sample_falls_back_to_the_first(self):
        response = self.get_page(reseq_id=999999)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "breseq-table")

    def test_a_junk_sample_id_does_not_500(self):
        response = self.get_page(reseq_id="not-a-number")
        self.assertEqual(200, response.status_code)

    # --- frequency column -----------------------------------------------------

    def test_a_clonal_sample_has_no_frequency_column(self):
        self.assertNotIn("<th>freq</th>", self.content())

    def test_a_population_sample_has_one(self):
        isolate = self.reseq.tech_rep.isolate
        isolate.is_population = True
        isolate.save()
        content = self.content()
        self.assertIn("<th>freq</th>", content)
        self.assertIn("100%", content)

    def test_a_polymorphic_call_is_shaded(self):
        isolate = self.reseq.tech_rep.isolate
        isolate.is_population = True
        isolate.save()
        observed = ObservedMutation.objects.first()
        observed.frequency = 0.42
        observed.save()

        content = self.content()
        self.assertIn("polymorphism_table_row", content)
        self.assertIn("42.0%", content)

    # --- evidence column ------------------------------------------------------

    def test_evidence_links_to_the_alignment_when_there_is_one(self):
        self.reseq.bam_stored = True
        self.reseq.save()
        observed = ObservedMutation.objects.first()
        self.assertIn("observed_mut_id=%s" % observed.id, self.content())

    def test_evidence_is_plain_text_without_an_alignment(self):
        # A bare .gd import has no reads, so there is nothing to link to.
        self.assertFalse(self.reseq.bam_stored)
        self.assertNotIn("observed_mut_id=", self.content())

    # --- rows with no annotation ---------------------------------------------

    def test_a_mutation_with_no_annotation_falls_back_to_the_flat_columns(self):
        mutation = Mutation.objects.get(position=130)
        mutation.annotation = None
        mutation.save()

        self.assertIsNone(gd_entry(mutation))
        row = build_rows([ObservedMutation.objects.get(mutation=mutation)])[0]
        self.assertFalse(row["annotated"])
        self.assertEqual(mutation.sequence_change, row["mutation"])

    def test_the_page_says_how_to_fix_unannotated_rows(self):
        Mutation.objects.update(annotation=None)
        response = self.get_page()
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "reannotate")


class BreseqTablePermissionTestCase(TestCase):
    """The page is scoped through the same helper the rest of /mutations uses."""

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]

    def test_a_stranger_does_not_get_the_page(self):
        stranger = User.objects.create(username="stranger", email="s@e.com",
                                       is_active=True, date_joined=datetime.now())
        self.client.force_login(stranger)
        response = self.client.get(PAGE, {"ale_experiment_id": self.experiment.ale_id})
        self.assertNotContains(response, "breseq-table", status_code=200)

    def test_an_experiment_with_no_samples_says_so(self):
        self.client.force_login(User.objects.get(username="tester"))
        response = self.client.get(PAGE, {"ale_experiment_id": self.experiment.ale_id})
        self.assertContains(response, "no resequencing samples")
