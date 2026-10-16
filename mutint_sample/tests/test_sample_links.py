"""breseq's report links are the sample box's, through `sample_link_registry`."""

from django.contrib.auth.models import User
from django.test import TestCase

from mutint_experiment.models import Population, Project
from mutint_experiment.views import _create_experiment
from mutint_sample.models import Sample
from mutint_sample.sample_links import breseq_report_links
from mutint_common.sample_link_registry import sample_links


class BreseqReportLinksTestCase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user("owner", password="test123")
        project = Project.objects.create(name="p", user=self.owner)
        experiment = _create_experiment(project, "e", self.owner)
        population = Population.objects.create(experiment=experiment, name="1")
        self.sample = Sample.objects.create(population=population, time_point=1, name="1",
                                            source_name="s1")

    def test_a_sample_with_no_report_gets_no_link(self):
        self.assertEqual([], breseq_report_links(self.sample))

    def test_the_report_and_its_summary_when_the_sample_has_one(self):
        self.sample.report_stored = True
        self.sample.save(update_fields=["report_stored"])
        urls = [url for _, url, _ in breseq_report_links(self.sample)]
        self.assertEqual(["/mutations/report/%d/" % self.sample.pk,
                          "/mutations/report/%d/?page=summary.html" % self.sample.pk], urls)

    def test_the_check_report_when_the_sample_has_that(self):
        self.sample.check_report_stored = True
        self.sample.save(update_fields=["check_report_stored"])
        self.assertEqual([("breseq check report", "/mutations/report/%d/check/" % self.sample.pk)],
                         [(label, url) for label, url, _ in breseq_report_links(self.sample)])

    def test_core_registers_them(self):
        """So the Mutations page's sample box draws them beside a plugin's links."""
        self.sample.report_stored = True
        self.sample.save(update_fields=["report_stored"])
        self.assertIn("/mutations/report/%d/" % self.sample.pk,
                      [link["url"] for link in sample_links(self.sample)])

    def test_the_mutations_page_links_the_report_from_the_sample_box_only(self):
        self.sample.report_stored = True
        self.sample.save(update_fields=["report_stored"])
        self.client.force_login(self.owner)
        content = self.client.get("/mutations/breseq?experiment_id=%d&sample_id=%d"
                                  % (self.sample.population.experiment_id, self.sample.pk)).content.decode()
        box = content[content.index('class="sample-inputs-links"'):content.index("</div>", content.index('class="sample-inputs-links"'))]
        self.assertIn('href="/mutations/report/%d/"' % self.sample.pk, box)
        self.assertNotIn('class="breseq-crosslink">breseq report', content)
