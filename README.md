# ALEdb

A Django web application for cataloging [Adaptive Laboratory Evolution (ALE)](https://en.wikipedia.org/wiki/Adaptive_laboratory_evolution) experiments — tracking experimental metadata, sequencing data, and genetic mutations.

**Live instance:** https://aledb.org

---

## Quick start

**Requirements:** Python 3.10+, Git

```bash
git clone <repo-url> aledb-core
cd aledb-core
./aledb start
```

`./aledb start` will:
1. Create a Python virtual environment at `env/main/` and install dependencies (run once
   on first invocation; use `./aledb install` to (re)install deps without starting)
2. Run database migrations (SQLite, no external database needed)
3. Create a default admin user (`admin` / `admin`)
4. Open your browser to `http://127.0.0.1:8000`

The admin interface is at `http://127.0.0.1:8000/admin/`. Log in with `admin` / `admin` and change the password immediately.

The app starts empty — no experiments loaded. See [Loading data](#loading-data) below.

---

## Loading data

ALEdb imports experiments from [breseq](https://github.com/barricklab/breseq) output directories. Each ALE experiment corresponds to one breseq output directory, which contains a `.gd` (genome diff) file and associated HTML result files.

### Upload experiments

```bash
./aledb upload /path/to/experiment1 /path/to/experiment2 ...
```

Each path should be the root of a breseq output directory (the one containing `output/` and `data/` subdirectories). The upload command parses the `.gd` files, creates all mutation and metadata records, and recomputes fixation and convergence statistics.

To delete experiments by ID:

```bash
./aledb delete 4 20 19
```

### Sequencing result files (`ALE_DATA_ROOT_DIR`)

breseq produces HTML reports, coverage plots, and evidence files alongside the mutation calls. ALEdb can serve these files directly so users can click through from a mutation to the underlying sequencing evidence.

Set `ALE_DATA_ROOT_DIR` to the directory that contains your breseq output directories:

```bash
export ALE_DATA_ROOT_DIR=/path/to/aledata/
./aledb start
```

ALEdb serves files from this directory at the `/aledata/` URL path. For example, if a sequencing result is at `/path/to/aledata/exp1/output/index.html`, it will be accessible at `http://127.0.0.1:8000/aledata/exp1/output/index.html`.

If `ALE_DATA_ROOT_DIR` is not set, the app runs normally but links to sequencing result files will not resolve.

### Sequencing URL prefix (`SEQUENCING_URL`)

`SEQUENCING_URL` is the URL prefix ALEdb puts in front of a sample's stored `location` when it builds a link to that sample's breseq report. If your sequencing files are hosted externally — for example, served by nginx directly from a mounted volume — set it to the public URL prefix for those files:

```bash
export SEQUENCING_URL=https://example.org/aledata/
```

To serve them through Django's own `/aledata/` route instead, point `SEQUENCING_URL` at it
explicitly — `export SEQUENCING_URL=http://127.0.0.1:8000/aledata/`. There is no implicit
fallback: when `SEQUENCING_URL` is empty, or when a sample has no stored `location`, the
sample name renders as plain text with no link. `.gd` files imported through the web uploader
always fall in the latter case, since a bare `.gd` has no breseq HTML report to link to.

Leave this unset for local development.

---

## Configuration

All configuration is via environment variables. The defaults are suitable for local development.

| Variable | Default | Description |
|----------|---------|-------------|
| `ALE_DATA_ROOT_DIR` | `ale_data_root_dir` | Filesystem path to the directory containing breseq output directories. ALEdb serves files from here at `/aledata/`. |
| `SEQUENCING_URL` | _(empty)_ | Public URL prefix for sequencing result links. Leave empty to render sample names as plain text with no report link. Note this does **not** fall back to the `/aledata/` route — set it explicitly (e.g. `http://127.0.0.1:8000/aledata/`) if you want links to go through Django. |
| `DJANGO_SECRET_KEY` | insecure dev key | Django secret key. Must be set to a long random string in any non-local deployment. |
| `DEBUG` | `0` | Set to `1` to enable Django debug mode (shows error tracebacks in the browser). |
| `DJANGO_SERVER_HOST` | `localhost` | Hostname added to `ALLOWED_HOSTS`. Set to your server's hostname or IP for non-local deployments. |
| `PUBLIC` | `0` | Set to `1` to enable read-only public access mode. |
| `GOOGLE_ANALYTICS_TAG` | _(empty)_ | Google Analytics measurement ID (e.g. `G-XXXXXXXX`). |
| `FORCE_SQLITE` | `0` | Set to `1` to force SQLite even when other database settings are present. |
| `DJANGO_SETTINGS_MODULE` | `config.settings_local` | Django settings module. Use `config.settings_private` for production with auth enforcement. |

### Settings files

| File | Purpose |
|------|---------|
| `config/settings_local.py` | Local development (SQLite, DEBUG=True). Default when using `./aledb`. |
| `config/settings_private.py` | Production with login enforcement (`LoginRequiredMiddleware`). |
| `config/settings_public.py` | Public read-only deployment. |

---

## Management commands

```bash
./aledb upload path1 path2    # upload ALE experiments from breseq output dirs
./aledb delete 4 20 19        # delete experiments by ID
./aledb shell                 # open Django shell
./aledb test                  # run test suite
./aledb makemigrations        # generate new migrations after model changes
./aledb migrate               # apply migrations
./aledb collectstatic         # collect static files to STATIC_ROOT
```

---

## Extending ALEdb

To add custom apps without modifying this repo, see [DEVELOPER.md](DEVELOPER.md) for the git submodule integration guide.

---

## Citation

If you use ALEdb in your work, please cite:

> Patrick V Phaneuf, Dennis Gosting, Bernhard O Palsson, Adam M Feist,
> *ALEdb 1.0: a database of mutations from adaptive laboratory evolution
> experimentation*, **Nucleic Acids Research**, Volume 47, Issue D1, 08
> January 2019, Pages D1164–D1171,
> <https://doi.org/10.1093/nar/gky983>

## License

ALEdb is released under the **MIT License** — free for any use, including educational, research, non-profit, and commercial. Copyright © 2015–2026 The Feist Lab. See [LICENSE](LICENSE) for full terms.
