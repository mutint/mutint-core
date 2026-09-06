# URLs, views and permissions

## Mounting your pages

```python
from django.urls import include, re_path
from mutint_common.plugin_registry import register_plugin_urlpatterns

register_plugin_urlpatterns([
    re_path(r'^yourthing/', include('mutint_yourthing.urls')),
])
```

Core's `get_core_urlpatterns()` appends whatever has been registered, so an assembled project
needs no edit to `config/urls.py` when a plugin is added.

**Pick a prefix nothing else owns.** Django does not backtrack out of a matched `include()`,
so if you mount at a prefix core already matches, your patterns are unreachable and the
symptom is a 404 rather than an error. Core's occupied prefixes include `mutations/`,
`mutation-table/`, `curate/`, `ale/`, `import/`, `filter/`, `stats`, `export/`,
`search/`, `dashboard`, `about`.

## The house view style

Every page in this codebase is a function-based view that checks its own permission and
renders a template. There are no `Form` classes, no class-based views and no DRF. A write is a
separate `@require_POST` endpoint returning JSON, called from the page with `mutintPost`.

The split matters:

- The **GET page** checks permission and renders `403.html` with a 403 status if refused,
  because people reach URLs directly.
- The **POST endpoint** checks again, because the button being hidden is not a permission
  check.

## Permissions

Authorization is `mutint_experiment/permissions.py` and nothing else. Four ordered roles are
granted on a **project**: `read < write < admin < owner`. Nothing below the project is owned;
an experiment, a sample and a mutation are all reached through `experiment.project`.

For reading:

```python
from mutint_experiment.permissions import can_view_project

if not can_view_project(request.user, experiment.project):
    return render(request, "403.html", context, status=403)
```

For writing, there is an obligation core cannot meet for you:

!!! danger "Ask `can_edit_experiment`, not `can_edit_project`"

    ```python
    from mutint_experiment.permissions import can_edit_experiment

    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "..."}, status=403)
    ```

    An experiment can be **locked**, and a lock outranks every role — a locked experiment
    refuses every web write from everyone, admins, owners and superusers included. The flag
    lives on the experiment, so `can_edit_project(user, experiment.project)` cannot see it
    and will happily authorise a write into a dataset somebody deliberately closed.

    Core's own write paths all ask the right question, and so does `mutint-phylogeny`, which
    is the plugin to copy. Rebuilds are deliberately exempt: derived data should keep up with
    a locked experiment rather than go stale.

`experiment_lock_refusal(experiment)` returns a sentence naming why and who can lift it, or
`None` when the lock is not the reason — worth using, because "you may not edit this" and
"nobody may edit this at the moment" are different answers and only one is actionable.

The role cache in `permissions.py` is not an optimisation you can ignore: a mutation table
asks the same question once per sample column and once per mutation row. If you write a view
that loops, ask through the same helpers rather than querying `ProjectAccess` yourself.

## Getting the experiment

Core's pages take `?experiment_id=` and resolve it through
`mutint_sample.views.common.get_experiment(request)`, which raises
`Experiment.DoesNotExist` when none was selected — a normal state, not an error, and
`no_experiment_selected()` renders the page that explains it — and a bare `ValueError` when
the caller may not view it.

Catch them separately. Rendering `500.html` for the second tells a reader the site broke when
in fact they were refused.

## A nav entry

```python
from mutint_common.nav_registry import EXPERIMENT_SECTION, MAIN_SECTION, register_nav_item

register_nav_item('Your Thing', url_name='yourthing', section=EXPERIMENT_SECTION)
```

`MAIN_SECTION` is always shown. `EXPERIMENT_SECTION` appears only when an experiment is
selected and is rendered with `?experiment_id=` appended, in both the sidebar and the
header button bar.

Pass `url=` (a literal path) or `url_name=` (reversed at render time), never both. Prefer
`url_name`: an entry whose name will not reverse is skipped rather than rendered dead, so a
half-installed plugin cannot leave a broken link. Core's own entries use literal paths only
because several of them rely on `APPEND_SLASH` redirects that reversing would change.
