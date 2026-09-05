"""Every registered admin changelist renders.

`MediaAdmin.list_display` named `experiments`, a method that filtered `TimePoint` on a `project`
field `TimePoint` did not have. It raised `FieldError` on every render, so /admin/…/media/ had
been a 500 rather than a page -- and nothing noticed, because a `list_display` callable is
only ever called by the admin's own rendering and no test rendered it.

So the test is the general shape of that bug rather than the one instance: walk what the site
has registered and ask each changelist for a page. A callable added to some future
`list_display` that raises is then a failing test rather than a page nobody visits.
"""

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from mutint_experiment.models import (
    Experiment, UserGroup, Population, Project, ProjectAccess,
)
from mutint_experiment.roles import ROLE_OWNER


class AdminChangelistTestCase(TestCase):
    """Every registered model gets a row, which is the whole point.

    An empty changelist renders 200 no matter what `list_display` names, because a
    `list_display` callable is only ever called once per row -- so a fixture-less version of
    this test passes against the very bug it was written for. Each model below therefore has
    at least one instance before the sweep runs.
    """

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="root", email="r@e.com", password="x")
        self.client.force_login(self.superuser)

        self.project = Project.objects.create(name="P", user=self.superuser)
        ProjectAccess.objects.create(
            project=self.project, user=self.superuser, role=ROLE_OWNER)
        experiment = Experiment.objects.create(name="E", project=self.project)
        Population.objects.create(experiment=experiment, name="1")
        UserGroup.objects.create(name="G", owner=self.superuser)

    def test_fixture_covers_every_registered_model(self):
        """Guards the fixture itself: a model registered later gets no row from setUp, and
        its changelist is then back to proving nothing."""
        empty = [model.__name__ for model in self.registered_models()
                 if not model._default_manager.exists()]
        self.assertEqual(empty, [], "no row in the fixture for these admin models")

    def registered_models(self):
        """This suite's admins, not Django's or a dependency's.

        `admin.site._registry` also carries `auth.Group` and django-tasks-db's
        `DBTaskResult`, whose rendering is their maintainers' problem and whose fixtures
        would be this test's. Everything under an `mutint_*` app is ours.
        """
        return [model for model in admin.site._registry
                if model._meta.app_label.startswith("mutint")]

    def test_every_registered_changelist_renders(self):
        rendered = 0
        for model in self.registered_models():
            url = reverse("admin:%s_%s_changelist" % (
                model._meta.app_label, model._meta.model_name))
            with self.subTest(model=model.__name__):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
            rendered += 1
        # A registry that silently emptied would pass every subTest above.
        self.assertGreater(rendered, 1)
