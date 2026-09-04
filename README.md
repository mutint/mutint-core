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
1. Install everything it needs into `env/`, on the first invocation only: a pinned Python, a
   virtual environment built from it, the external tools components declare in their
   `tools.txt`, and PostgreSQL (use `./aledb install` to do this without starting)
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

ALEdb imports experiments from [breseq](https://github.com/barricklab/breseq) output directories. Each ALE experiment corresponds to one breseq output directory, which contains a `.gd` (genome diff) file and associated HTML result files.

### Upload experiments

```bash
./aledb import /path/to/data --project P --experiment E --person alice
```

Each path should be the root of a breseq output directory (the one containing `output/` and `data/` subdirectories). The upload command parses the `.gd` files, creates all mutation and metadata records, and recomputes fixation and convergence statistics.

To delete experiments by ID:

```bash
./aledb delete 4 20 19
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

### breseq result import (`ALEDB_STORE_DIR`)

Dropping breseq result folders on `/import/add/` uploads five files per sample --
`data/output.gd`, `data/reference.gff3`, `data/reference.fasta`,
`data/reference.bam`, `data/reference.bam.bai` -- and nothing else, so the bulk of a run
never leaves your machine. They are stored under `ALEDB_STORE_DIR`, keyed by database id:

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

### breseq result import (`ALEDB_STORE_DIR`)

Dropping breseq result folders on `/import/add/` uploads five files per sample and nothing else,
so the bulk of a run never leaves your machine:

```
<sample>/data/output.gd
<sample>/data/reference.gff3   <sample>/data/reference.fasta
<sample>/data/reference.bam    <sample>/data/reference.bam.bai
```

Large folders are uploaded in chunks with progress, so a multi-GB drop never depends on a
single long request. Files are stored under `ALEDB_STORE_DIR`, keyed by database id:

```
<store>/experiments/<experiment_id>/reference/{reference.gff3,reference.fasta,reference.fasta.fai}
<store>/samples/<sample_id>/{sample.gd,aligned.bam,aligned.bam.bai}
```

**Sample statistics come from `data/summary.json`.** breseq writes it beside `output/`;
ALEdb reads total reads, average read length, percent mapped and mean coverage from it. A
sample without that file still imports, with those statistics left at zero. They used to be
scraped out of `summary.html` by table position, which is why breseq HTML reports were once
needed at all.

**Every sample in an experiment shares one reference genome.** The first import establishes
it; a later sample whose reference does not match is rejected individually while the rest of
the batch imports.

**The sequence is the sole invariant.** Two samples belong to the same experiment when their
reference *sequence* is identical; annotation may legitimately differ in detail between
breseq runs, and that alone never causes a rejection. A folder import never rewrites the
experiment's annotation -- import order should not decide it -- but the **Replace annotation**
import type does, since that is an explicit request.

References are **normalized** before being stored or hashed: whatever arrives is converted to
one canonical pair -- a GFF3 of genes only, plus a FASTA -- so a GenBank and the GFF3 breseq
derived from the same genome compare equal rather than looking like two different references.

### Reference genomes

A bare `.gd` carries no reference, so it cannot establish one. Drop a reference on
`/import/add/` -- on its own, or alongside the data in the same drop, since the reference
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
| `ALEDB_STORE_DIR` | `<repo>/aledb_store` | Managed store that ALEdb owns: uploaded `.gd`, BAM/BAI, and per-experiment reference genomes. Paths inside it are derived from database ids, never from client input. |
| `ALEDB_UPLOAD_SESSION_TTL_HOURS` | `24` | How long a staged-but-unfinalized upload survives before `./aledb reap_uploads` removes it. |
| `ALEDB_STORE_DIR` | `<repo>/aledb_store` | Managed store ALEdb owns: uploaded `.gd`, BAM/BAI, and per-experiment reference genomes. Paths inside it derive from database ids, never from client input. |
| `ALEDB_UPLOAD_SESSION_TTL_HOURS` | `24` | How long a staged-but-unfinalized upload survives before `./aledb reap_uploads` removes it. |
| `DJANGO_SECRET_KEY` | insecure dev key | Django secret key. Must be set to a long random string in any non-local deployment. |
| `DEBUG` | `0` | Set to `1` to enable Django debug mode (shows error tracebacks in the browser). |
| `DJANGO_SERVER_HOST` | `localhost` | Hostname added to `ALLOWED_HOSTS`. Set to your server's hostname or IP for non-local deployments. |
| `PUBLIC` | `0` | Set to `1` to enable read-only public access mode. |
| `GOOGLE_ANALYTICS_TAG` | _(empty)_ | Google Analytics measurement ID (e.g. `G-XXXXXXXX`). |
| `ALEDB_DB_HOST` | _(unset)_ | **Unset means the entry script manages a PostgreSQL server under `env/`.** Set it to a hostname (or a socket directory) to use a server you run yourself, in which case nothing is provisioned, started or stopped for you. |
| `ALEDB_DB_NAME` | the checkout's directory name | Database name, e.g. `aledb_core`. |
| `ALEDB_DB_USER` | `aledb` | Database role. |
| `ALEDB_DB_PASSWORD` | _(empty)_ | Not needed for the managed server, which is socket-only and trusts the local user. |
| `ALEDB_DB_PORT` | `5432` | Ignored by the managed server, which listens on no port at all. |
| `ALEDB_ALLOW_REMOTE_TESTS` | `0` | Set to `1` to let `./aledb test` run against a server this checkout does not manage. Tests create and drop `test_<name>` on it, so this is deliberately awkward. |
| `DJANGO_SETTINGS_MODULE` | `config.settings_local` | Django settings module. Use `config.settings_private` for production with auth enforcement. |

### Settings files

| File | Purpose |
|------|---------|
| `config/settings_local.py` | Local development (`DEBUG=True`). Default when using `./aledb`. |
| `config/settings_private.py` | Production with login enforcement (`LoginRequiredMiddleware`). |
| `config/settings_public.py` | Public read-only deployment. |

---

## Management commands

```bash
./aledb import path1 path2    # import breseq dirs, .gd files and reference genomes
./aledb delete 4 20 19        # delete experiments by ID
./aledb shell                 # open Django shell
./aledb db status             # where the database is, and whether it is running
./aledb db start              # start it and leave it up across several commands
./aledb db stop               # stop it
./aledb db reset --yes        # throw the database away and start again, empty
./aledb db_worker             # run queued background work (coverage derivation)
./aledb test                  # run test suite
./aledb makemigrations        # generate new migrations after model changes
./aledb migrate               # apply migrations
./aledb collectstatic         # collect static files to STATIC_ROOT
```

---

## Extending ALEdb

ALEdb is extended by **plugins**: separate repositories, each holding one Django app, that
register themselves at startup. This repo contains no reference to any of them -- installing
one is adding a git submodule, with no file here to edit.

The full guide is the documentation site, built locally:

```bash
./aledb docs --serve      # http://127.0.0.1:8001
./aledb docs              # or build to site/
```

It covers the repository structure a plugin should use, the seven registries and what each
contributes, how to test a plugin (which happens in an assembled project, since a plugin's app
is not installed in this one), and how to assemble a project in the first place.

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
