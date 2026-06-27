# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ALEdb is a Django 5 web application for managing Adaptive Laboratory Evolution (ALE) experiments. It stores experimental data, parses genomic sequencing output (breseq `.gd` files), and provides analysis tools for mutations, convergence, and enrichment.

## Commands

**Run all tests** (auto-uses SQLite, no MySQL needed):
```bash
./test.sh
# or directly:
python3 manage.py test
```

**Run a single test**:
```bash
python3 manage.py test builder.tests.test_ale_experiment.TestEnrichment.test_reseq_URL
```

**Coverage**:
```bash
coverage run manage.py test && coverage report
```

**Django management commands** (within Docker container `aledb-web`):
```bash
python3 manage.py shell               # Django REPL
python3 manage.py makemigrations && python3 manage.py migrate
python3 manage.py upload path1 path2  # upload ALE experiments
python3 manage.py delete 4 20 19      # delete experiments by ID
python3 manage.py collectstatic
```

**Docker (primary deployment)**:
```bash
docker-compose -f docker-compose-prod-asgi-host-nginx.yml up --build -d
docker-compose -f docker-compose-prod-asgi-host-nginx.yml down
docker-compose -f docker-compose-prod-asgi-host-nginx.yml logs web
```

**Local dev without MySQL**: Set `FORCE_SQLITE=1` in `.docker/one.env` and restart containers.

## Architecture

### Django Apps

Each directory at the repo root is a Django app. Key apps:

- **`ale/`** — Core data models: `AleExperiment`, `Project`, `AleId`, `Flask`, `Isolate`, `Media`, `FreezerBox`. This is the central schema everything else references.
- **`builder/`** — Experiment upload pipeline. `ale_experiment.py` is the main entry point; it reads breseq output directories, calls `gdparse/` to parse `.gd` mutation files, then triggers fixation/convergence/stats recomputation.
- **`seq/`** — Mutation models and views (accessible at `/mutations/`).
- **`fixation/`** — Fixated mutation computation.
- **`converge/`** — Convergence analysis across experiments.
- **`filter/`** — Experiment filtering UI and models.
- **`pipeline/`** — Azure Batch pipeline management for running breseq on raw sequencing data.
- **`metadata/`** — Parses XPMD metadata files associated with experiments.
- **`export/`** and **`md_export/`** — Data export in various formats.
- **`stats/`** — Precomputed static statistics (`StaticData` model).
- **`genes/`** — Gene annotation data.
- **`enrichment/`** — Gene set enrichment analysis.
- **`search/`** — Cross-experiment search.
- **`bibliome/`** — Publication/bibliography management.
- **`dashboard/`** — Dashboard views and timeline events.
- **`accounts/`** — User auth with brute-force protection (`django-defender`).
- **`common/`** — Shared utilities and global static files (`common/staticfiles/`).
- **`aleinfo/`** — Django project config: settings, root URLs, ASGI/WSGI entry points.

### Settings Structure

- `aleinfo/defaults.py` — Base settings (all env vars read here). Contains the `DATABASES` dict for MySQL (default) and five sequencing-machine databases (ucsd_machine_one/two, dtu_machine_one/two/three).
- `aleinfo/settings_public.py` / `settings_private.py` — Extend defaults for public vs. private deployment. Select with `DJANGO_SETTINGS_MODULE`.
- Tests and `FORCE_SQLITE=1` automatically switch `DATABASES['default']` to SQLite.

### Data Flow: Uploading an Experiment

1. `python3 manage.py upload <path>` calls `builder.ale_experiment.upload_experiment()`
2. Reads breseq output dirs; parses `.gd` files via `builder.gdparse.gdparse()`
3. Creates `ale`, `seq`, and `metadata` model instances
4. Triggers `fixation.util`, `converge.util`, and `stats.util` to recompute derived data
5. Updates dashboard cache via `dashboard.util.rebuild_dashboard_data()`

### Permissions

Uses `django-guardian` for object-level permissions. `VIEW_PROJECT` permission gates access to non-public projects. `ale/permissions.py` has the grant helpers.

### Infrastructure

- ASGI server: Daphne + Django Channels (WebSocket support)
- Reverse proxy: nginx
- Cache/sessions: Redis (`django-defender` brute-force tracking)
- File storage: Azure Blob Storage mounted via blobfuse at `/data/aledata/`
- `SEQUENCING_URL` env var controls the public-facing URL prefix for sequencing result files
