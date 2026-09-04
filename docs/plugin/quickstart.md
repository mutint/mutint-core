# Quickstart

A plugin from nothing to a page in an assembled project. It adds one thing — a page listing
an experiment's mutation types with counts — and touches no file in aledb-core.

The names: repository `aledb-yourthing`, app `aledb_yourthing`. See
[Repository structure](structure.md) for why they differ.

## 1. The repository

Beside the other repos in the suite, because submodule URLs here are relative paths that git
resolves against the parent's remote:

```bash
cd ~/src/aledb-refactor
mkdir -p aledb-yourthing/aledb_yourthing/{templates/yourthing,tests}
cd aledb-yourthing
git init
touch requirements.txt
touch aledb_yourthing/__init__.py aledb_yourthing/tests/__init__.py
```

## 2. The app

`aledb_yourthing/apps.py` — everything core learns about you:

```python
from django.apps import AppConfig


class YourThingConfig(AppConfig):
    name = 'aledb_yourthing'

    def ready(self):
        # Inside ready(), not at module scope: apps.py is imported while the app registry is
        # still populating.
        from django.urls import include, re_path
        from aledb_common.nav_registry import EXPERIMENT_SECTION, register_nav_item
        from aledb_common.plugin_registry import register_plugin_urlpatterns

        register_plugin_urlpatterns([
            re_path(r'^yourthing/', include('aledb_yourthing.urls')),
        ])
        register_nav_item('Your Thing', url_name='yourthing',
                          section=EXPERIMENT_SECTION)
```

`aledb_yourthing/urls.py`:

```python
from django.urls import re_path

from aledb_yourthing import views

urlpatterns = [
    re_path(r'^$', views.your_thing, name='yourthing'),
]
```

`aledb_yourthing/views.py`:

```python
import collections

import aledb_seq.views.common
from django.shortcuts import render

from aledb_common.util import get_user_context
from aledb_experiment.models import Experiment
from aledb_experiment.permissions import can_view_project
from aledb_filter.util import filtered_mutation_call_queryset
from aledb_filter.view_filter import get_view_filter
from aledb_seq.models import MutationCall

EXPERIMENT_PATH = "sample__tech_rep__isolate__flask__ale_id__ale_experiment"


def your_thing(request):
    context = get_user_context(request.user)
    try:
        experiment = aledb_seq.views.common.get_experiment(request)
    except Experiment.DoesNotExist:
        # Not an error: it is how the page opens before an experiment is chosen.
        return aledb_seq.views.common.no_experiment_selected(
            request, context, None, "your thing")
    except ValueError:
        return render(request, "403.html", context, status=403)

    if not can_view_project(request.user, experiment.project):
        return render(request, "403.html", context, status=403)

    # Through the reader's own filter -- see "Showing filtered data". `get_view_filter`
    # never returns None, and an unfiltered reader's filter changes nothing.
    queryset, ignored_genes = filtered_mutation_call_queryset(
        MutationCall.objects.filter(**{EXPERIMENT_PATH: experiment}),
        view_filter=get_view_filter(request, experiment.ale_id))

    counts = collections.Counter(
        queryset.values_list("mutation__mutation_type", flat=True))

    context.update({
        "experiment_id": experiment.ale_id,
        "experiment_name": experiment.name,
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        "title": "%s mutation types" % experiment.name,
        "counts": sorted(counts.items()),
    })
    return render(request, "yourthing/index.html", context)
```

`aledb_yourthing/templates/yourthing/index.html`:

```django
{% extends 'base.html' %}
{% block title %}{{ title }}{% endblock %}
{% block header %}<b>{{ ale_project_name }}: {{ experiment_name }}</b> - Your Thing{% endblock %}

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
git add -A && git commit -m "feat: aledb-yourthing"
```

## 3. Install it

```bash
cd ../mutint
git -c protocol.file.allow=always submodule add ../aledb-yourthing aledb-yourthing
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

`aledb_yourthing/tests/test_page.py`:

```python
from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import Experiment


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
            "/yourthing/", {"experiment_id": self.experiment.ale_id})

        self.assertEqual(200, response.status_code)
        self.assertIn("no mutations stored", response.content.decode())

    def test_it_registers_a_nav_entry(self):
        from aledb_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

        labels = [item["label"] for item in get_nav_items(EXPERIMENT_SECTION)]

        # Assert your own entry. Never assert on what is *absent* from a shared registry --
        # that is a statement about the deployment, not about this plugin.
        self.assertIn("Your Thing", labels)
```

Run it — **from the assembled project, not from aledb-core**:

```bash
cd mutint
./mutint test aledb_yourthing
```

From aledb-core this finds nothing and reports `Ran 0 tests ... OK`. See
[Testing](testing.md).

## Next

- Storing something computed: [Models and derived data](derived-data.md)
- Writing, not just reading: [URLs, views and permissions](views-and-urls.md) — a plugin that
  writes has an obligation core cannot enforce for it
- Shipping data that demonstrates it: [Packaging](packaging.md)
