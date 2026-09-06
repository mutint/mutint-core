# Writing a plugin for MutInt

`mutint-core` is a Django project that can run on its own or be embedded in an **assembled
project** alongside other apps. A **plugin** is one of those other apps: its own git
repository, holding one Django app, that adds something to MutInt without mutint-core knowing it
exists.

That last part is literal. **mutint-core contains no reference to any plugin** — no import, no
setting, no template include, no URL. A plugin announces itself at startup by calling
registration functions, and core renders whatever has been registered. Adding one to a
deployment is adding a git submodule; there is no core file to edit.

Several plugins exist today, and they are the worked examples this guide keeps pointing at:

| plugin | what it adds |
|---|---|
| `mutint-compare` | the cross-sample mutation table at `/compare/`, with its Show menu of convergent and fixed mutations |
| `mutint-phylogeny` | a maximum-parsimony tree over an experiment's samples |
| `mutint-needle` | the needle plot, as a panel on the Overview |
| `mutint-breseq` | breseq runs on uploaded reads |
| `mutint-api` | the public read API at `/api/` |

## What a plugin can add

Everything below is a registration, made from your `AppConfig.ready()`. Each is covered in
[The registries](../plugin/registries.md), with the full signatures under **Reference**.

- **Pages** — URL patterns, mounted under a prefix of your choosing.
- **A sidebar entry**, in the main section or the per-experiment one.
- **Derived data** — a table computed from the mutations, which core marks stale for you when
  they change.
- **A CSV export type**, appearing in the export menus.
- **An import type**, appearing on the Add Data page and in its auto-detection.
- **A section on `/about`**, describing your component and reporting its version.
- **An example dataset**, loadable with `./mutint load_example`.
- **Context for the experiment views**, without core depending on you.

## What a plugin cannot do

- **It cannot change core's behavior by being installed.** Registration is additive. If your
  plugin needs core to behave differently, that is a change to core, not a plugin.
- **It cannot assume another plugin is installed.** A deployment picks its own set. Where you
  must name one — asking for its rebuild, say — the API is built to skip a name nothing
  registered rather than raise.
- **It cannot bypass permissions.** Reading and writing an experiment's data is gated the same
  way for a plugin as for core, and a plugin that writes has an obligation core cannot enforce
  for it. See [URLs, views and permissions](../plugin/views-and-urls.md).

## Where to start

[The quickstart](../plugin/quickstart.md) builds a plugin end to end — repository, app,
registration, installed into an assembled project, tests running. It is worth doing once even
if you then throw it away, because the two things that most often go wrong are both
environmental: a plugin's tests can only run in an assembled project, and an assembled project
reaches your code through a path that has a hyphen in it.
