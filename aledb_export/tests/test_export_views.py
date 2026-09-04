"""Both export endpoints answer with a zip.

`aledb_export` had no tests, and both of its views were raising AttributeError on every
request: they selected experiments with `str(exp.ale_id)`, and `Experiment`'s primary key
stopped being called `ale_id` when it was renamed to the implicit `id`. `paths.EXPERIMENT_PK`
exists so that a rename of that column survives in lookup *strings*; an attribute access is
exactly what it cannot reach, which is why the rename swept the queries and left these four.

So what is asserted here is thin on purpose -- a status and a filename, not a format. The
failure this guards is a page that does not answer at all, and the only reason it went
unnoticed for a release is that nothing ever asked it to.
"""

import csv
import io
import zipfile

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import Experiment, Population, TimePoint, Media, Project
from aledb_experiment.roles import ROLE_OWNER
from aledb_seq.models import Mutation, MutationCall, Sample


class ExportViewTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="P", user=self.user)
        from aledb_experiment.models import ProjectAccess
        ProjectAccess.objects.create(project=self.project, user=self.user, role=ROLE_OWNER)
        self.experiment = Experiment.objects.create(name="E", project=self.project)

        ale = Population.objects.create(experiment=self.experiment, name="1")
        flask = TimePoint.objects.create(population=ale, value=500,
                                     media=Media.objects.create(description="M9"))
        sample = Sample.objects.create(
            time_point=flask, name="1-1", is_clonal=True, source_name="1-500-1-1")
        mutation = Mutation.objects.create(
            experiment=self.experiment, mutation_type="SNP", position=150,
            sequence_change="T", gene="thrA", reseq_reference="SYN001")
        MutationCall.objects.create(sample=sample, mutation=mutation,
                                        frequency=1.0)

    def _zip(self, response):
        data = (b"".join(response.streaming_content)
                if getattr(response, "streaming", False) else response.content)
        return zipfile.ZipFile(io.BytesIO(data))

    def test_the_mutation_export_answers_a_zip(self):
        response = self.client.get(
            "/export/?project_id=%d&experiment_ids=%d&mut_type=mut"
            % (self.project.id, self.experiment.id))

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/zip", response["Content-Type"])
        self.assertEqual(1, len(self._zip(response).namelist()))

    def test_the_experiment_index_answers_a_csv_of_the_experiments(self):
        """A bare CSV, not a zip -- the other endpoint bundles a file per experiment and
        this one is a single table."""
        response = self.client.get(
            "/export/experiment_index?project_id=%d&experiment_ids=%d&mut_type=mut"
            % (self.project.id, self.experiment.id))

        self.assertEqual(200, response.status_code)
        self.assertEqual("text/csv", response["Content-Type"])

        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(2, len(rows), "a header and one experiment")
        # The first column is the experiment's primary key. Its *header* still reads
        # `ale_id`, which is the name that column had before the rename; changing a
        # published header is its own decision and not this fix's.
        self.assertEqual(str(self.experiment.id), rows[1][0])
        self.assertEqual("E", rows[1][1])

    def test_an_experiment_id_that_matches_nothing_is_refused_not_raised(self):
        """The failure mode this whole file exists for: selection is by
        `Experiment.id`, and getting the attribute wrong made every request a 500
        rather than a refusal."""
        for path in ("/export/", "/export/experiment_index"):
            with self.subTest(path=path):
                response = self.client.get(
                    "%s?project_id=%d&experiment_ids=%d&mut_type=mut"
                    % (path, self.project.id, self.experiment.id + 9999))
                self.assertEqual(400, response.status_code)
