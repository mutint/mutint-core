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
        params.setdefault("ale_experiment_id", self.experiment.id)
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

    def test_it_names_the_columns_for_what_they_hold(self):
        """Type and Reference, not breseq's evidence and seq id.

        Those named the .gd field rather than the column: the first renders
        mutation_type, the second the reference sequence name.
        """
        content = self.content()
        for header in ("Type", "Reference", "Position", "Mutation", "Annotation",
                       "Gene", "Description"):
            self.assertIn(">%s</th>" % header, content)
        for old_name in ("<th>evidence</th>", "<th>seq&nbsp;id</th>", "<th>position</th>"):
            self.assertNotIn(old_name, content)

    def test_every_header_carries_its_column_class(self):
        """This is what lets alignment be stated once and hold for both.

        Without it the headers fall back to Bootstrap's th { text-align: left }
        while the cells beneath align independently -- invisible to any test that
        only checks header text, which is how the two drifted apart.
        """
        content = self.content()
        for column in ("evidence", "seq-id", "position", "mutation", "annotation",
                       "gene", "description"):
            self.assertIn('<th class="breseq-%s">' % column, content)
            self.assertIn('<td class="breseq-%s">' % column, content)

    # --- breseq's markup ------------------------------------------------------

    def test_a_nonsense_snp_is_coloured_by_effect(self):
        self.assertIn("snp_type_nonsense", self.content())

    def test_the_changed_base_in_a_codon_is_marked(self):
        self.assertIn("mutation_in_codon", self.content())

    def test_gene_names_are_italic_with_a_strand_arrow(self):
        content = self.content()
        self.assertIn("<i>thrA</i>", content)
        self.assertIn("&rarr;", content)

    def test_the_gene_list_toggle_script_is_loaded(self):
        """Rendered, not read off the file: a <script> outside a block is discarded silently.

        Past 15 genes a deletion's Description collapses behind a Show button, and the
        handler for it lives in a shared file precisely because three pages render that
        markup and only this one used to carry the script.
        """
        self.assertIn("js/breseq_table.js", self.content())

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

    def _make_population(self):
        self.reseq.is_population = True
        self.reseq.save()

    def test_a_clonal_sample_has_no_frequency_column(self):
        """Asserted by header count, not by matching markup.

        This was assertNotIn("<th>freq</th>"), which after the headers were
        renamed passed for a population sample too -- green while testing nothing.
        """
        self.assertEqual(7, self.content().count("<th "))
        self.assertNotIn("Freq", self.content())

    def test_a_population_sample_has_one(self):
        self._make_population()
        content = self.content()
        self.assertEqual(8, content.count("<th "))
        self.assertIn('<th class="breseq-freq">Freq</th>', content)
        self.assertIn("100%", content)

    # --- the empty state ------------------------------------------------------

    def _empty_filter_params(self):
        """Ignore every gene, so the {% empty %} branch renders.

        Through the query string, which is how a reader sets their filter -- it used to write a
        shared `AleExperimentFilter` row, and there is no such row now.
        """
        return {"ignore_genes": ", ".join(
            Mutation.objects.values_list("gene", flat=True).distinct())}

    def test_the_empty_message_spans_exactly_the_columns_rendered(self):
        """The colspan was hardcoded to 8 while a clonal sample has 7 columns,
        because Freq is conditional -- so the message ran past the table.

        Compared against the headers actually rendered rather than against 7 and 8,
        so adding a ninth column fails here instead of silently drifting again. The
        markup still states the count twice; this is the guard.
        """
        for population in (False, True):
            with self.subTest(population=population):
                self.reseq.is_population = population
                self.reseq.save()
                content = self.content(**self._empty_filter_params())

                self.assertIn("No mutations passed the current filters", content)
                headers = content.count("<th ")
                self.assertIn('colspan="%d"' % headers, content)

    def test_a_polymorphic_call_is_shaded(self):
        self.reseq.is_population = True
        self.reseq.save()
        observed = ObservedMutation.objects.order_by("id").first()
        observed.frequency = 0.42
        observed.save()

        content = self.content()
        self.assertIn("polymorphism_table_row", content)
        self.assertIn("42.0%", content)

    # --- evidence column ------------------------------------------------------

    def test_evidence_links_to_the_alignment_when_there_is_one(self):
        self.reseq.bam_stored = True
        self.reseq.save()
        observed = ObservedMutation.objects.order_by("id").first()
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
        response = self.client.get(PAGE, {"ale_experiment_id": self.experiment.id})
        self.assertNotContains(response, "breseq-table", status_code=200)

    def test_an_experiment_with_no_samples_says_so(self):
        self.client.force_login(User.objects.get(username="tester"))
        response = self.client.get(PAGE, {"ale_experiment_id": self.experiment.id})
        self.assertContains(response, "no resequencing samples")
