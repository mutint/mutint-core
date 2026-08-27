# Repository structure

Every existing plugin has the same shape, and it is worth following because the assembly
machinery assumes parts of it.

```
aledb-yourthing/                 <- the git repository
├── aledb_yourthing/             <- exactly one Django app package
│   ├── __init__.py
│   ├── apps.py                  <- the AppConfig, and every registration
│   ├── urls.py                  <- your URL patterns
│   ├── views.py
│   ├── util.py                  <- the work, kept out of the views
│   ├── models.py                <- only if you store something
│   ├── migrations/
│   ├── templates/
│   │   └── yourthing/           <- namespaced by app, not flat
│   ├── static/
│   │   └── aledb_yourthing/     <- namespaced by app, not flat
│   ├── examples/                <- optional, see Packaging
│   └── tests/
├── requirements.txt             <- even if empty; the entry script looks for it
└── tools.txt                    <- optional, non-Python tools
```

## The two names

The **repository** is `aledb-yourthing`, with a hyphen. The **app package** inside it is
`aledb_yourthing`, with an underscore. They are not interchangeable and the difference causes
one of the more confusing failures in this codebase:

!!! warning "A submodule directory can never be a Python package"

    An assembled project puts each submodule directory on `sys.path`, which is how
    `aledb_yourthing` becomes importable. The submodule directory itself —
    `aledb-yourthing` — has a hyphen, so it can never be a package, and `unittest`
    discovery can never descend into it. That is why a bare `./mutint test` cannot find
    plugin tests by discovery and has to be told what to run. See
    [Testing](testing.md).

    Renaming the directory would not help. Giving a submodule root an `__init__.py` would
    make every app importable by two dotted paths at once, and `aledb_yourthing.models`
    and `aledb_core.aledb_yourthing.models` are two module objects with two sets of model
    classes.

## One app per repository

Nothing enforces it, and every existing plugin follows it. An assembled project discovers apps
by scanning each submodule directory for packages holding both `__init__.py` and `apps.py`, so
two apps in one repository would both be installed, always together, with no way for a
deployment to take one. If they are genuinely separable, they are two repositories.

## `apps.py` is the whole interface

Everything core learns about your plugin, it learns from `AppConfig.ready()`. That makes it
the first file to read in an unfamiliar plugin and the file to keep legible.

```python
from django.apps import AppConfig


class YourThingConfig(AppConfig):
    name = 'aledb_yourthing'

    def ready(self):
        from django.urls import include, re_path
        from aledb_common.nav_registry import EXPERIMENT_SECTION, register_nav_item
        from aledb_common.plugin_registry import register_plugin_urlpatterns

        register_plugin_urlpatterns([
            re_path(r'^yourthing/', include('aledb_yourthing.urls')),
        ])
        register_nav_item('Your Thing', url_name='yourthing',
                          section=EXPERIMENT_SECTION)
```

**Import inside `ready()`, not at module scope.** `apps.py` is imported while the app registry
is still populating, and a module-level import of anything that touches models raises
`AppRegistryNotReady`. Every existing plugin does its imports inside the method for this
reason.

**`ready()` can run more than once.** Django calls it once per process, but a test that
reloads apps, or a management command that calls `django.setup()` again, will call it twice.
Registration functions that would be wrong to repeat say so in their reference pages —
`register_rebuilder` raises on a duplicate name, deliberately, because two rebuilds sharing a
name would share one staleness row.

## `util.py` and the views

The convention across all four plugins: `views.py` resolves the request, checks permission and
renders; `util.py` does the work and takes plain arguments. It is not architecture for its own
sake — a plugin's real logic usually needs to be callable from a management command, a rebuild
hook and a test, none of which have a request.
