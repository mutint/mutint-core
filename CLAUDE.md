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

**Baseline: 181 run, 2 failures + 4 errors.** All six are pre-existing and unrelated to any
recent work; treat any *other* failure as yours. All six are in
`aledb_metadata.tests.test_metadata.TestParser`:

- four errors — `test_get_media_supplement_description`, and three sharing one cause,
  `KeyError: 'R'` at `aledb_metadata/parser.py:149`
- two failures — `test_creating_media_with_metadata_upload`, `test_xpmd_validator`

`test_reseq_URL` and `test_upload_ALE_collection` used to be on this list and now pass.

### Gotchas when writing tests

- **`find_user()` prompts on stdin.** Anything reaching `try_creating_project` with a person
  name that matches no `User` raises `EOFError` under the test runner. Create the `User` first,
  or use `gd_import.prepare_experiment_by_id`, which never resolves a person.
- **`Project.objects.create()` leaves the owner unable to view it.** `can_view_project` consults
  the django-guardian grant, never `Project.user`. Use the `/ale/projects/create/` view, or call
  `grant_access_to_project` yourself, or the project's own pages will 403 in the test — and it
  will not appear in `get_user_projects` either. That used to be masked: the grant lookups
  filtered on a stale app label and `get_user_projects` fell through to returning everything.
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

**Docker (production deployment)**:
```bash
docker-compose -f docker-compose-prod-asgi-host-nginx.yml up --build -d
docker-compose -f docker-compose-prod-asgi-host-nginx.yml down
docker-compose -f docker-compose-prod-asgi-host-nginx.yml logs web
```

## Architecture

### `/aledata/` serves the legacy data root

`aledb_common/views.py` serves `ALE_DATA_ROOT_DIR` at `/aledata/` — breseq HTML reports,
GATK output and CNVnator coverage plots for CLI-uploaded experiments. It is separate from
`ALEDB_STORE_DIR`, which holds web-uploaded data and is served by
`aledb_seq/views/alignments.py`.

The route takes a client-supplied path, so it works in three steps and all three matter:

1. **Containment.** The path is joined to `DOC_ROOT`, `abspath`-normalised, and required to
   stay inside the root via `os.path.commonpath`. `normpath` alone is not enough — it happily
   walks above the root.
2. **Ownership by prefix.** A sample's stored `location` (`<exp>/breseq/<s>/output/`),
   `gatk_location` and `experiment_location` are the roots its files sit under, so the
   requested path's own directory prefixes are matched against those three columns. A path
   under no stored prefix is a 404: this route serves experiment data, not the filesystem.
3. **`can_view_project`** on the owning experiment. There is no exemption for any file type.

It previously had a branch keyed on `'.html' in page_name or '.ba' in page_name` that skipped
authorization outright — substring tests, so every `.bam`/`.bai` matched too — and its
"permission check" elsewhere was `can_view_experiment`, a stub returning `True`. Both are
gone. Streaming and byte ranges come from `aledb_common/fileserve.py`, shared with the
alignment routes.

### Django Apps

All apps use the `aledb_*` namespace. Key apps:

- **`aledb_experiment/`** — Core data models: `AleExperiment`, `Project`, `AleId`, `Flask`, `Isolate`, `Media`, `FreezerBox`. Central schema everything else references.
- **`aledb_import/`** — Experiment upload pipeline, with **two `.gd` parsers on two paths**:
  - CLI / breseq-directory upload — `ale_experiment.py` is the entry point; reads breseq output
    dirs (`annotated.gd` + `index.html` + `summary.html`) and parses with the vendored
    hand-rolled `gdparse/gdparse/gdparse.py` (`GDParser`), then triggers fixation/convergence/stats
    recomputation.
  - Web drag-and-drop upload — `gd_import.py` takes bare `.gd` files and parses with the
    external `genomediff` package (`GenomeDiff.read`). Each record is stored verbatim in
    `Mutation.gd_data` and round-tripped back out by `Mutation.to_gd_line()` (`aledb_seq/models.py`)
    for `gdtools APPLY`. Note `gdparse` has no `INT` type; `genomediff` does.
  - Web breseq **folder** upload — `upload_session.py` (chunked: `POST /import/uploads/`,
    `.../chunk`, `.../finalize`) stages the drop, then `breseq_folder.py` imports it. Takes
    `output/annotated.gd` plus `data/reference.{gff3,fasta}` and `data/reference.bam{,.bai}`;
    stores them under `ALEDB_STORE_DIR` keyed by database id (`aledb_common/store.py`), and
    records the shared reference as `ExperimentReference`. Samples whose reference does not
    hash-match the experiment's are rejected individually. Alignments are served with HTTP
    range support by `aledb_seq/views/alignments.py`, which resolves every path from a
    primary key rather than from anything the client sends.
  - Web breseq **folder** upload — `upload_session.py` (chunked: `POST /import/uploads/`,
    `.../chunk`, `.../finalize`) stages the drop, then `breseq_folder.py` imports it. Takes
    `output/annotated.gd` plus `data/reference.{gff3,fasta}` and `data/reference.bam{,.bai}`,
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
- **`aledb_seq/`** — Mutation models and views (accessible at `/mutations/`).
- **`aledb_fixation/`** — Fixated mutation computation.
- **`aledb_converge/`** — Convergence analysis across experiments.
- **`aledb_filter/`** — Experiment filtering UI and models.
- **`aledb_metadata/`** — Parses XPMD metadata files associated with experiments.
- **`aledb_export/`** — Data export in various formats.
- **`aledb_stats/`** — Precomputed static statistics (`StaticData` model).
- **`aledb_search/`** — Cross-experiment search.
- **`aledb_bibliome/`** — Publication/bibliography management.
- **`aledb_dashboard/`** — Dashboard views and timeline events.
- **`aledb_accounts_noauth/`** — Default auth stub: Django's built-in login/logout, no enforcement. Swap for `aledb_accounts` (brute-force protection) or any other auth app by changing `INSTALLED_APPS`.
- **`aledb_accounts/`** — Enhanced auth with `django-defender` brute-force protection. Optional; used in production (`settings_private.py`).
- **`aledb_common/`** — Shared utilities, middleware (`LoginRequiredMiddleware`), context registry, **import registry**, and global static files.
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
  `/mutations` passed `filter_type="AMP"` — a value that means **exclude** AMP, not include it.
  Both are gone and `/mutations` now renders every mutation type. `AMP` was never a separate
  feature: it is one of eight breseq/GenomeDiff types, first-class throughout the pipeline.
- `/import/add/?ale_experiment_id=<pk>` is the one place data goes in. It is scoped to an
  experiment **by primary key**, so two experiments may share a name and two people may add to
  the same one — unlike `_prepare_experiment`, whose name+person lookup forks an experiment per
  person. Use `gd_import.prepare_experiment_by_id` for anything web-facing.
  There is **no unscoped form of this page and no sidebar entry for it** — `aledb_import`
  registers no nav item. Both existed briefly and could only ever land on a page with no
  experiment to add to; without a usable `ale_experiment_id` the route is now a plain 404.
- Everything records the logged-in user; there are no person fields to fill in.

**Deletion is soft.** `Project` and `AleExperiment` carry `deleted_at`/`deleted_by`
(`SoftDeleteMixin`); only those two are flagged, and children are reached by traversal when
`./aledb purge_deleted --older-than <days>` finally removes them. `objects` is deliberately
unfiltered — a filtered default manager would silence the import paths' `get_or_create` — so
user-facing lists exclude deleted rows explicitly via `aledb_experiment.models.live()`.

### Import types are pluggable

`aledb_common/import_registry.py` is a fourth registry alongside the plugin, nav, and export
ones. An app registers what it can ingest from `AppConfig.ready()` and it appears in the Add
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

1. `./aledb upload <path>` calls `aledb_import.ale_experiment.upload_experiment()`
2. Reads breseq output dirs; parses `.gd` files via `aledb_import.gdparse.gdparse()`
   (the web drag-and-drop path instead goes through `aledb_import.gd_import` / the `genomediff` package)
3. Creates `aledb_experiment`, `aledb_seq`, and `aledb_metadata` model instances
4. Triggers `aledb_fixation.util`, `aledb_converge.util`, and `aledb_stats.util` to recompute derived data
5. Updates dashboard cache via `aledb_dashboard.util.rebuild_dashboard_data()`

### Infrastructure (production)

- ASGI server: Daphne + Django Channels
- Reverse proxy: nginx
- Cache/sessions: Redis (`django-defender` brute-force tracking)
- File storage: Azure Blob Storage mounted via blobfuse at `/data/aledata/`
- `SEQUENCING_URL` env var controls the public-facing URL prefix for sequencing result files
