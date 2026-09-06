# Quick start

mutint-core runs standalone with **no external services to provide** — it installs its own PostgreSQL under `env/`; no Docker, no
broker. The entry script builds its own virtualenv on first use.

```bash
cd mutint-core
./mutint start
```

That migrates the database, creates an admin user, opens a browser and starts the server on
port 8000. It is the whole first run.

`./mutint` bootstraps a virtualenv at `env/main/` and re-execs under it, so there is nothing to
activate. `env/` is git-ignored. If a component declares non-Python tools in a `tools.txt`,
they are installed into `env/tools/` with micromamba, downloaded as a static binary — no conda
needed on the host.

## Running an assembled project

Same idea, one directory up:

```bash
cd mutint
./mutint start
```

An assembled project collects mutint-core and its plugins as git submodules and discovers them
at startup. Every command below works the same in both.

## Reinstalling

```bash
./mutint install      # or ./mutint install
```

Rebuilds the environment from every component's `requirements.txt`. Worth running after
pulling a change that moves a dependency — particularly the `genomediff` pin, which is a git
SHA and which pip will not upgrade on the requirement alone.

## Checking it

```bash
./mutint check        # Django's system checks
./mutint test         # the full suite, ~2 minutes
```

The suite is green. Any failure is a regression, not a known state.
