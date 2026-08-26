# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ALEdb is a Django 5 web application for managing Adaptive Laboratory Evolution (ALE) experiments. It stores experimental data, parses genomic sequencing output (breseq `.gd` files), and provides analysis tools for mutations, convergence, and enrichment.

### Role of this repo

`aledb-core` serves two purposes:

1. **Standalone app** — run directly from this repo for a self-contained ALEdb instance (SQLite, local dev via `./aledb start`).
2. **Git submodule** — embedded in an assembled project (e.g. `mutint`) that adds custom Django apps without modifying aledb-core. The assembled project provides its own `config/` package (settings, URLs, wsgi) and uses helpers from `aledb_common` to inherit core settings and URL patterns:
   - `aledb_common.base_settings.get_base_settings()` — returns all core settings as a dict
   - `aledb_common.urls.get_core_urlpatterns()` — returns all core URL patterns

See `DEVELOPER.md` for the full submodule integration guide.

## Commands

**Local dev setup** (SQLite, no MySQL or Docker needed):
```bash
./aledb start             # first run: auto-creates a venv at env/main and installs
                          #   requirements, then re-execs under it; runs migrations,
                          #   creates admin user, opens browser, starts server
./aledb install           # (re)install deps into env/main without starting the server
```

`./aledb` bootstraps its own virtualenv at `env/main/` (sentinel `env/main/.installed`)
and re-execs under `env/main/bin/python` — no manual `python -m venv` / `activate` needed.
This mirrors mutint's `./mutint` entry script. `env/` is git-ignored.

**Run all tests**:
```bash
./aledb test
```

**Run a single test**:
```bash
./aledb test aledb_import.tests.test_ale_experiment.TestEnrichment.test_reseq_URL
```

**Coverage**:
```bash
coverage run ./aledb test && coverage report
```

### Testing notes

**The suite is fast: ~13 seconds for the whole thing.** Per-app it is 0-8s; most of that is
Django starting up, not the tests. The test database is in-memory SQLite, so runs do not
contend for a file and can be repeated freely.

**If a test run appears to hang, it is almost certainly not the tests.** Two things cause it:

1. *Chaining the run onto slow setup.* `patch files && migrate && ./aledb test` can blow a
   command timeout in the earlier steps, and the run looks stuck when it never really started.
   Run `./aledb test` as its own command.
2. *Killing your own shell.* `ps aux | grep "[z]sh -c source" | xargs kill` matches the wrapper
   of the command currently running it, so it kills itself and exits 144. If you want to clear
   a genuinely orphaned run, match on the Python process (`pkill -f "django test"`) instead.

**Baseline: 852 run, 0 failures** standalone; **965** in an assembled project, where the
plugins' own tests join them. The suite is green — treat *any* failure as yours. (These said
642 and 688 for a while and were wrong by more than the sharing work added — standalone was
already 698 before it. Re-count rather than adjusting the number by what you think you
added.)

**A bare `test` runs the installed first-party apps, not whatever discovery finds.**
`aledb_common/test_runner.py` substitutes them when no labels are given. Standalone this
changes nothing; in an assembled project it is the difference between running the suite and
not. `./mutint test` used to report `Ran 0 tests ... OK` — unittest discovery walks the
working directory, and an assembled project's code lives in submodule directories named
`aledb-core`, `aledb-compare` and so on, which can never be Python packages, so discovery
could not descend into them however they were laid out. Renaming them would not have helped:
a directory is skipped unless it holds an `__init__.py`, and giving one to a submodule root
would make every app importable by two dotted paths at once — `aledb_seq.models` and
`aledb_core.aledb_seq.models` are two module objects, which means two sets of model classes.

The app set comes from `about_registry.first_party_app_configs()`, the same predicate the
About page inventories with, so "which apps are ours" is stated once.

**Core's tests must not assert on what is *absent* from a shared registry.** Nav entries,
About sections and export types are contributed by whatever is installed, so
`assertNotIn("Compare", nav_labels)` is a statement about the install set, not about core —
it passes standalone and fails the moment a plugin is added. Assert core's own registrations,
and leave a plugin's to the plugin. Three tests said otherwise and were wrong; the About one
now counts entries per *checkout*, which is the invariant it always meant.

It was not green for years. The last six were all in
`aledb_metadata.tests.test_metadata.TestParser` and all dated to two 2019 commits that changed
code without migrating what depended on it: `14966400` switched the metadata schema to short
keys (`A`/`F`/`I`/`R`) plus JSON blobs but migrated only the `test3/` fixtures, and `19dfc7a9`
stopped the parser writing `Media.substrate` while the tests kept asserting on it.

### Gotchas when writing tests

- **Metadata is validated on CLI upload.** `aledb_metadata/xpmdvalidator/validate.py` used to
  open with an unconditional `return True`, so every metadata directory passed. It is live now:
  a fixture that does not satisfy `Json_schema.json` will fail
  `_check_and_extract_parameters_from_metadata` and the upload returns early. Build metadata
  fixtures from `aledb_metadata/tests/test3/`, which is the canonical shape.
- **`find_user()` prompts on stdin.** Anything reaching `try_creating_project` with a person
  name that matches no `User` raises `EOFError` under the test runner. Create the `User` first,
  or use `gd_import.prepare_experiment_by_id`, which never resolves a person.
- **`Project.objects.create()` is enough now, and used to not be.** `can_view_project` read
  the django-guardian grant and never `Project.user`, so a project could name an owner who
  could not open it, and five test modules carried a `POST /ale/projects/create/` workaround
  for it. `effective_role` reads `Project.user` directly, so that trap is gone. Call
  `set_primary_owner(project, user)` when you want the grant row as well — it is what the
  create view, the CLI importer and `load_example` all use.
- **Access is explicit; staff have no blanket read.** `can_view_project` used to end
  `return bool(user.is_staff)`. A test that gives someone `is_staff=True` and expects them to
  see a project is asserting the old behaviour.
- **Override the store.** Anything touching `ALEDB_STORE_DIR` needs
  `override_settings(ALEDB_STORE_DIR=tempfile.mkdtemp())`, or tests write into the repo.
- **Template content outside a `{% block %}` is silently discarded** in a child template. A
  `<script>` appended after `{% endblock %}` never renders and no error is raised — assert on
  the rendered HTML rather than trusting the file.
- **A soft-deleted row is still in `objects`.** `objects` is deliberately unfiltered; assert
  through `aledb_experiment.models.live()` or the list helpers when checking visibility.

**Django management commands** (local):
```bash
./aledb shell
./aledb makemigrations && ./aledb migrate
./aledb upload path1 path2   # upload ALE experiments
./aledb delete 4 20 19       # delete experiments by ID
./aledb collectstatic
```

Migrations are tracked in version control. Commit the files generated by
`./aledb makemigrations` alongside the model changes that produced them.

**There is no Docker in this repo.** This section used to give
`docker-compose -f docker-compose-prod-asgi-host-nginx.yml up`; that file does not exist here
and neither does any other compose file, Dockerfile or Procfile. It described the pre-refactor
deployment. See **Infrastructure (production)** below.

## Architecture

### The shell's two widths

Neither the sidebar nor the content box has a fixed width, and neither should get one back.

- `.sidebar` is `width: max-content` when open (set inline in `base.html`, and by
  `toggle_sidebar`), so it is as wide as its widest entry plus the 15px `.nav > li > a` padding
  either side. `max-width: 17vw` -- what the fixed width used to be -- keeps a long experiment
  name from pushing the page over; `overflow-x: hidden` trims it instead. Collapsed, the inline
  width is cleared and `.sidebar`'s `width: 0` is what remains.
- `#aledb-content` is `display: flow-root` and fills whatever is left beside the sidebar and the
  collapse strip. It used to be `float: left; width: 77vw` beside a 17vw sidebar, with
  `toggle_sidebar` swapping in a second guess of 95vw; neither added up, and several vw of every
  page went unused down the right-hand side. With the sidebar sized to its own content, any
  fixed width would be wrong by a different amount again.

`flow-root` rather than `overflow: hidden`: both establish the block formatting context that
stops the box sliding under the floats, but `hidden` would clip a menu that opens past the edge
-- the genome browser's sample menu is one.

**The title and the page's content share one left edge, at `#aledb-content`'s 25px padding.**
Two things used to break that, and they broke it in opposite directions, which is why the
misalignment looked inconsistent rather than uniform:

- The header block sits in a Bootstrap `.col-lg-12`, and a grid column carries a 15px gutter
  each side -- so the title alone stood 15px further in.
- A `.row` placed straight into `#aledb-content` bleeds 15px *out*, because its -15px margins
  are meant to be cancelled by the 15px padding of a `.container`, and this box is not one --
  it has 25px of its own. Most pages use `.row` as a plain wrapper round a form or a table,
  so their content sat left of the title while the edit pages, which use no row, sat flush.
  The Add Data page manages the same trick one level up with a bare `.col-lg-8`.

`common.css` flattens the gutters that have nothing to cancel them: the header's column, a
row that is a direct child, and a column used outside a row. A genuine multi-column row keeps
its inter-column gutters -- only the outermost edges go -- so the Overview page's three panels
still breathe. Measured across twelve pages with headless Chrome, all now at the same x.

**The paginate control lays its buttons out with flex, not floats.** Bootstrap 3 builds
`.pagination` as an inline-block `ul` whose `li`s are `display: inline` and whose `a`s are
floated, and the shrink-to-fit width that gives the `ul` came out one button short -- so
**"Last" sat on a second line** on every page with a paged table, at any window width, with
any amount of empty space to its right. It is not a width problem and moving things around
does not fix it; `display: inline-flex` on the `ul` sizes the row to its contents and takes
the float arithmetic out of it.

Worth knowing because it looks like a consequence of whatever was last changed nearby, and
is not: it was measured identical before and after the alignment rules above.

**That `display: flow-root` is set inline in `base.html`, not in `common.css`, and must stay
there.** It is the whole of what keeps the content box off the sidebar, so a browser holding a
cached older copy of `common.css` renders every page with the header on top of the sidebar --
which is exactly what happened when the rule lived only in the stylesheet. Layout this
load-bearing ships with the markup that assumes it.

For the same reason aledb-core's own CSS and JS are linked with `?v={{ aledb_version }}`. A
release changes every one of those URLs, so a browser cannot serve half of one version and half
of another. Third-party CDN assets are already versioned in their paths.

`common.css` also trims the header block on every page: `.page-header`'s 40px top margin (and
the `h2`'s own, which collapses through it, since `.page-header` has no top padding or border),
its padding and margin below, and the empty `<h2>{{ error }}</h2>` slot in `base.html`, which
has no height but still contributes Bootstrap's 20px `h2` margin. That leaves 25px above the
title -- the same padding the page has at its foot -- and 16px below the header. `:empty` rather
than hiding the error heading outright, so a real error still shows.

Menus that run as long as the experiment has samples carry `.aledb-menu`, which is only
`max-height` and `overflow-y`: Bootstrap's `.dropdown-menu` already gives these menus everything
else they share with the Metadata page's column menu.

### Branding: aledb-core has none

`/` is the project list, the sidebar carries no name or version, there is no icon upper-right,
and no institution is credited. All of that was ALEdb's and now lives in the `aledb-deploy`
repo. A deployment adds its own through three seams, none of which aledb-core knows the content
of:

- `ALEDB_BRANDING` (`aledb_common/base_settings.py`) — `{'name', 'version', 'logo'}`, empty by
  default. `base.html` renders each only `{% if %}` it is set, so an unset value renders nothing
  rather than an empty element.
- `templates/home/splash.html` — `aledb_home.views.home` looks it up and falls back to
  `aledb_experiment.views.projects` on `TemplateDoesNotExist`. Rendered in place, not
  redirected, so `/` stays `/`.
- `templates/branding/footer.html` — the "hosted and maintained by…" footer, included by the
  dashboard and About pages. Core ships it **empty**; it existed as four pasted copies before.

More generally, `TEMPLATES['DIRS']` now leads with the project's `templates/` dir and
`STATICFILES_DIRS` with its `staticfiles/`, so a deployment overrides any core template or asset
by path. The source dir is `staticfiles/`, not `static/` — `static/` is `STATIC_ROOT`, and
Django raises `ImproperlyConfigured` if it appears in `STATICFILES_DIRS`. The entry is also
omitted when the directory is absent, or every `./aledb check` reports `staticfiles.W004`.

**The `Powered by ALEdb vX.Y.Z` watermark is not part of this** and has no setting. It is
aledb-core's attribution and renders on every deployment, branded or not.

### Versioning

`aledb_common/version.py` is the single source of truth — before this it existed only as the
literal `ALEdb 1.1.0` inside `base.html`. Bump with `./aledb version --bump patch|minor|major`,
which rewrites that file and leaves the `git tag v<version>` to you.

`version` is a name Django reserves: `ManagementUtility.execute()` answers it with Django's own
version before app commands are ever consulted, so the management command alone is not enough.
`aledb_common/cli.py` dispatches it directly. Delete that branch and `./aledb version` silently
starts printing Django's version instead — `aledb_common/tests/test_version.py` goes through
`manage()` rather than `call_command` precisely to catch that.


### The per-sample mutation page

`aledb_seq/views/breseq_table.py` renders one sample at `/mutations/breseq` in breseq's own
column order and colouring, as the per-sample companion to Compare, which pivots the whole
experiment and lives in the **aledb-compare** plugin at `/compare/` (see **Compare is a
plugin** below). A picker moves between samples; the evidence cell links into the genome
browser when the sample has a stored alignment.

The picker is the same menu as the genome browser's Samples control and the Metadata page's
column menu, but picking one sample rather than any number, so the button carries the chosen
name as its value and exactly one row is `active`. Its rows are **links, not a `<select>` in a
form**: one request per sample either way, and links need no script at all, which is what the
`<noscript>` submit button used to cover. The href is the same `?ale_experiment_id&reseq_id`
pair the form submitted.

The cell markup is not a lookalike -- it is generated by `aledb_import.annotate.display`,
the port of the code that wrote the report the sample was imported from. A row is
`{**mutation.gd_data, **mutation.annotation}` handed to `add_html_fields()`, which is why
annotation lives in one JSON column rather than twenty scalar ones: rendering is a dict
merge, not a rebuild. It is server-rendered rather than fed to DataTables as a JSON blob,
because the markup already exists by the time the view runs.

It is called **Mutations** in the nav and on the page. **Compare** used to sit beside it
here; it is registered by the aledb-compare plugin now, so on a deployment without that
plugin this page is the only mutation table core offers. The table markup
itself lives in `aledb_seq/templates/breseq_table/_mutation_table.html`, shared with the genome
browser, which renders the one mutation it is open at through the same `build_rows` — the two
must not drift, because the cell contents come from `annotate.display` and mean nothing without
these columns around them.

A mutation imported before a reference was available has no annotation to render and falls
back to the flat columns, with the page pointing at `./aledb reannotate`. That fallback is
the usual reason the page looks plain: nothing is wrong with the rendering, the rows simply
have no annotation yet. `Mutation.gd_data` is kept for every mutation, so `reannotate`
recomputes them in place against the stored reference -- re-importing is not needed.

### Creating and importing are pages, not dialogs

`/ale/projects/new/`, `/ale/experiments/new/` and `/import/add/` are all full pages. The
first two were modals over the list tables and are not any more: a create form wants a
heading, room to explain its fields and a URL you can link someone to. The modals were also
where two bugs lived -- an inline panel overlapped the DataTable beneath it, and Bootstrap's
data-api was bound twice so a dialog opened and closed in the same millisecond.

`?project=<pk>` on the experiment page fixes the project, so arriving from a project there
is no picker to get wrong. Each page checks permission itself -- 403 signed out, 403 for a
project you cannot edit -- rather than relying on the button being hidden. The POSTs still
go to `project_create` / `experiment_create`, which is where the real checks live.

### Editing is three pages, and one of them is not what it looks like

`/ale/project/<pk>/edit/`, `/ale/experiment/<pk>/edit/` and `/ale/sample/<pk>/edit/`, plus
`/ale/experiment/<pk>/samples/` for the whole experiment at once. Same split as creating: a
GET page that checks permission itself, a `@require_POST` JSON endpoint that checks again.
All four gate on `can_edit_project` -- `Project.user` or a superuser -- so staff, who may
*view* every project, cannot edit one they do not own.

The project summary on `ale/project_detail.html` **stays read-only**; editing did not go
back into it. It now shows `description` and `status` as well, which were editable-but-never-
displayed before: a save has to be visible somewhere or it reads as having done nothing.

**Changing a sample's identity never writes a number.** `aledb_experiment/samples.py`
resolves (or creates) the `AleId`/`Flask`/`Isolate`/`TechnicalReplicate` row for the *target*
A/F/I/R and re-points `ResequencingExperiment.tech_rep` at it. Those four rows are shared --
`gd_import._get_or_create_chain` reuses one `AleId` and one `Flask` across every sample under
them -- so `flask.flask_number = 30; flask.save()` renumbers every sibling in that flask. The
FK re-point also keeps `reseq.pk` fixed, which the store paths (`samples/<pk>/aligned.bam`)
depend on, and makes a swap need no ordering logic: both samples move to freshly resolved
targets and the rows they vacated are pruned afterwards.

Three lookups there deliberately differ from `gd_import`, and each is a correction:

- `Flask` keys on `(ale_id, flask_number)` with `media` in `defaults`. `gd_import` passes
  `media=` as a *lookup* kwarg while `Flask` is unique on that pair, so it raises
  `IntegrityError` against an existing flask carrying different media.
- `Isolate` is `filter().order_by("pk").first()`, not `get_or_create`. `Isolate` has no
  `unique_together` and `gd_import` get_or_creates it on six fields including `reseq_date`, so
  real databases already hold two rows at one `(flask, isolate_number)` and `get_or_create`
  raises `MultipleObjectsReturned` on them. Adding the constraint needs a data-repair
  migration and is a separate change.
- A newly created row inherits `media`, `freezer_box`, the isolate description and the
  replicate's text from the row it replaced. A renumber re-labels a sample; it does not move
  it to different growth conditions or discard what was written about the run.

Two samples may not share a coordinate, and the save refuses it. There is no constraint
saying so, but `aledb-fixation` builds `flask_isolate_mutation_dict[(flask, isolate)] = qs` by
plain assignment, so the second sample at a coordinate silently overwrites the first and its
mutations vanish from fixation with no error. Emptied rows are pruned bottom-up for the
opposite reason: `rebuild_sample_counts` counts `AleId`/`Flask`/`Isolate` **rows**, and the
ALE picker is built from `AleId` rows. An `Isolate` still referenced by another isolate's
`parent_isolate` or an `AleId.starting_strain` is kept instead -- both are `DO_NOTHING`, so
the database would reject the delete, and nothing in the product writes either column.

A structural save calls `run_post_experiment_hooks` and `rebuild_sample_counts`, once per
POST. It deliberately does **not** call `rebuild_dashboard_data`, which pulls every
`ObservedMutation` in the database into Python -- nothing about a renumber changes a mutation
count, and paying for the whole database on every rename is what would make this feel broken
in production. A descriptive-only save rebuilds nothing.

**`Flask.flask_number` is labelled "Time point" on the edit pages.** It is the only ordinal
in the schema that places a sample along an ALE -- fixation sorts by it and takes the last two
to decide what has fixed -- and real data carries values like 30000, so it is plainly being
used to record cumulative divisions rather than a count of flasks. The column keeps its name;
only the UI changed, including the validation message, which is the one place the internal
name would otherwise reach a user. Its input is plain text, not `type="number"`: steppers are
useless on a five-figure value. **It is still an `IntegerField`, so a fractional time point is
refused** -- a real one needs a column change plus a decision about the A-F-I-R filename
parsing that assumes integers.

**No edit page touches `person`.** The field is absent from every form *and* from what the
endpoints assemble, so it is not merely ignored -- there is nowhere for a posted value to go.
Changing who owns or ran something is its own workflow: folding it into a details form means
every save rewrites it, and a form that dropped the field would silently blank it.

**The trap to know about:** a renumber often changes no visible label.
`ale_flask_isolate_str` returns `Isolate.description` verbatim whenever it is set, and
`_get_or_create_autonumbered_chain` fills it with the filename for every sample not already
named `A-F-I-R`. So both pages show the computed `A# F# I# R#` beside the effective label and
keep the description editable in the same form. A duplicate `sample_name` within an
experiment is refused for a related reason -- re-import finds an existing sample by name --
but only when the name actually *changed*, or an experiment that already had a duplicate pair
could never be saved at all.

### Sharing: four roles, at the project level only

Access is granted on a **project** and nowhere else. An experiment, a sample and a mutation
are all reached through `experiment.project`, so there is one place to ask the question and
one place to change the answer. Nothing below the project has an owner.

    read  <  write  <  admin  <  owner

`read` sees the project and its data. `write` adds, edits and curates — data, samples, tags,
experiment filters. `admin` additionally manages access and may soft-delete the project.
`owner` additionally grants and revokes ownership.

`aledb_experiment/roles.py` holds the ordering and nothing else — it imports nothing from
Django, so `models.py` (which needs `ROLE_CHOICES` for a field) and `permissions.py` (which
needs `rank`) can both use it without importing each other. Roles are strings, not integers,
so the column reads in `/admin/` and in a sqlite dump; `roles_at_least()` is what turns
"role ≥ write" into one `role__in=[...]` lookup.

`aledb_experiment/permissions.py` is the whole policy, and `effective_role(user, project)` is
the whole of *that* — every `can_*` is a comparison against it. Three things confer a role
with no row: a superuser is owner everywhere, `Project.user` is owner of their own project,
and `is_public` gives everyone `read`. **There is no blanket grant for staff**; that clause
used to end `can_view_project` and, since `load_projects` marks every imported user staff, it
made nearly everything readable by nearly everyone. `./aledb project_access` is the escape
hatch for a deployment that relied on it.

**`ProjectAccess` is one row per (project, subject, role)**, where the subject is a user *or*
a group. Its unique constraints are conditional (`condition=Q(user__isnull=False)`) because a
plain `unique_together` would treat NULLs as distinct and permit unlimited duplicate group
rows. A group may not hold `owner`: ownership has to be answerable about a person, and
otherwise a group's manager could add themselves and own every project the group owns.

**`Project.user` stays, as the *primary* owner, mirrored by a `ProjectAccess` owner row.** It
is read by `Project.owner()`, two templates, `ProjectAdmin`, `load_projects`,
`try_creating_project` and `load_example` — which looks projects up *by* it. `set_primary_owner`
is the only writer, and revoking the primary owner re-points it at the longest-standing
remaining owner. Multiple owners are allowed, so a project whose only owner leaves is not
stranded.

**The role cache is not an optimisation.** `mutation_table_builder` calls
`can_add_experiment_filter` once per sample column *and* once per mutation row, so a table of
400 mutations across 20 samples asks 420 times. django-guardian absorbed that in its own
per-user cache; `permissions.py` keeps a `{project_pk: (generation, role)}` dict on the `User`
instance, whose lifetime is naturally one request. A module-level generation counter, bumped
by every write helper, invalidates it — which is what stops a `User` object that outlives a
grant (every test, and the CLI) from answering with a stale role.
`test_permissions.CacheTestCase` asserts the query count directly.

**django-guardian is gone.** It stored a single `view_project` object permission and nothing
else, and the two lookups built on it filtered on the app label `'ale'` while the real label
is `aledb_experiment`, so they matched nothing. `0005` converts its grants to `read` rows and
every `Project.user` to an `owner` row. It reads guardian's table with **raw SQL behind an
introspection guard**, not `apps.get_model("guardian", ...)`: the ORM version only works while
guardian is still in `INSTALLED_APPS`, which would have forced migrate-then-uninstall across
two releases.

**`0003_backfill_view_project_grants.py` is inert but must stay, under that name.** It
declared a dependency on `("guardian", "0001_initial")`, and a dependency on an uninstalled
app makes `migrate` fail with `NodeNotFoundError` on a *fresh* database — which every test run
builds. Four other apps (`aledb_seq.0005`, `aledb_stats.0003`, `aledb_filter.0002`,
`aledb_common.0001`) name the file as a dependency, so it cannot be renamed away.

### Groups

`AleGroup` + `AleGroupMembership`, in `aledb_experiment`. Deliberately not
`django.contrib.auth.Group`: that one is administered from `/admin/`, has no owner, and
carries a `permissions` m2m that would sit unused inviting someone to wire model permissions
into a per-object scheme.

`is_manager` is a flag on the membership row rather than a second m2m, so "every manager is a
member" is true by construction. **The group's owner holds a membership row too**, written
when the group is created — that is what lets `effective_role` reach them through the same
join as everyone else instead of needing a `Q(group__owner=user)` special case in the hot
query. Owner renames/manages/appoints/transfers/deletes; a manager renames and manages plain
members only; a member just sees the page. The owner's row can never be removed or demoted —
transfer first, which leaves the outgoing owner a manager.

**Project roles and group roles are disjoint vocabularies with no bridge**, which is itself
the guardrail: holding `admin` on a project lets you add "Barrick Lab" to it and gives you
nothing whatever over that group. The corollary is that **the add-a-group box resolves against
`visible_groups(user)`, not every group**. There is no autocomplete — a box that resolved any
name would be an oracle: type names until one is accepted and you have enumerated every group
on the installation. A group you cannot see returns the same message as one that does not
exist, and `test_permissions` asserts the two strings are equal.

### The sharing and group pages

`/ale/project/<pk>/access/` (admin or owner) and `/ale/groups/`, `/ale/group/<pk>/`. Same
shape as every other page here: function-based views, permission checked inline, `403.html`
with a status, hand-written Bootstrap markup posting to `@require_POST` JSON endpoints through
`aledbPost` — no Django `Form` classes, no autocomplete, usernames typed in and resolved
server-side.

The role dropdown on a row posts to the **same** grant endpoint as the add boxes: it is an
upsert keyed on the subject, so there is no second endpoint that could disagree with it. A row
the actor could not re-grant — an owner, seen by an admin — renders as text rather than a
dropdown, so the page never offers a control whose every use the server would refuse.

### Compare is a plugin

`/compare/` -- mutations as rows, samples as columns -- lives in the **aledb-compare** repo,
not in core. It is one way of looking at an experiment rather than a core function, which is
exactly what aledb-fixation and aledb-converge are: all three are a function view that builds a
context and renders `base_table_template.html` through `mutation_table_builder`. Compare was
simply the one that had never been moved out.

What stayed, and why none of it could go:

- **`mutation_table_builder`** -- `aledb_search`, `aledb_export`, aledb-fixation and
  aledb-converge all call it.
- **`base_table_template.html` and `table_template.js`** -- rendered by three other pages.
- **The curation endpoints**, now at `/mutation-table/` (`aledb_seq/views/table_actions.py`,
  `aledb_seq/table_urls.py`). Every table posts to them, not just Compare, and the state is
  shared: `TechnicalReplicate.tags` is what the Show/Hide Tag control filters sample columns on
  in `get_reseq_ordered_dict`, so tagging from one table changes what the other three show.
  Replicating them per plugin would have meant four write paths to core-owned tables.

**`/mutations/` is not a page any more** and returns 404. `aledb_seq.urls` has no `^$`, and a
plugin cannot reclaim that path: Django does not backtrack out of a matched `include()`.

Two consequences worth knowing before wondering whether something is broken:

- **Compare's nav entry sits at the end of the experiment section**, after Filter and the other
  plugins, because nav order is `INSTALLED_APPS` order and assembled projects append plugins
  after every core app. Listing `aledb-compare` first in `.gitmodules` makes it the first
  *plugin* entry, which is as close to its old position as the design allows.
- **Core's suite cannot reach it.** `./aledb test` has no plugin discovery, so Compare's tests
  run only as `./mutint test aledb_compare`.

`breseq_table.html` links to it through `{% url 'compare' as compare_url %}` inside an `{% if %}`,
so the "all samples" link disappears rather than dangling where the plugin is not installed --
the posture `nav_registry` takes with a `url_name` that will not reverse.

**The three endpoint URLs in `table_template.js` are reversed by name, not written out.** That
file is a Django template included inside a `<script>`, so `{% url %}` works there -- and so does
anything else tag-shaped, **including inside a `//` or `/* */` comment**, which the template
engine does not recognise as a comment at all. Writing a tag name in a comment there executes it.

Nothing in core rendered that file until this change: `aledb_search` has no tests and the other
three consumers are plugin pages. `aledb_seq/tests/test_table_actions.py` renders it directly now,
which is what lets core notice a broken tag before four pages do.

### Example datasets

`./aledb load_example` lists what is registered; `./aledb load_example <name>` loads it.
Components own their own data -- `aledb-fixation` ships `aledb-fixation-example` -- and
register it from `AppConfig.ready()` through `aledb_common/example_registry.py`, the sixth
registry.

**A dataset is a directory of files the import path already understands**, not a Django
fixture. Loading one runs the registered handlers in priority order exactly as a drop on the
Add Data page does, so the derived data it exists to show is genuinely computed. A fixture
would load faster and prove nothing. A `README` and dotfiles in the directory are skipped, so
the expected answer can live beside the files that produce it.

The command refuses a second load, naming `--replace`; `--replace` soft-deletes the previous
experiment rather than importing on top of it. Everything lands in one project called
**Examples**, created with the django-guardian grant -- `Project.objects.create` alone leaves
the owner unable to view what they own.

**Why this exists.** Fixed Mutations renders an empty table when an experiment has nothing
fixed, and an empty table when the feature is broken, and there was no data anywhere in the
suite that could tell the two apart.

Three are registered, one per plugin, each with a `README.md` beside its data stating the
expected answer as a table, and each asserted by that plugin's tests:

| dataset | shape | what it demonstrates |
|---|---|---|
| `aledb-fixation-example` | 2 ALEs x 4 flasks | a mutation in the last two flasks is fixed; one lost earlier is not |
| `aledb-converge-example` | 3 ALEs x 2 flasks | a gene hit in two lineages converges; one recurring in a single lineage does not |
| `aledb-compare-example` | 2 ALEs x 3 flasks | a pivot with full, partial and single rows across all six mutation types |

The first two have a computed answer to check. **Compare's does not** -- it derives nothing --
so what its dataset supplies instead is a *pattern*, because a table whose rows all look alike
demonstrates nothing. It is also the only one carrying every mutation type, `AMP` included:
that used to appear solely on `/mutations/amplifications` because Compare passed a
`filter_type` whose value means *exclude*, and the `AMP` row is what stops that returning
unnoticed.

### A mutation is fixated in the last two flasks, so the time axis must be the flask

`aledb-fixation` intersects an ALE's final two flasks by `flask_number`, so **an ALE with one
flask can never fix anything** -- and neither can an experiment made entirely of such ALEs.
That is the usual reason for an empty Fixed Mutations page and it is not a bug.

It is easy to arrive at by accident. `gd_import` reads a strict `A-F-I-R` filename
(`1-1500-1-1.gd` = ALE 1, flask 1500, isolate 1, replicate 1); **anything else falls to
auto-numbering, which puts every sample under ALE 1 / flask 1 as separate isolates.** A
51-timepoint series imported as `Ara-1_500gen_762B.gd` and friends therefore becomes 51
isolates of one flask, with the generations in `isolate_number` -- which is exactly the shape
of the MutInt dev database, and why fixation has never produced a row there.

`./aledb rebuild_fixation [<experiment_id>]` recomputes. It reports the count and says when no
ALE has more than one flask, which is the difference between a stale answer and an impossible
one. Fixation is otherwise computed only by the post-experiment hook, so an experiment whose
data predates the plugin -- or whose filters changed since -- had no way to catch up.

### Which import types the Add page offers

Two rules, because the reasons differ. Both are declared on the handler and applied by
`get_import_types_for(has_reference)`, never in the template, so the dropdown and the JSON
the page classifies a drop with cannot disagree:

- `only_without_reference` -- `reference` stops being offered once the experiment has one.
  Establishing a reference is a one-time act; replacing the *annotation* is
  `replace_annotation`, and swapping in a different genome is deliberately shell-only.
- `requires_reference` -- `replace_annotation` and `genomediff` are **absent** until there is
  a genome. There is no greyed-out presentation and no `unavailable` parameter; a dropdown
  entry you can see and cannot pick is a dead end. The page's own banner carries the answer
  instead, and names the bare-`.gd` case explicitly, so the short menu is explained whether
  or not an entry is there to point at.

`breseq_folder` is never blocked: it brings its own reference. Auto-detect is unaffected --
a reference dropped alongside data still runs first on `priority`, which is how a first drop
establishes one. `/import/types/` stays unscoped; it has no experiment to scope by.

Because both the menu and the banner are rendered from `has_reference` at page load, a drop
that establishes one leaves the page stale. `finalize` therefore returns `has_reference`, and
the page **reloads** when it flips rather than patching the menu and the banner in the client,
where the two could drift from what the server would render. The import summary is the only
record that the drop happened, so it rides across the reload in `sessionStorage` under
`aledb-add-summary-<experiment>` and is re-rendered on the way back.

`replace_annotation` claims only GenBank and GFF3, not FASTA. A FASTA is sequence with no
features, so there is nothing in one to install -- it used to be accepted and then rejected
on the sequence check, which named the wrong reason.

### A GenBank's contig names come from its LOCUS line

`annotate/genbank.py` takes each record's `seq_id` from **LOCUS** (`record.name`,
`NC_000913`), not from **VERSION** (`record.id`, `NC_000913.3`). breseq does the same
(`reference_sequence.cpp` `LoadGenBankFileHeader`), so every seq_id in a `.gd` it writes is
the unversioned one — and `ReferenceSequences.add` matches names **exactly**, on purpose, so
a reference that called the contig `NC_000913.3` would reject every one of those `.gd` files.
Taking VERSION here made a GenBank and breseq's own GFF3 of the same genome disagree about
what its contigs are called, which is the one disagreement the whole normalization design
exists to prevent.

The VERSION accession stays in `references.sequences` as an alias, so a lookup by either
spelling resolves. It is an alias only: `_sequences_of` and `render_breseq_gff3` both dedupe
by object identity and emit `annotated.seq_id`, so the canonical name is what gets stored and
hashed.

Existing references are untouched — their GFF3 was rendered when they were established. A
GenBank re-imported through `replace_annotation` against a reference established under the old
rule renames its contigs, which is exactly the case `reference_rename` asks about before
proceeding.

### Telling someone they picked the wrong type

The one mistake worth engineering for is a `.gd` chosen as a reference genome, or the reverse:
those are the two things an experiment is made of, and each parser's own words for the other
("No GenBank records found in: ...") name what failed rather than what to do. Three layers,
each catching what the one before it cannot:

- **Before anything uploads**, `renderList()` in `import/add.html` groups the files the chosen
  type declined by what they *do* look like, and names the type they belong to -- including a
  type this experiment cannot use yet ("...which needs this experiment to have a reference
  genome first"), which is why the page embeds the **unscoped** registry as
  `all_import_types` alongside the scoped `import_types` that fills the dropdown.
- **Server-side, by pattern**, `import_registry.identify()` turns `run_import`'s "not
  recognised as Reference genome" into "...it looks like GenomeDiff mutations, so import it
  with that type". Patterns only, never a handler's `detect`: the job is to name a likely
  alternative, not to re-decide what the file is.
- **Server-side, by content**, `aledb_import/sniff.py` reads the first non-blank line, which
  is where all four formats declare themselves. This is the layer that survives a wrong
  extension: a `.gd` saved as `.gbk` passes every pattern check above and is caught by
  `reference.detect_format`, and a GenBank named `.gd` by `gd_import._parse_document`.

The sniff is deliberately narrow. It overrides the extension only to *refuse*, never to route:
`detect_format` still trusts `.gff3` over content, and auto-detect still routes on `patterns`.
Only a positively identified other format is refused, so anything whose first line declares
nothing is still the parser's to judge.

### The genome browser

`aledb_seq/views/browse.py` renders igv.js for one `ObservedMutation` at
`/mutations/browse?observed_mut_id=<pk>`, linked from every mutation-table frequency cell whose
sample has `bam_stored`. It is the first consumer of the alignment routes, which had been built
and tested with nothing pointing at them.

Two things are easy to get wrong and fail *silently* — an empty track, no error:

- **Every track needs an explicit `format:`.** The routes end `/bam`, `/fasta`, `/gff3` and
  carry no file extension; igv.js otherwise infers format from the extension.
- **Every indexed track needs an explicit `indexURL:`.** The store renames breseq's
  `data/reference.bam.bai` to `aligned.bam.bai`, so igv.js's default `<url>.bai` derivation
  would be wrong even if the URLs had extensions.

`igv.min.js` is vendored in `aledb_common/staticfiles/js/` and loaded from the browse template
only — it is ~1.4 MB and no other page needs it.

### Coverage comes from a BigWig, not from igv

A sample contributes **two** tracks: a `wig` track on its stored `coverage.bw`, and the
alignment track, at adjacent `order` values so they read as one block. The reason is that
`checkZoomIn` gates the *whole* alignment track on `visibilityWindow` — igv's own coverage row
is inside that track, so it is taken away with the reads. Coverage at a whole-genome view is
therefore impossible from the BAM alone, and asking for the reads instead is what exhausts the
tab.

The alignment track sets **`visibilityWindow: 4000`**, overriding igv's own 30 kb default, so
reads stop drawing above a 4 kb span while the BigWig keeps going at every zoom.

The **Display menu decides which tracks are loaded**, not which rows of one track are shown.
That distinction is the whole feature: *Display Coverage Only* does not load the alignment track
at all, so **no BAM request is made** — measured, 0 BAM requests against 9 for the BigWig. It
changes track identity rather than a flag, so choosing an item reloads whatever is showing.

A sample imported before coverage existed simply has no wig track until `./aledb coverage` runs.

**igv builds its entire UI inside a shadow root on `#igv-browser`**, with its own stylesheet
adopted there (`attachShadow({mode:'open'})` + `adoptedStyleSheets`). Two consequences, both of
which will waste an afternoon if you do not know them:

- **No rule in `browse.css`, or any document stylesheet, can style anything igv draws.** The
  class lands on the element and the rule silently never applies. Styling igv's internals means
  handing the rules to the shadow root — `browse.html` appends a `<style>` there for the mutant
  tint. A rule for `.igv-track-label` sitting in `browse.css` looks correct and does nothing.
- **`document.querySelector` cannot see igv's DOM.** `document.querySelectorAll('.igv-track-label')`
  returns nothing while igv is fully rendered; you have to go through
  `document.getElementById('igv-browser').shadowRoot`. Anything checking igv's rendering — a
  headless probe especially — will otherwise conclude igv never rendered at all.

The **sample menu** is a dropdown over every sample in the experiment with an alignment, the
one being viewed included: it is shown and hidden like the rest, so *Hide all samples* leaves
only the reference and gene tracks. It has the same shape as the column menu on the Metadata
page -- DataTables' colvis collection -- a `ul.dropdown-menu` of `<li><a>` where a showing
sample is `active` on its `<li>`, so Bootstrap's own `.dropdown-menu > .active > a` paints the
row and there is nothing to restyle. (DataTables does put `#717171` on the active `<li>`, but
its `<a>` covers the row, so that grey is never the colour you see; do not copy it.) The rows
are links only so Bootstrap styles them, which is why the click handler stops the default as
well as the propagation. Three things about it are load-bearing:

- A sample's track is held as the **promise** of it, not the track. *Show all samples* fires
  every `loadTrack` at once, and a plain "have I got it yet" test would load the same BAM twice.
- Every track config carries an explicit **`order`**, or igv appends a re-shown sample at the
  bottom of the stack instead of returning it to its place in the menu.
- The menu subscribes to igv's **`trackremoved`**, because igv removes tracks by itself too —
  its per-track gear menu has *Remove track*, and a track that fails to load is discarded the
  same way. Without it the box stays ticked for a sample that is no longer on screen.

Every alignment track also sets **`showSoftClips: true`**, which igv defaults to off. The page
exists to look at one called position, and a clipped end is often what explains the call. It is
the same flag the track's gear menu toggles, so it can still be turned off per track.

Beside it a **Display** menu sets reads only, coverage only, or both -- on every alignment
track showing, and as the default the next one loads with. It is deliberately stateless: igv's
own gear menu can change a single track afterwards, so no one value would be true of them all,
and the menu never marks a current choice. The flags are igv's own `showCoverage` and
`showAlignments`, the pair its gear menu toggles, so a track configured with them starts the way
the menu would set it.

**Do not copy igv's own height recipe** for that menu. It adds `coverageTrackHeight` to
`alignmentTrack.height`, which is the reads' *current* box rather than the space they are owed,
so a round trip through coverage-only ratchets a track down and leaves it there -- measured at
300px becoming 100px. Remember the height the track had while its reads were showing and hand
igv that total instead; it divides the space itself.

A `*` marks the samples the mutation is **called** in, using the mutation table's own rule
(`breseq_present or gatk_present`) rather than a second one, so it agrees with the filled cells
back on `/mutations`. An ObservedMutation row alone is not a call: one with `present=False`
records that the mutation was looked for and found absent. The same `*` is prefixed to the igv
track name, so a stack of pileups says which of them carry the call — igv puts no constraints on
a track name. In the menu a sample without one gets a same-width empty span so the names stay in
a column; on the track there is deliberately no such padding, because an igv track label is its
own shrink-to-fit badge with centred text and has no column to align to.

All four margins round the browser measure the same 25px; the header trim that makes the top
one work is in `common.css` and applies to every page (see **The shell's two widths**).

The cell markup is coupled to two things that substring-test it: `_contains_mutation` decides
whether a row renders by looking for `true`, and `table_template.js` colours a cell by testing
for `class="true"`. Keep that class on the anchor, and keep `true` out of the empty-cell literal.

### Django Apps

All apps use the `aledb_*` namespace. Key apps:

- **`aledb_experiment/`** — Core data models: `AleExperiment`, `Project`, `AleId`, `Flask`, `Isolate`, `Media`, `FreezerBox`. Central schema everything else references.
- **`aledb_import/`** — Experiment upload pipeline. **Every path ends in `gd_import`**, so a
  CLI upload and a web drop produce identical rows:
  - `gd_import.py` parses with the external `genomediff` package (`GenomeDiff.read`) and is
    the one place mutations are stored. Each record is kept verbatim in `Mutation.gd_data`
    and round-tripped back out by `Mutation.to_gd_line()` (`aledb_seq/models.py`) for
    `gdtools APPLY`, so nothing but the raw record may go in that field.
  - CLI upload — `./aledb upload <path>` walks for `<exp>/breseq/` + `<exp>/metadata/`,
    hands the folders to `breseq_folder`, then parses the metadata. It used to be a second
    importer that read the .gd plus the breseq HTML report and shared no code with
    the web paths; that is gone, along with `upload.py`.
  - The vendored `gdparse/gdparse/gdparse.py` (`GDParser`) survives only for the annotation
    test fixtures. Note it has no `INT` type; `genomediff` does.
  - **Annotation is internal** (`annotation.py` + `annotate/`). Gene, codon and amino-acid
    fields are derived from the experiment's stored reference at import, not read out of
    the `.gd`, so breseq's plain `output.gd` is enough and `gdtools ANNOTATE` is not needed.
    `./aledb reannotate <id> [--ref FILE]` recomputes them when a better reference arrives.
    The annotator is a port of breseq's own, checked against `gdtools` output.
  - Web breseq **folder** upload — `upload_session.py` (chunked: `POST /import/uploads/`,
    `.../chunk`, `.../finalize`) stages the drop, then `breseq_folder.py` imports it. Takes
    `data/output.gd` plus `data/reference.{gff3,fasta}` and `data/reference.bam{,.bai}` --
    everything from the sample's `data/` folder, `output/` is not consulted;
    stores them under `ALEDB_STORE_DIR` keyed by database id (`aledb_common/store.py`), and
    records the shared reference as `ExperimentReference`. Samples whose reference does not
    hash-match the experiment's are rejected individually. Alignments are served with HTTP
    range support by `aledb_seq/views/alignments.py`, which resolves every path from a
    primary key rather than from anything the client sends.
  - Web breseq **folder** upload — `upload_session.py` (chunked: `POST /import/uploads/`,
    `.../chunk`, `.../finalize`) stages the drop, then `breseq_folder.py` imports it. Takes
    `data/output.gd` plus `data/reference.{gff3,fasta}` and `data/reference.bam{,.bai}`,
    storing them under `ALEDB_STORE_DIR` keyed by database id (`aledb_common/store.py`).
    The shared reference is recorded as `ExperimentReference`; a sample whose reference
    *sequence* does not hash-match the experiment's is rejected on its own. Sequence is the
    sole invariant (`ExperimentReference.matches_sequence`) — differing annotation never
    rejects, and a folder import leaves the stored annotation alone so import order cannot
    redefine it; only the explicit `replace_annotation` import type refreshes it. A bare `.gd` is *skipped* here —
    it has no reference for that check to apply to.
  - **References arrive with the data.** There is no separate reference page: the `reference`
    import type (priority 10) runs before anything hash-checked against it, so a GenBank,
    GFF3 or FASTA dropped alongside `.gd` files in one drop is established first whatever
    order the files are listed in. `import_gd_files` still raises `ReferenceRequired` for
    non-registry callers when the target experiment has none.
  - **`replace_annotation`** (priority 11) is the narrow survivor of the retired
    `/import/reference/` page: it refreshes an established reference's annotation while
    holding the *sequence* fixed, and refuses a file whose sequence differs. It claims the
    same files as `reference` and only its higher priority number keeps auto-detect from ever
    picking it, so it is reachable only by being named explicitly. It is hidden from the Add
    page's dropdown until the experiment has a reference (`requires_reference=True`).
    `establish_or_check(..., replace=True)`, which overwrites a *different* genome, is
    deliberately reachable from the shell only.
  - **Normalization is the linchpin** (`reference.py`, `reference_store.py`): every reference,
    however it arrives, is converted to one canonical pair — a genes-only GFF3 plus a FASTA —
    before being stored or hashed. Without it a GenBank and the GFF3 breseq derived from the
    same genome would hash differently and the shared-reference check would reject valid data.
    GenBank and FASTA go through Biopython; GFF3 uses the small in-repo reader, since
    Biopython has no GFF3 parser. Alignments are served with HTTP range support by
    `aledb_seq/views/alignments.py`.
    Sample identity comes from the filename: a strict A-F-I-R name (`3-30000-1-1.gd`) is
    parsed as such, and anything else (`Ara-1_500gen_762B.gd`) gets its own auto-numbered
    isolate under ALE 1 / flask 1, with `Isolate.description` set to the filename so it
    displays by name. Do not route this through `util.parse_ale_name`, whose bare
    `except: return 1` would collapse every non-conforming file onto the same sample.
- **`aledb_seq/`** — Mutation models and views (`/mutations/breseq`, `/mutations/browse`),
  the shared `mutation_table_builder`, and the curation endpoints at `/mutation-table/`.
  Note `/mutations/` itself is **not** a page: it was Compare, now the aledb-compare plugin.
- **`aledb_fixation/`** — Fixated mutation computation.
- **`aledb_converge/`** — Convergence analysis across experiments.
- **`aledb_filter/`** — Experiment filtering UI and models.
- **`aledb_metadata/`** — Parses XPMD metadata files associated with experiments.
- **`aledb_export/`** — Data export in various formats.
- **`aledb_stats/`** — Precomputed statistics: `StaticData` (the needle plot) and
  `ExperimentSummary` (the Overview's mutation counts). Both are rebuilt through
  `aledb_common.rebuild_registry` rather than computed per request; the Overview used to
  materialise every ObservedMutation in the experiment to produce sixteen integers.
- **`aledb_search/`** — Cross-experiment search.
- **`aledb_bibliome/`** — Publication/bibliography management.
- **`aledb_dashboard/`** — Dashboard views and timeline events.
- **`aledb_accounts_noauth/`** — Default auth stub: Django's built-in login/logout, no enforcement. Swap for `aledb_accounts` (brute-force protection) or any other auth app by changing `INSTALLED_APPS`.
- **`aledb_accounts/`** — Enhanced auth with `django-defender` brute-force protection. Optional; used in production (`settings_private.py`).
- **`aledb_common/`** — Shared utilities, middleware (`LoginRequiredMiddleware`), the seven
  registries (context, import, plugin, nav, about, example and **rebuild**), and global static
  files. `DerivedDataState` is its only model.
- **`config/`** — Django project config: settings, root URLs, ASGI/WSGI entry points.

### Adding and deleting through the UI

Creation and deletion are nested under the objects they act on:

- `/ale/projects/` — **+ New project**, optionally creating its first experiment in the same
  step, and delete selected rows.
- `/ale/experiments/` — **+ New experiment** (a project picker plus a name) and delete selected
  rows. `/ale/project/<pk>/` carries the same **+ New experiment**, with the project implicit.
  Both POST `/ale/experiments/create/` and land on the new experiment's Add page. The picker
  lists only projects `can_edit_project` allows, so it never offers one the POST would 403 on;
  a user with no editable project is shown **+ New project** instead.
- Shared JS for those controls — `aledbPost`, `aledbConfirmDelete`, `aledbTogglePanel` — lives in
  `aledb_common/staticfiles/js/aledb_crud.js`, loaded from `base.html`. It was duplicated inline
  in two templates before. A page using `aledbPost` must render `{% csrf_token %}` somewhere:
  that is what sets the cookie it reads. `aledbConfirmDelete` calls `swal()`, which `base.html`
  does **not** load — pull sweetalert in per template.
- An experiment's page (`/stats?ale_experiment_id=<pk>`) carries **+ Add data** and **Delete**,
  rendered through `{% block experiment_actions %}` in `aledb_common/templates/base.html`.
- There is no Amplifications page. `/mutations/amplifications` was a copy of `mutation_table`
  differing in one argument, and it was the only page showing `AMP` mutations, because
  Compare passed `filter_type="AMP"` — a value that means **exclude** AMP, not include it.
  Both are gone and Compare now renders every mutation type. `AMP` was never a separate
  feature: it is one of eight breseq/GenomeDiff types, first-class throughout the pipeline.
- `/import/add/?ale_experiment_id=<pk>` is the one place data goes in. It is scoped to an
  experiment **by primary key**, so two experiments may share a name and two people may add to
  the same one — unlike `_prepare_experiment`, whose name+person lookup forks an experiment per
  person. Use `gd_import.prepare_experiment_by_id` for anything web-facing.
  There is **no unscoped form of this page and no sidebar entry for it** — `aledb_import`
  registers no nav item. Both existed briefly and could only ever land on a page with no
  experiment to add to; without a usable `ale_experiment_id` the route is now a plain 404.
- Everything records the logged-in user; there are no person fields to fill in.
- Editing lives beside all of this -- see **Editing is three pages** above. The controls in
  `stats.html`'s `{% block experiment_actions %}` are now gated on `can_edit`; Add and Delete
  used to render for everyone, which was a dead end dressed up as an action rather than a
  hole, since the endpoints refused anyway.

**Deletion is soft.** `Project` and `AleExperiment` carry `deleted_at`/`deleted_by`
(`SoftDeleteMixin`); only those two are flagged, and children are reached by traversal when
`./aledb purge_deleted --older-than <days>` finally removes them. `objects` is deliberately
unfiltered — a filtered default manager would silence the import paths' `get_or_create` — so
user-facing lists exclude deleted rows explicitly via `aledb_experiment.models.live()`.

### Import types are pluggable

`aledb_common/import_registry.py` is one of seven registries in `aledb_common/` -- alongside
`plugin_registry`, `nav_registry`, `about_registry`, `context_registry`, `example_registry` and
`rebuild_registry`. An app registers what it can ingest from `AppConfig.ready()` and it appears in the Add
page's type dropdown and in auto-detect, with no edit to core:

```python
register_import_handler(name='my_type', label='My measurements (.tsv)',
                        patterns=['.tsv'], handle=my_import, priority=50)
```

Unlike `nav_registry`, this one **has explicit ordering**: `priority` decides which handler runs
first, because a reference genome must be established before mutations that are hash-checked
against it. Core registers `reference` (10), `breseq_folder` (50) and `genomediff` (60) in
`aledb_import/handlers.py`. A handler whose shape is not a suffix match supplies its own
`detect` — breseq folders are directory-shaped, and both the reference and genomediff handlers
exclude files that live inside one.

`patterns` does more than route. `identify()` uses it to name the type a rejected file belongs
to, and the Add page serialises it to name the type a file in the drop belongs to before
anything uploads — so a plugin gets both of those by registering, with no edit to core.

### Pluggable App Slots

Some functionality is designed to be swapped by changing `INSTALLED_APPS`:

**Auth slot** — any app with `auth_app = True` in its `AppConfig` and `app_name = 'accounts'` in its `urls.py` is auto-discovered by `config/urls.py`. Default: `aledb_accounts_noauth`. Production: `aledb_accounts`.

**Experiment context providers** — registered via `aledb_common.context_registry.register_experiment_context_provider()` in `AppConfig.ready()`. Used by `aledb_bibliome` to inject publication data into experiment views without a hard dependency.

### Settings Structure

- `config/defaults.py` — Delegates to `aledb_common.base_settings.get_base_settings()`; adds `ROOT_URLCONF` and `WSGI_APPLICATION`. SQLite fallback when `FORCE_SQLITE=1` or running tests.
- `config/settings_local.py` — Local dev (SQLite, DEBUG=True, no Redis/Azure). Created by `./aledb start`.
- `config/settings_private.py` — Production with auth enforcement and `aledb_accounts`.
- `config/settings_public.py` — Public read-only deployment.
- Select with `DJANGO_SETTINGS_MODULE`.

### Data Flow: Uploading an Experiment

1. `./aledb upload <path>` calls `aledb_import.ale_experiment.upload_ale_collection()`
2. Hands each `<exp>/breseq/` to `aledb_import.breseq_folder`, which parses via
   `aledb_import.gd_import` / the `genomediff` package -- the same route a web drop takes
3. Creates `aledb_experiment`, `aledb_seq`, and `aledb_metadata` model instances
4. Ends in `gd_import.run_post_processing`, which asks the rebuild registry to recompute
   everything derived -- the experiment filter defaults, each plugin's tables
   (`aledb_fixation`, `aledb_converge`), the needle-plot data and the Overview's counts, then
   the dashboard's installation-wide totals. See **Derived data and rebuilds** in the suite
   `CLAUDE.md`; none of it is the Django cache framework, which this repo does not use.

### Infrastructure (production)

- File storage: `ALEDB_STORE_DIR`, keyed by database id (`aledb_common/store.py`)

This section used to also claim Daphne, Django Channels, nginx and Redis. **Nothing in
`requirements.txt` supports any of it** and no compose file in this tree references it -- it
was inherited from the pre-refactor deployment. Everything is synchronous in-request; there is
no worker, no broker and no scheduler. `WORKERS.md` in the suite root is the design note for
adding one, and is not implemented.
