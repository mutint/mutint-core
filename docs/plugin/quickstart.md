# Quickstart

A plugin from nothing to a page in an assembled project. It adds one thing — a page listing
an experiment's mutation types with counts — and touches no file in mutint-core.

The names: repository `mutint-yourthing`, app `mutint_yourthing`. See
[Repository structure](structure.md) for why they differ.

## 1. The repository

Beside the other repos in the suite, because submodule URLs here are relative paths that git
resolves against the parent's remote:

```bash
cd ~/src/mutint-code
mkdir -p mutint-yourthing/mutint_yourthing/{templates/yourthing,tests}
cd mutint-yourthing
git init
touch requirements.txt
touch mutint_yourthing/__init__.py mutint_yourthing/tests/__init__.py
```

## 2. The app

`mutint_yourthing/apps.py` — everything core learns about you:

```python
from django.apps import AppConfig


class YourThingConfig(AppConfig):
    name = 'mutint_yourthing'

    def ready(self):
        # Inside ready(), not at module scope: apps.py is imported while the app registry is
        # still populating.
        from django.urls import include, re_path
        from mutint_common.nav_registry import EXPERIMENT_SECTION, register_nav_item
        from mutint_common.plugin_registry import register_plugin_urlpatterns

        register_plugin_urlpatterns([
            re_path(r'^yourthing/', include('mutint_yourthing.urls')),
        ])
        register_nav_item('Your Thing', url_name='yourthing',
                          section=EXPERIMENT_SECTION)
```

`mutint_yourthing/urls.py`:

```python
from django.urls import re_path

from mutint_yourthing import views

urlpatterns = [
    re_path(r'^$', views.your_thing, name='yourthing'),
]
```

`mutint_yourthing/views.py`:

```python
import collections

import mutint_sample.views.common
from django.shortcuts import render

from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_view_project
from mutint_filter.util import filtered_mutation_call_queryset
from mutint_filter.view_filter import get_view_filter
from mutint_experiment import paths
from mutint_sample.models import MutationCall

# The ORM path from a MutationCall up to its experiment. Spelled by
# `mutint_experiment.paths` rather than by hand: it is `sample__population__experiment`
# today, and it has changed twice.
EXPERIMENT_PATH = paths.to_experiment(paths.FROM_CALL)


def your_thing(request):
    context = get_user_context(request.user)
    try:
        experiment = mutint_sample.views.common.get_experiment(request)
    except Experiment.DoesNotExist:
        # Not an error: it is how the page opens before an experiment is chosen.
        return mutint_sample.views.common.no_experiment_selected(
            request, context, None, "your thing")
    except ValueError:
        return render(request, "403.html", context, status=403)

    if not can_view_project(request.user, experiment.project):
        return render(request, "403.html", context, status=403)

    # Through the reader's own filter -- see "Showing filtered data". `get_view_filter`
    # never returns None, and an unfiltered reader's filter changes nothing.
    queryset, ignored_genes = filtered_mutation_call_queryset(
        MutationCall.objects.filter(**{EXPERIMENT_PATH: experiment}),
        view_filter=get_view_filter(request, experiment.id))

    counts = collections.Counter(
        queryset.values_list("mutation__mutation_type", flat=True))

    context.update({
        "experiment_id": experiment.id,
        "experiment_name": experiment.name,
        "project_name": experiment.project.name if experiment.project else "",
        "project_id": experiment.project_id,
        "title": "%s mutation types" % experiment.name,
        "counts": sorted(counts.items()),
    })
    return render(request, "yourthing/index.html", context)
```

`mutint_yourthing/templates/yourthing/index.html`:

```django
{% extends 'base.html' %}
{% block title %}{{ title }}{% endblock %}
{% block header %}<b>{{ project_name }}: {{ experiment_name }}</b> - Your Thing{% endblock %}

{% block content %}
    <table class="table">
        <thead><tr><th>Type</th><th>Calls</th></tr></thead>
        <tbody>
        {% for type, count in counts %}
            <tr><td>{{ type }}</td><td>{{ count }}</td></tr>
        {% empty %}
            <tr><td colspan="2">This experiment has no mutations stored.</td></tr>
        {% endfor %}
        </tbody>
    </table>
{% endblock %}
```

Commit it:

```bash
git add -A && git commit -m "feat: mutint-yourthing"
```

## 3. Install it

```bash
cd ../mutint
git -c protocol.file.allow=always submodule add ../mutint-yourthing mutint-yourthing
./mutint check
```

Nothing in the assembled project was edited. `config/settings.py` reads `.gitmodules`,
discovers your app, and `AppConfig.ready()` does the rest.

## 4. See it

```bash
./mutint start
```

Pick an experiment; **Your Thing** is at the end of the experiment section of the sidebar,
after the other plugins, because nav order is `INSTALLED_APPS` order.

## 5. A test

`mutint_yourthing/tests/test_page.py`:

```python
from django.contrib.auth.models import User
from django.test import TestCase

from mutint_experiment.models import Experiment


class YourThingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        # Through the view, not Project.objects.create: access is granted on the project, and
        # an experiment nobody owns can be viewed by nobody.
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

    def test_the_page_renders(self):
        response = self.client.get(
            "/yourthing/", {"experiment_id": self.experiment.id})

        self.assertEqual(200, response.status_code)
        self.assertIn("no mutations stored", response.content.decode())

    def test_it_registers_a_nav_entry(self):
        from mutint_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

        labels = [item["label"] for item in get_nav_items(EXPERIMENT_SECTION)]

        # Assert your own entry. Never assert on what is *absent* from a shared registry --
        # that is a statement about the deployment, not about this plugin.
        self.assertIn("Your Thing", labels)
```

Run it — **from the assembled project, not from mutint-core**:

```bash
cd mutint
./mutint test mutint_yourthing
```

From mutint-core this finds nothing and reports `Ran 0 tests ... OK`. See
[Testing](testing.md).

## Next

- Storing something computed: [Models and derived data](derived-data.md)
- Writing, not just reading: [URLs, views and permissions](views-and-urls.md) — a plugin that
  writes has an obligation core cannot enforce for it
- Shipping data that demonstrates it: [Packaging](packaging.md)
