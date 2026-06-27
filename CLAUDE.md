# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ALEdb is a Django 5 web application for managing Adaptive Laboratory Evolution (ALE) experiments. It stores experimental data, parses genomic sequencing output (breseq `.gd` files), and provides analysis tools for mutations, convergence, and enrichment.

## Commands

**Local dev setup** (SQLite, no MySQL or Docker needed):
```bash
./aledb start             # runs migrations, creates admin user, opens browser, starts server
```

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

**Django management commands** (local):
```bash
./aledb shell
./aledb makemigrations && ./aledb migrate
./aledb upload path1 path2   # upload ALE experiments
./aledb delete 4 20 19       # delete experiments by ID
./aledb collectstatic
```

**Docker (production deployment)**:
```bash
docker-compose -f docker-compose-prod-asgi-host-nginx.yml up --build -d
docker-compose -f docker-compose-prod-asgi-host-nginx.yml down
docker-compose -f docker-compose-prod-asgi-host-nginx.yml logs web
```

## Architecture

### Django Apps

All apps use the `aledb_*` namespace. Key apps:

- **`aledb_experiment/`** — Core data models: `AleExperiment`, `Project`, `AleId`, `Flask`, `Isolate`, `Media`, `FreezerBox`. Central schema everything else references.
- **`aledb_import/`** — Experiment upload pipeline. `ale_experiment.py` is the main entry point; reads breseq output dirs, calls `gdparse/` to parse `.gd` files, then triggers fixation/convergence/stats recomputation.
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
- **`aledb_common/`** — Shared utilities, middleware (`LoginRequiredMiddleware`), context registry, and global static files.
- **`config/`** — Django project config: settings, root URLs, ASGI/WSGI entry points.

### Pluggable App Slots

Some functionality is designed to be swapped by changing `INSTALLED_APPS`:

**Auth slot** — any app with `auth_app = True` in its `AppConfig` and `app_name = 'accounts'` in its `urls.py` is auto-discovered by `config/urls.py`. Default: `aledb_accounts_noauth`. Production: `aledb_accounts`.

**Experiment context providers** — registered via `aledb_common.context_registry.register_experiment_context_provider()` in `AppConfig.ready()`. Used by `aledb_bibliome` to inject publication data into experiment views without a hard dependency.

### Settings Structure

- `config/defaults.py` — Base settings. MySQL default; SQLite fallback when `FORCE_SQLITE=1` or running tests.
- `config/settings_local.py` — Local dev (SQLite, DEBUG=True, no Redis/Azure). Created by `./aledb start`.
- `config/settings_private.py` — Production with auth enforcement and `aledb_accounts`.
- `config/settings_public.py` — Public read-only deployment.
- Select with `DJANGO_SETTINGS_MODULE`.

### Data Flow: Uploading an Experiment

1. `./aledb upload <path>` calls `aledb_import.ale_experiment.upload_experiment()`
2. Reads breseq output dirs; parses `.gd` files via `aledb_import.gdparse.gdparse()`
3. Creates `aledb_experiment`, `aledb_seq`, and `aledb_metadata` model instances
4. Triggers `aledb_fixation.util`, `aledb_converge.util`, and `aledb_stats.util` to recompute derived data
5. Updates dashboard cache via `aledb_dashboard.util.rebuild_dashboard_data()`

### Infrastructure (production)

- ASGI server: Daphne + Django Channels
- Reverse proxy: nginx
- Cache/sessions: Redis (`django-defender` brute-force tracking)
- File storage: Azure Blob Storage mounted via blobfuse at `/data/aledata/`
- `SEQUENCING_URL` env var controls the public-facing URL prefix for sequencing result files
