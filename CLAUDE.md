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

**Baseline: 463 run, 0 failures.** The suite is green — treat *any* failure as yours.

It was not green for years. The last six were all in
`aledb_metadata.tests.test_metadata.TestParser` and all dated to two 2019 commits that changed
code without migrating what depended on it: `14966400` switched the metadata schema to short
keys (`A`/`F`/`I`/`R`) plus JSON blobs but migrated only the `test3/` fixtures, and `19dfc7a9`
stopped the parser writing `Media.substrate` while the tests kept asserting on it.

### Gotchas when writing tests

- **Metadata is validated on CLI upload.** `aledb_metadata/xpmdvalidator/validate.py` used to
  open with an unconditional `return True`, so every metadata directory passed. It is live now:
  a fixture that does not satisfy `Json_schema.json` will fail
  `_check_and_extract_parameters_from_metadata` and the upload returns early. Build metadata
  fixtures from `aledb_metadata/tests/test3/`, which is the canonical shape.
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

### The shell's two widths

Neither the sidebar nor the content box has a fixed width, and neither should get one back.

- `.sidebar` is `width: max-content` when open (set inline in `base.html`, and by
  `toggle_sidebar`), so it is as wide as its widest entry plus the 15px `.nav > li > a` padding
  either side. `max-width: 17vw` -- what the fixed width used to be -- keeps a long experiment
  name from pushing the page over; `overflow-x: hidden` trims it instead. Collapsed, the inline
  width is cleared and `.sidebar`'s `width: 0` is what remains.
- `#aledb-content` is `display: flow-root` and fills whatever is left beside the sidebar and the
  collapse strip. It used to be `float: left; width: 77vw` beside a 17vw sidebar, with
  `toggle_sidebar` swapping in a second guess of 95vw; neither added up, and several vw of every
  page went unused down the right-hand side. With the sidebar sized to its own content, any
  fixed width would be wrong by a different amount again.

`flow-root` rather than `overflow: hidden`: both establish the block formatting context that
stops the box sliding under the floats, but `hidden` would clip a menu that opens past the edge
-- the genome browser's sample menu is one.

**That `display: flow-root` is set inline in `base.html`, not in `common.css`, and must stay
there.** It is the whole of what keeps the content box off the sidebar, so a browser holding a
cached older copy of `common.css` renders every page with the header on top of the sidebar --
which is exactly what happened when the rule lived only in the stylesheet. Layout this
load-bearing ships with the markup that assumes it.

For the same reason aledb-core's own CSS and JS are linked with `?v={{ aledb_version }}`. A
release changes every one of those URLs, so a browser cannot serve half of one version and half
of another. Third-party CDN assets are already versioned in their paths.

`common.css` also trims the header block on every page: `.page-header`'s 40px top margin (and
the `h2`'s own, which collapses through it, since `.page-header` has no top padding or border),
its padding and margin below, and the empty `<h2>{{ error }}</h2>` slot in `base.html`, which
has no height but still contributes Bootstrap's 20px `h2` margin. That leaves 25px above the
title -- the same padding the page has at its foot -- and 16px below the header. `:empty` rather
than hiding the error heading outright, so a real error still shows.

Menus that run as long as the experiment has samples carry `.aledb-menu`, which is only
`max-height` and `overflow-y`: Bootstrap's `.dropdown-menu` already gives these menus everything
else they share with the Metadata page's column menu.

### Branding: aledb-core has none

`/` is the project list, the sidebar carries no name or version, there is no icon upper-right,
and no institution is credited. All of that was ALEdb's and now lives in the `aledb-deploy`
repo. A deployment adds its own through three seams, none of which aledb-core knows the content
of:

- `ALEDB_BRANDING` (`aledb_common/base_settings.py`) — `{'name', 'version', 'logo'}`, empty by
  default. `base.html` renders each only `{% if %}` it is set, so an unset value renders nothing
  rather than an empty element.
- `templates/home/splash.html` — `aledb_home.views.home` looks it up and falls back to
  `aledb_experiment.views.projects` on `TemplateDoesNotExist`. Rendered in place, not
  redirected, so `/` stays `/`.
- `templates/branding/footer.html` — the "hosted and maintained by…" footer, included by the
  dashboard and About pages. Core ships it **empty**; it existed as four pasted copies before.

More generally, `TEMPLATES['DIRS']` now leads with the project's `templates/` dir and
`STATICFILES_DIRS` with its `staticfiles/`, so a deployment overrides any core template or asset
by path. The source dir is `staticfiles/`, not `static/` — `static/` is `STATIC_ROOT`, and
Django raises `ImproperlyConfigured` if it appears in `STATICFILES_DIRS`. The entry is also
omitted when the directory is absent, or every `./aledb check` reports `staticfiles.W004`.

**The `Powered by ALEdb vX.Y.Z` watermark is not part of this** and has no setting. It is
aledb-core's attribution and renders on every deployment, branded or not.

### Versioning

`aledb_common/version.py` is the single source of truth — before this it existed only as the
literal `ALEdb 1.1.0` inside `base.html`. Bump with `./aledb version --bump patch|minor|major`,
which rewrites that file and leaves the `git tag v<version>` to you.

`version` is a name Django reserves: `ManagementUtility.execute()` answers it with Django's own
version before app commands are ever consulted, so the management command alone is not enough.
`aledb_common/cli.py` dispatches it directly. Delete that branch and `./aledb version` silently
starts printing Django's version instead — `aledb_common/tests/test_version.py` goes through
`manage()` rather than `call_command` precisely to catch that.


### The per-sample mutation page

`aledb_seq/views/breseq_table.py` renders one sample at `/mutations/breseq` in breseq's own
column order and colouring, as the per-sample companion to `/mutations`, which pivots the
whole experiment. A picker moves between samples; the evidence cell links into the genome
browser when the sample has a stored alignment.

The picker is the same menu as the genome browser's Samples control and the Metadata page's
column menu, but picking one sample rather than any number, so the button carries the chosen
name as its value and exactly one row is `active`. Its rows are **links, not a `<select>` in a
form**: one request per sample either way, and links need no script at all, which is what the
`<noscript>` submit button used to cover. The href is the same `?ale_experiment_id&reseq_id`
pair the form submitted.

The cell markup is not a lookalike -- it is generated by `aledb_import.annotate.display`,
the port of the code that wrote the report the sample was imported from. A row is
`{**mutation.gd_data, **mutation.annotation}` handed to `add_html_fields()`, which is why
annotation lives in one JSON column rather than twenty scalar ones: rendering is a dict
merge, not a rebuild. It is server-rendered rather than fed to DataTables as a JSON blob,
because the markup already exists by the time the view runs.

It is called **Mutations** in the nav and on the page, with `/mutations` called **Compare**
beside it and per-sample listed first; both routes keep their old names. The table markup
itself lives in `aledb_seq/templates/breseq_table/_mutation_table.html`, shared with the genome
browser, which renders the one mutation it is open at through the same `build_rows` — the two
must not drift, because the cell contents come from `annotate.display` and mean nothing without
these columns around them.

A mutation imported before a reference was available has no annotation to render and falls
back to the flat columns, with the page pointing at `./aledb reannotate`. That fallback is
the usual reason the page looks plain: nothing is wrong with the rendering, the rows simply
have no annotation yet. `Mutation.gd_data` is kept for every mutation, so `reannotate`
recomputes them in place against the stored reference -- re-importing is not needed.

### Creating and importing are pages, not dialogs

`/ale/projects/new/`, `/ale/experiments/new/` and `/import/add/` are all full pages. The
first two were modals over the list tables and are not any more: a create form wants a
heading, room to explain its fields and a URL you can link someone to. The modals were also
where two bugs lived -- an inline panel overlapped the DataTable beneath it, and Bootstrap's
data-api was bound twice so a dialog opened and closed in the same millisecond.

`?project=<pk>` on the experiment page fixes the project, so arriving from a project there
is no picker to get wrong. Each page checks permission itself -- 403 signed out, 403 for a
project you cannot edit -- rather than relying on the button being hidden. The POSTs still
go to `project_create` / `experiment_create`, which is where the real checks live.

### Which import types the Add page offers

Three rules, because the reasons differ. All of them are declared on the handler and applied
by `get_import_types_for(has_reference)`, never in the template, so the dropdown and the JSON
the page classifies a drop with cannot disagree:

- `only_without_reference` -- `reference` stops being offered once the experiment has one.
  Establishing a reference is a one-time act; replacing the *annotation* is
  `replace_annotation`, and swapping in a different genome is deliberately shell-only.
- `requires_reference` with `unavailable="hide"` -- `replace_annotation` is absent until
  there is a genome to hold fixed.
- `requires_reference` with `unavailable="disable"` -- `genomediff` is shown greyed with the
  reason. Hide what nobody would go looking for; disable what someone will arrive holding
  and needs an answer about.

`breseq_folder` is never blocked: it brings its own reference. Auto-detect is unaffected --
a reference dropped alongside data still runs first on `priority`, which is how a first drop
establishes one. `/import/types/` stays unscoped; it has no experiment to scope by.

`replace_annotation` claims only GenBank and GFF3, not FASTA. A FASTA is sequence with no
features, so there is nothing in one to install -- it used to be accepted and then rejected
on the sequence check, which named the wrong reason.

### The genome browser

`aledb_seq/views/browse.py` renders igv.js for one `ObservedMutation` at
`/mutations/browse?observed_mut_id=<pk>`, linked from every mutation-table frequency cell whose
sample has `bam_stored`. It is the first consumer of the alignment routes, which had been built
and tested with nothing pointing at them.

Two things are easy to get wrong and fail *silently* — an empty track, no error:

- **Every track needs an explicit `format:`.** The routes end `/bam`, `/fasta`, `/gff3` and
  carry no file extension; igv.js otherwise infers format from the extension.
- **Every indexed track needs an explicit `indexURL:`.** The store renames breseq's
  `data/reference.bam.bai` to `aligned.bam.bai`, so igv.js's default `<url>.bai` derivation
  would be wrong even if the URLs had extensions.

`igv.min.js` is vendored in `aledb_common/staticfiles/js/` and loaded from the browse template
only — it is ~1.4 MB and no other page needs it.

### Coverage comes from a BigWig, not from igv

A sample contributes **two** tracks: a `wig` track on its stored `coverage.bw`, and the
alignment track, at adjacent `order` values so they read as one block. The reason is that
`checkZoomIn` gates the *whole* alignment track on `visibilityWindow` — igv's own coverage row
is inside that track, so it is taken away with the reads. Coverage at a whole-genome view is
therefore impossible from the BAM alone, and asking for the reads instead is what exhausts the
tab.

The alignment track sets **`visibilityWindow: 4000`**, overriding igv's own 30 kb default, so
reads stop drawing above a 4 kb span while the BigWig keeps going at every zoom.

The **Display menu decides which tracks are loaded**, not which rows of one track are shown.
That distinction is the whole feature: *Display Coverage Only* does not load the alignment track
at all, so **no BAM request is made** — measured, 0 BAM requests against 9 for the BigWig. It
changes track identity rather than a flag, so choosing an item reloads whatever is showing.

A sample imported before coverage existed simply has no wig track until `./aledb coverage` runs.

**igv builds its entire UI inside a shadow root on `#igv-browser`**, with its own stylesheet
adopted there (`attachShadow({mode:'open'})` + `adoptedStyleSheets`). Two consequences, both of
which will waste an afternoon if you do not know them:

- **No rule in `browse.css`, or any document stylesheet, can style anything igv draws.** The
  class lands on the element and the rule silently never applies. Styling igv's internals means
  handing the rules to the shadow root — `browse.html` appends a `<style>` there for the mutant
  tint. A rule for `.igv-track-label` sitting in `browse.css` looks correct and does nothing.
- **`document.querySelector` cannot see igv's DOM.** `document.querySelectorAll('.igv-track-label')`
  returns nothing while igv is fully rendered; you have to go through
  `document.getElementById('igv-browser').shadowRoot`. Anything checking igv's rendering — a
  headless probe especially — will otherwise conclude igv never rendered at all.

The **sample menu** is a dropdown over every sample in the experiment with an alignment, the
one being viewed included: it is shown and hidden like the rest, so *Hide all samples* leaves
only the reference and gene tracks. It has the same shape as the column menu on the Metadata
page -- DataTables' colvis collection -- a `ul.dropdown-menu` of `<li><a>` where a showing
sample is `active` on its `<li>`, so Bootstrap's own `.dropdown-menu > .active > a` paints the
row and there is nothing to restyle. (DataTables does put `#717171` on the active `<li>`, but
its `<a>` covers the row, so that grey is never the colour you see; do not copy it.) The rows
are links only so Bootstrap styles them, which is why the click handler stops the default as
well as the propagation. Three things about it are load-bearing:

- A sample's track is held as the **promise** of it, not the track. *Show all samples* fires
  every `loadTrack` at once, and a plain "have I got it yet" test would load the same BAM twice.
- Every track config carries an explicit **`order`**, or igv appends a re-shown sample at the
  bottom of the stack instead of returning it to its place in the menu.
- The menu subscribes to igv's **`trackremoved`**, because igv removes tracks by itself too —
  its per-track gear menu has *Remove track*, and a track that fails to load is discarded the
  same way. Without it the box stays ticked for a sample that is no longer on screen.

Every alignment track also sets **`showSoftClips: true`**, which igv defaults to off. The page
exists to look at one called position, and a clipped end is often what explains the call. It is
the same flag the track's gear menu toggles, so it can still be turned off per track.

Beside it a **Display** menu sets reads only, coverage only, or both -- on every alignment
track showing, and as the default the next one loads with. It is deliberately stateless: igv's
own gear menu can change a single track afterwards, so no one value would be true of them all,
and the menu never marks a current choice. The flags are igv's own `showCoverage` and
`showAlignments`, the pair its gear menu toggles, so a track configured with them starts the way
the menu would set it.

**Do not copy igv's own height recipe** for that menu. It adds `coverageTrackHeight` to
`alignmentTrack.height`, which is the reads' *current* box rather than the space they are owed,
so a round trip through coverage-only ratchets a track down and leaves it there -- measured at
300px becoming 100px. Remember the height the track had while its reads were showing and hand
igv that total instead; it divides the space itself.

A `*` marks the samples the mutation is **called** in, using the mutation table's own rule
(`breseq_present or gatk_present`) rather than a second one, so it agrees with the filled cells
back on `/mutations`. An ObservedMutation row alone is not a call: one with `present=False`
records that the mutation was looked for and found absent. The same `*` is prefixed to the igv
track name, so a stack of pileups says which of them carry the call — igv puts no constraints on
a track name. In the menu a sample without one gets a same-width empty span so the names stay in
a column; on the track there is deliberately no such padding, because an igv track label is its
own shrink-to-fit badge with centred text and has no column to align to.

All four margins round the browser measure the same 25px; the header trim that makes the top
one work is in `common.css` and applies to every page (see **The shell's two widths**).

The cell markup is coupled to two things that substring-test it: `_contains_mutation` decides
whether a row renders by looking for `true`, and `table_template.js` colours a cell by testing
for `class="true"`. Keep that class on the anchor, and keep `true` out of the empty-cell literal.

### Django Apps

All apps use the `aledb_*` namespace. Key apps:

- **`aledb_experiment/`** — Core data models: `AleExperiment`, `Project`, `AleId`, `Flask`, `Isolate`, `Media`, `FreezerBox`. Central schema everything else references.
- **`aledb_import/`** — Experiment upload pipeline. **Every path ends in `gd_import`**, so a
  CLI upload and a web drop produce identical rows:
  - `gd_import.py` parses with the external `genomediff` package (`GenomeDiff.read`) and is
    the one place mutations are stored. Each record is kept verbatim in `Mutation.gd_data`
    and round-tripped back out by `Mutation.to_gd_line()` (`aledb_seq/models.py`) for
    `gdtools APPLY`, so nothing but the raw record may go in that field.
  - CLI upload — `./aledb upload <path>` walks for `<exp>/breseq/` + `<exp>/metadata/`,
    hands the folders to `breseq_folder`, then parses the metadata. It used to be a second
    importer that read the .gd plus the breseq HTML report and shared no code with
    the web paths; that is gone, along with `upload.py`.
  - The vendored `gdparse/gdparse/gdparse.py` (`GDParser`) survives only for the annotation
    test fixtures. Note it has no `INT` type; `genomediff` does.
  - **Annotation is internal** (`annotation.py` + `annotate/`). Gene, codon and amino-acid
    fields are derived from the experiment's stored reference at import, not read out of
    the `.gd`, so breseq's plain `output.gd` is enough and `gdtools ANNOTATE` is not needed.
    `./aledb reannotate <id> [--ref FILE]` recomputes them when a better reference arrives.
    The annotator is a port of breseq's own, checked against `gdtools` output.
  - Web breseq **folder** upload — `upload_session.py` (chunked: `POST /import/uploads/`,
    `.../chunk`, `.../finalize`) stages the drop, then `breseq_folder.py` imports it. Takes
    `data/output.gd` plus `data/reference.{gff3,fasta}` and `data/reference.bam{,.bai}` --
    everything from the sample's `data/` folder, `output/` is not consulted;
    stores them under `ALEDB_STORE_DIR` keyed by database id (`aledb_common/store.py`), and
    records the shared reference as `ExperimentReference`. Samples whose reference does not
    hash-match the experiment's are rejected individually. Alignments are served with HTTP
    range support by `aledb_seq/views/alignments.py`, which resolves every path from a
    primary key rather than from anything the client sends.
  - Web breseq **folder** upload — `upload_session.py` (chunked: `POST /import/uploads/`,
    `.../chunk`, `.../finalize`) stages the drop, then `breseq_folder.py` imports it. Takes
    `data/output.gd` plus `data/reference.{gff3,fasta}` and `data/reference.bam{,.bai}`,
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
ones (`aledb_common/about_registry.py` is the fifth). An app registers what it can ingest from `AppConfig.ready()` and it appears in the Add
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

1. `./aledb upload <path>` calls `aledb_import.ale_experiment.upload_ale_collection()`
2. Hands each `<exp>/breseq/` to `aledb_import.breseq_folder`, which parses via
   `aledb_import.gd_import` / the `genomediff` package -- the same route a web drop takes
3. Creates `aledb_experiment`, `aledb_seq`, and `aledb_metadata` model instances
4. Triggers `aledb_fixation.util`, `aledb_converge.util`, and `aledb_stats.util` to recompute derived data
5. Updates dashboard cache via `aledb_dashboard.util.rebuild_dashboard_data()`

### Infrastructure (production)

- ASGI server: Daphne + Django Channels
- Reverse proxy: nginx
- Cache/sessions: Redis (`django-defender` brute-force tracking)
- File storage: `ALEDB_STORE_DIR`, keyed by database id (`aledb_common/store.py`)
