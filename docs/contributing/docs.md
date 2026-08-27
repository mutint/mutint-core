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
behaviour will pass. If you change how something works, search `docs/` for its name.

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
