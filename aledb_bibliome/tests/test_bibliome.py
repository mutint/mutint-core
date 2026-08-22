import os
from django.test import TestCase

from django.contrib.auth.models import User
from aledb_experiment.models import AleExperiment, Instrument, Project
from datetime import datetime
from aledb_bibliome.models import Publication
from aledb_bibliome.publication import create_publication

__author__ = 'Muyao'


class TestBibliome(TestCase):

    def test_create_publication(self):
        self.current_location = os.path.dirname(os.path.realpath(__file__))
        self.user = User.objects.create(username="pphaneuf", password="test123",
                                        first_name="Patrick", last_name="Phaneuf", email="email@email.com",
                                        is_active=True, is_staff=True, date_joined=datetime.now())
        project = Project.objects.create(
            name="test_project", user=self.user, date=datetime.now(),
            status="In progress", is_public=False)
        # A publication only needs an experiment to hang off; this used to run a
        # whole breseq import to get one.
        AleExperiment.objects.create(
            name="test", person="Patrick", project=project,
            instrument=Instrument.objects.create(name="test_instrument"))
        expected_publication_count = 1
        create_publication("test_publication_journal", "aledb.org", 1)
        self.assertEqual(expected_publication_count, Publication.objects.all().count())
        self.assertEqual("test_publication_journal", Publication.objects.get(id=1).title)
        self.assertEqual("aledb.org", Publication.objects.get(id=1).url)