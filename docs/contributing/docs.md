# Contributing to these docs

## Building

```bash
./aledb docs             # build to site/
./aledb docs --serve     # live reload at http://127.0.0.1:8001
./aledb docs --strict    # fail on a broken link or an unresolved reference
```

The first run installs `requirements-docs.txt` into `env/main`. That toolchain is deliberately
**not** in `requirements.txt`: the entry script installs that into every deployment, and a
production ALEdb has no use for a static site generator.

`site/` is git-ignored. The sources are `docs/` and `mkdocs.yml`.

## The same command builds a deployment's manual

Every command here is inherited by an assembled project — both entry scripts end at
`aledb_common.cli.manage()` — so `./mutint docs` reaches this one. It does **not** build
aledb-core's docs from inside the submodule. It builds MutInt's manual: MutInt's own pages,
plus every installed component's, merged by audience.

`aledb_common/docs_manual.py` does the collecting. In outline:

- the project is found from `ALEDB_TOOLS_DIR`, exported by the entry script and the only thing
  that knows — settings cannot, because an assembled project reaches `get_base_settings()`
  through aledb-core's `config/defaults.py`;
- components come from `about_registry.first_party_app_configs()`, so an uninstalled submodule
  contributes nothing, and **the project is excluded from its own component list** or
  aledb-core standalone would collect itself and render everything twice;
- each component's `docs/` is **symlinked** into `.docs-build/docs/<component>/` — mkdocs walks
  with `followlinks=True`, so a build can never serve a stale copy of somebody else's pages;
- a generated `mkdocs.yml` merges the navs and points `mkdocstrings.paths` at every component,
  without which a plugin's `:::` reference will not resolve.

There is one code path. Standalone, aledb-core finds no other components and the merge is a
merge of one.

## Where a fact should live

- **How something behaves** → the docstring in `aledb_common/`. The Reference pages are
  generated from those, so fixing the docstring fixes the page.
- **How to do something, and why it is done that way** → a guide page under `docs/plugin/`.
- **Why *this repo* is built the way it is**, for whoever maintains core → `CLAUDE.md`. That
  file is not part of this site and is written for a different reader.

The Reference pages are three lines each and should stay that way. Prose added there is prose
that is not next to the code it describes.

## Markdown docstrings

This is why the toolchain is MkDocs. The registry docstrings are written in markdown —
360 non-blank lines carrying 90 single-backtick code spans — and `mkdocstrings` parses them as
markdown. Sphinx's `autodoc` parses docstrings as reStructuredText, where a single backtick is
a *title reference*, so all 90 would have rendered as italics.

Keep writing them as markdown.

## The nav is explicit

`mkdocs.yml` lists every page. An inferred nav quietly absorbs a page nobody linked and hides
one that was deleted; listing them makes both a visible edit.

## What is guarded, and what is not

`aledb_common/tests/test_docs.py` fails when:

- a registry module in `aledb_common/` has no page under `docs/reference/`;
- a public `register_*` function is named nowhere under `docs/`.

Both are file reads — they do not import mkdocs, which is not installed in a normal
environment.

**Neither catches prose going out of date.** They catch a registry or a hook appearing with no
mention at all, which is the failure that actually happens; a guide describing last year's
behavior will pass. If you change how something works, search `docs/` for its name.

## Versioning

Not set up, by choice — there is one version and nothing is hosted.

When it is wanted, the tool is [`mike`](https://github.com/jimporter/mike), which is already
compatible with this configuration and needs one addition to `mkdocs.yml`:

```yaml
extra:
  version:
    provider: mike
```

Then `mike deploy 1.1 latest` builds a version into a branch and `mike serve` runs the picker.
It is deliberately not added yet: with nothing deployed, that block renders a version selector
with nothing in it.
