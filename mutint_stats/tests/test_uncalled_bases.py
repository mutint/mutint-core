"""The Overview reports uncalled *bases*, and what share of the reference they are.

The column used to be a count of `UncalledRegion` rows, which said nothing about how much
of the genome was actually missing: one region can be a single base or a megabase.

The share needs `ReferenceSequences.total_length`, which defaults to 0 and is absent
entirely for an experiment whose reference was never established -- so "unknown" is a state
this has to have an answer for, and the answer is to print no percentage rather than 0%.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from mutint_experiment.models import Experiment, Population, Project
from mutint_sample.models import ReferenceSequences, Sample, UncalledRegion
from mutint_stats.util import get_sample_info_list, uncalled_bases_per_sample


class UncalledBasesTestCase(TestCase):

    def setUp(self):
        user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(user)
        project = Project.objects.create(name="P", user=user)
        from mutint_experiment.models import ProjectAccess
        from mutint_experiment.roles import ROLE_OWNER
        ProjectAccess.objects.create(project=project, user=user, role=ROLE_OWNER)
        self.experiment = Experiment.objects.create(name="E", project=project)
        self.population = Population.objects.create(experiment=self.experiment, name="1")
        self.sample = Sample.objects.create(
            population=self.population, time_point=500, name="1", source_name="s")

    def region(self, start, end, seq_id="REL606", sample=None):
        return UncalledRegion.objects.create(
            sample=sample or self.sample, seq_id=seq_id, start=start, end=end)

    def reference(self, total_length):
        return ReferenceSequences.objects.create(
            experiment=self.experiment, gff3_sha256="a", fasta_sha256="b",
            total_length=total_length)

    def rows(self):
        return get_sample_info_list(Sample.objects.filter(pk=self.sample.pk))

    # --- the sum -------------------------------------------------------------------------

    def test_bounds_are_inclusive(self):
        """A region from 100 to 100 is one base. `_is_uncovered` asks
        `start <= position <= end`, so the endpoints are covered and the length is
        `end - start + 1`."""
        self.region(100, 100)
        self.assertEqual({self.sample.pk: 1}, uncalled_bases_per_sample([self.sample.pk]))

    def test_regions_are_summed(self):
        self.region(100, 199)          # 100 bases
        self.region(1000, 1009)        # 10
        self.assertEqual({self.sample.pk: 110},
                         uncalled_bases_per_sample([self.sample.pk]))

    def test_regions_on_different_sequences_are_summed_together(self):
        """A reference with a plasmid has more than one sequence, and the figure is for the
        genome rather than for one contig."""
        self.region(1, 10, seq_id="REL606")
        self.region(1, 5, seq_id="plasmid")
        self.assertEqual({self.sample.pk: 15},
                         uncalled_bases_per_sample([self.sample.pk]))

    def test_a_sample_with_no_regions_is_absent_rather_than_zero(self):
        """The caller defaults it; the query has no row to return."""
        self.assertEqual({}, uncalled_bases_per_sample([self.sample.pk]))
        self.assertEqual(0, self.rows()[0]["uncalled_bases"])

    def test_each_sample_is_counted_separately(self):
        other = Sample.objects.create(population=self.population, time_point=500,
                                      name="2", source_name="t")
        self.region(1, 10)
        self.region(1, 100, sample=other)
        self.assertEqual({self.sample.pk: 10, other.pk: 100},
                         uncalled_bases_per_sample([self.sample.pk, other.pk]))

    # --- the share -----------------------------------------------------------------------

    def test_the_percentage_is_of_the_reference_length(self):
        self.reference(total_length=1000)
        self.region(1, 250)

        row = self.rows()[0]
        self.assertEqual(250, row["uncalled_bases"])
        self.assertAlmostEqual(25.0, row["uncalled_percent"])

    def test_no_reference_means_no_percentage(self):
        """None, not 0.0. An experiment whose reference length is unknown has no answer
        here, and 0% beside a real base count would claim the genome is entirely called."""
        self.region(1, 250)

        row = self.rows()[0]
        self.assertEqual(250, row["uncalled_bases"])
        self.assertIsNone(row["uncalled_percent"])

    def test_a_reference_of_unknown_length_means_no_percentage_either(self):
        """`total_length` defaults to 0, so a row can exist and still not answer."""
        self.reference(total_length=0)
        self.region(1, 250)

        self.assertIsNone(self.rows()[0]["uncalled_percent"])

    # --- what the page shows -------------------------------------------------------------

    def test_the_page_shows_the_bases_and_the_share(self):
        self.reference(total_length=4_600_000)
        self.region(1, 46_000)

        html = self.client.get("/stats?experiment_id=%d" % self.experiment.id,
                               follow=True).content.decode()

        self.assertIn("46,000", html, "grouped, because these run to millions")
        self.assertIn("(1.00%)", html)

    def test_the_page_omits_the_share_when_it_cannot_be_computed(self):
        self.region(1, 46_000)

        html = self.client.get("/stats?experiment_id=%d" % self.experiment.id,
                               follow=True).content.decode()
        cell = html.split('class="uncalled_bases"')[1][:160]

        self.assertIn("46,000", cell)
        self.assertNotIn("%", cell)
