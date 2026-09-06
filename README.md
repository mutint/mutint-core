# MutInt

A Django web application for the mutations found by sequencing evolved microbial populations -- most often [adaptive laboratory evolution (ALE)](https://en.wikipedia.org/wiki/Adaptive_laboratory_evolution) experiments analysed with [breseq](https://github.com/barricklab/breseq). It stores each experiment's reference genome, populations and samples, the mutations called in each, and the tools to compare, curate and export them.

**Live deployment:** [ALEdb](https://aledb.org), built on this platform.

---

## Quick start

**Requirements:** a `python3` to run the entry script (3.9 or later; the script provisions its own pinned interpreter), Git

```bash
git clone <repo-url> mutint-core
cd mutint-core
./mutint start
```

`./mutint start` will:
1. Install everything it needs into `env/`, on the first invocation only: a pinned Python, a
   virtual environment built from it, the external tools components declare in their
   `tools.txt`, and PostgreSQL (use `./mutint install` to do this without starting)
2. Start a private PostgreSQL server and create the database
3. Run database migrations
4. Create a default admin user (`admin` / `admin`)
5. Open your browser to `http://127.0.0.1:8000`

**Nothing has to be installed first, and nothing is installed outside this directory** --
not Python, not PostgreSQL. The database listens on a unix socket inside `env/`, not on the
network, so several checkouts never collide.

The admin interface is at `http://127.0.0.1:8000/admin/`. Log in with `admin` / `admin` and change the password immediately.

The app starts empty — no experiments loaded. See [Loading data](#loading-data) below.

---

## Loading data

MutInt imports [breseq](https://github.com/barricklab/breseq) output directories, bare `.gd` (genome diff) files, VCF files and reference genomes, from the browser (the experiment's **Import data** page) or the shell. Each sample is one breseq run.

### Import from the shell

```bash
./mutint import /path/to/data --project P --experiment E --owner alice
```

Each path is a breseq output directory (the one containing `output/` and `data/`), a `.gd` file, a VCF or a reference genome; the same handlers the Import data page uses decide what each is. Importing records every mutation call and annotates it against the experiment's reference.

To delete experiments by ID:

```bash
./mutint delete 4 20 19
```

### Viewing alignments

Click a frequency number in the mutation table to open a genome browser (igv.js) at that
mutation's position, showing the read pileup for that sample. Other samples in the experiment
can be added as extra tracks to compare them at the same locus.

A sample only has an alignment if it was imported as a **breseq result folder** — that is what
carries `data/reference.bam`. A sample imported from a bare `.gd` has mutation calls but no
reads, and its frequencies render as plain text rather than links.

The browser reads BAM/BAI directly over HTTP range requests from
`/mutations/alignments/<id>/{bam,bai}`, with the reference from
`/mutations/reference/<experiment_id>/{fasta,fai,gff3}`. Every request is permission-checked
like any other page, so a browser session sees exactly the experiments its user can view.

### breseq result import (`MUTINT_STORE_DIR`)

Dropping breseq result folders on `/import/` uploads five files per sample --
`data/output.gd`, `data/reference.gff3`, `data/reference.fasta`,
`data/reference.bam`, `data/reference.bam.bai` -- and nothing else, so the bulk of a run
never leaves your machine. They are stored under `MUTINT_STORE_DIR`, keyed by database id:

```
<store>/experiments/<experiment_id>/reference/{reference.gff3,reference.fasta,reference.fasta.fai}
<store>/samples/<sample_id>/{sample.gd,aligned.bam,aligned.bam.bai}
```

Every sample in an experiment must share a reference; the first import establishes it and a
later mismatch rejects that sample while the rest of the batch proceeds. Alignments are
served with HTTP range support, so a genome browser can read them:

```
/mutations/alignments/<sample_id>/bam
/mutations/alignments/<sample_id>/bai
/mutations/reference/<experiment_id>/{fasta,fai,gff3}
```

Access is gated by project permissions.

### Reference genomes

A bare `.gd` carries no reference, so it cannot establish one. Drop a reference on
`/import/` -- on its own, or alongside the data in the same drop, since the reference
import type runs first whatever order the files arrive in. Accepted:

| Format | Extensions | Notes |
|--------|-----------|-------|
| GenBank | `.gbk` `.gb` `.gbff` | sequence and annotation; parsed with Biopython |
| GFF3 | `.gff` `.gff3` | must include its `##FASTA` section |
| FASTA | `.fa` `.fasta` `.fna` | sequence only, no genes |

Importing a `.gd` into an experiment with no reference reports the file as unimported and
tells you to add one first.

**Replace annotation** is a separate import type, offered only once an experiment has a
reference. It refreshes the gene annotation from a new GenBank or GFF3 while holding the
sequence fixed: a file whose sequence differs is refused rather than applied. There is
deliberately no UI for replacing the *sequence* -- it is the invariant every sample is
hash-checked against -- though `reference_store.establish_or_check(..., replace=True)`
remains available from the shell.

Alignments are served with HTTP range support, so a genome browser can read them, gated by
project permissions:

```
/mutations/alignments/<sample_id>/bam
/mutations/alignments/<sample_id>/bai
/mutations/reference/<experiment_id>/{fasta,fai,gff3}
```



---

## Configuration

All configuration is via environment variables. The defaults are suitable for local development.

| Variable | Default | Description |
|----------|---------|-------------|
| `MUTINT_STORE_DIR` | `<repo>/data/store` | Managed store that MutInt owns: uploaded `.gd`, BAM/BAI, and per-experiment reference genomes. Paths inside it are derived from database ids, never from client input. |
| `MUTINT_UPLOAD_SESSION_TTL_HOURS` | `24` | How long a staged-but-unfinalized upload survives before `./mutint reap_uploads` removes it. |
| `DJANGO_SECRET_KEY` | insecure dev key | Django secret key. Must be set to a long random string in any non-local deployment. |
| `DEBUG` | `0` | Set to `1` to enable Django debug mode (shows error tracebacks in the browser). |
| `DJANGO_SERVER_HOST` | `localhost` | Hostname added to `ALLOWED_HOSTS`. Set to your server's hostname or IP for non-local deployments. |
| `GOOGLE_ANALYTICS_TAG` | _(empty)_ | Google Analytics measurement ID (e.g. `G-XXXXXXXX`). |
| `MUTINT_DB_HOST` | _(unset)_ | **Unset means the entry script manages a PostgreSQL server under `env/`.** Set it to a hostname (or a socket directory) to use a server you run yourself, in which case nothing is provisioned, started or stopped for you. |
| `MUTINT_DB_NAME` | the checkout's directory name (`mutint_core`), set by the entry script; `mutint` when settings are loaded any other way | Database name. |
| `MUTINT_DB_USER` | `mutint` | Database role. |
| `MUTINT_DB_PASSWORD` | _(empty)_ | Not needed for the managed server, which is socket-only and trusts the local user. |
| `MUTINT_DB_PORT` | _(empty)_ | Ignored by the managed server, which listens on no port at all. |
| `MUTINT_ALLOW_REMOTE_TESTS` | `0` | Set to `1` to let `./mutint test` run against a server this checkout does not manage. Tests create and drop `test_<name>` on it, so this is deliberately awkward. |
| `DJANGO_SETTINGS_MODULE` | `config.settings_local` | Django settings module. Use `config.settings_private` for production with auth enforcement. |
| `MUTINT_NCBI_EMAIL`, `MUTINT_NCBI_API_KEY` | _(empty)_ | Identify this installation to NCBI E-utilities, which the Reference page's sequence check and the NCBI viewer use; without them NCBI rate-limits harder. |
| `MUTINT_NCBI_TIMEOUT`, `MUTINT_NCBI_MAX_BASES` | `30`, `50000000` | Seconds to wait on NCBI, and the longest sequence the check will fetch. |
| `MUTINT_TOOLS_DIR` | `<repo>/env/tools` | Where the entry script installed components' `tools.txt`; set by the script, read by `mutint_common.tools`. |

### Settings files

| File | Purpose |
|------|---------|
| `config/settings_local.py` | Local development (`DEBUG=True`). Default when using `./mutint`. |
| `config/settings_private.py` | Production with login enforcement (`LoginRequiredMiddleware`). |

---

## Management commands

```bash
./mutint import path1 path2    # import breseq dirs, .gd files and reference genomes
./mutint delete 4 20 19        # delete experiments by ID
./mutint shell                 # open Django shell
./mutint db status             # where the database is, and whether it is running
./mutint db start              # start it and leave it up across several commands
./mutint db stop               # stop it
./mutint db reset --yes        # throw the database away and start again, empty
./mutint db_worker             # run queued background work (coverage derivation)
                              #   `./mutint start` runs one for you; this is for everywhere else
./mutint reap_jobs             # discard queue rows no worker ever claimed
./mutint test                  # run test suite
./mutint makemigrations        # generate new migrations after model changes
./mutint migrate               # apply migrations
./mutint collectstatic         # collect static files to STATIC_ROOT
```

---

## Extending MutInt

MutInt is extended by **plugins**: separate repositories, each holding one Django app, that
register themselves at startup. This repo contains no reference to any of them -- installing
one is adding a git submodule, with no file here to edit.

The full guide is the documentation site, built locally:

```bash
./mutint docs --serve      # http://127.0.0.1:8001
./mutint docs              # or build to site/
```

It covers the repository structure a plugin should use, the nine registries and what each
contributes, how to test a plugin (which happens in an assembled project, since a plugin's app
is not installed in this one), and how to assemble a project in the first place.

---

## Citation

MutInt grew out of ALEdb. If you use it in your work, please cite:

> Patrick V Phaneuf, Dennis Gosting, Bernhard O Palsson, Adam M Feist,
> *ALEdb 1.0: a database of mutations from adaptive laboratory evolution
> experimentation*, **Nucleic Acids Research**, Volume 47, Issue D1, 08
> January 2019, Pages D1164–D1171,
> <https://doi.org/10.1093/nar/gky983>

## License

MutInt is released under the **MIT License** — free for any use, including educational, research, non-profit, and commercial. Copyright © 2015–2026 The Feist Lab. See [LICENSE](LICENSE) for full terms.
