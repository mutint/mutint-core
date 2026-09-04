# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ALEdb is a Django 6.1 web application for managing Adaptive Laboratory Evolution (ALE)
experiments. (This said "Django 5" for a long time while 4.2 was what installed, and then said 4.2 while
the pin moved under it. `requirements.txt` pins `Django>=6.1,<6.2`; check it rather than this
sentence. The upgrade's whole user-visible surface was four things: `USE_L10N` gone,
`CheckConstraint(check=)` renamed to `condition=`, the SQLite backend subclass replaced by
`OPTIONS={'transaction_mode': 'IMMEDIATE'}` and then deleted with SQLite itself, and the
sidebar's Logout -- which really was a GET link that 5.0 turned into a 405, exactly as the
comment above it had predicted for two years.)

It stores experimental data, parses genomic sequencing output (breseq `.gd` files), and
provides analysis tools for mutations, convergence, and enrichment.

**Python is 3.13 and PostgreSQL is the only backend**, both provisioned by the entry script
rather than taken from the host: Django 6.1 requires Python 3.12+ and a current macOS ships
3.9. See **The database** in the suite `CLAUDE.md` for the whole design, including why running
anything outside `./aledb` will not find the database.

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

**The suite takes about two minutes.** Measured on PostgreSQL 18 under Python 3.13: 118s
standalone, 121s assembled. It was 140s standalone on Python 3.9 and *57s* on 3.12 with
Django 4.2 -- the interpreter made it two and a half times faster and Django 6.1 gave most of
that back, which is what a framework raising its default password-hasher iterations looks like
in a suite where two modules call `set_password` per user in `setUp`. Per-app it runs from 1.9s (`aledb_stats`, 32 tests) to 96s (`aledb_experiment`,
416) -- and that one app is two thirds of the whole run. Inside it `test_access_views` is 57s
and `test_groups` 34s, 90 of its 96 between them: both call `User.set_password` per user in
setUp, and Django 4.2's default PBKDF2 hasher costs a few tenths of a second every time.
Nothing else in the suite pays that, and a test-only `PASSWORD_HASHERS` override is the usual
answer if it ever becomes worth fixing. The test database is `test_<name>` on the cluster the
entry script starts under `env/`, created and dropped per run.

**Tests refuse to run against a database this checkout does not manage.** `base_settings`
raises `ImproperlyConfigured` unless `ALEDB_DB_MANAGED=1` (which only the entry script sets)
or `ALEDB_ALLOW_REMOTE_TESTS=1`. The old guard swapped in SQLite, which is not a thing that
can protect anything now: the suite creates and drops a real database on whatever server is
configured, and that server could be a deployment's.

**This said "~13 seconds" for a long time, and was wrong by an order of magnitude** -- the
same drift the test *count* above warns about, in the figure right beside it. What made it
worth correcting is that the explanation travelled with it: *"most of that is Django starting
up, not the tests"* was true at 13 seconds and is not true now. Startup is a couple of
seconds; the tests are the rest. Measure it rather than adjusting it by what you think you
added.

**A full run outlasts a two-minute command timeout**, which is new and is the first thing to
suspect when a run appears to die near the end having printed nothing. Give it a longer
timeout, or run one app at a time -- everything except `aledb_experiment` finishes in under
half a minute.

**If a test run appears to hang, that aside, it is almost certainly not the tests.** Two
things cause it:

1. *Chaining the run onto slow setup.* `patch files && migrate && ./aledb test` can blow a
   command timeout in the earlier steps, and the run looks stuck when it never really started.
   Run `./aledb test` as its own command.
2. *Killing your own shell.* `ps aux | grep "[z]sh -c source" | xargs kill` matches the wrapper
   of the command currently running it, so it kills itself and exits 144. If you want to clear
   a genuinely orphaned run, match on the Python process (`pkill -f "django test"`) instead.

**Baseline: 1636 run, 0 failures** standalone; **1817** in an assembled project, where the
plugins' own tests join them. They were 1646 and 1827 before the platform move, which deleted
six test modules that drove named migrations through the real executor and added the lifecycle,
task and round-trip tests that replaced them -- **re-measured, not arithmetic**. They were 1620 and 1801 before the account pages -- the login
page's normalisation, the local change-password page and the sidebar's admin link -- which
added 24 tests to a corner that had none at all.
**The standalone figure went down and the assembled one up** at the entry before that,
which is what extracting a component looks like: the needle plot's 22 tests left this repo with
the plot and 25 run in aledb-needle, and 8 new ones cover `panel_registry` here. They were 1634
and 1790 before that, 1625 and 1781 before the needle plot got a sequence
picker, 1622 and 1778 before a reference stopped reporting a
mutation count, 1621 and 1777 before the sample menu started toggling,
1620 and 1776 before the reads track stopped drawing
its own coverage row, 1610 and 1766 before coverage started weighting each
alignment by breseq's X1 redundancy tag, 1599 and 1755 before the genome browser dropped its
per-sample track and learned to switch mutation on a click, 1592 and 1748 before deleting data
made you type DELETE, 1587 and 1743 before the needle plot learned what
genome it was drawing, 1574 and 1730 before the database tracks, and 1568 and 1724 before the
assets were vendored. (The 1723 recorded a
commit earlier was measured before the `data-autoload` test existed; the assembled suite has
been re-run, not adjusted.) They were 1512 and 1668 before the NCBI Sequence Viewer, and
**both of those are re-counts, because this line was wrong when the viewer was written**: it
said 1437 and 1580 while the suites actually ran 1512 and 1668. Seventy-five and eighty-eight
tests had been added without anybody re-counting -- the trap the paragraph below warns about,
sprung again. The viewer itself adds 55, measured by running the suite with its two test
modules moved aside and again with them back, in both projects. Every figure before this
point in the list is therefore suspect by an unknown amount, and only worth reading as
history.
They were 1424 and 1567 before ownership was resynchronised on
demotion, 1405 and 1548 before the gene-name separator and the
1,000-gene limit, 1402 and 1545 before the Gene cell's wrapper was
closed on both branches, 1397 and 1540 before the gene-list Show button's
handler moved out of one template, 1391 and 1534 before editing and deleting became two
tabs, 1387 and 1530 before the Edit page learned to open on the sample it was linked from, and
1375 and 1518 before it learned to edit a mutation in some of its samples rather than all of
them. (The assembled figure was *run*, not
added up -- with `PYTHONPATH` pointed at this checkout, since `mutint/aledb-core` is a submodule
clone of the last commit. See the trap two sentences down for why the arithmetic is not
trusted, even when it agrees as it does here.)
They were 1305 and 1441 before the Add page learned to report
an import sample by sample -- and that assembled figure is a re-count, not arithmetic: 1441
plus the 31 tests this added is 1472, which is seven short, so the plugins had gained tests
that nobody had re-counted. It is the trap this paragraph already warns about, sprung again.
They were 1373 and 1516 before the progress poll stopped
writing, 1351 and 1494 before imports stopped losing
samples to each other, 1347 and 1490 before the page learned to
stop polling a finished import, 1340 and 1483 before the import
progress polling met SQLite's rollback journal, 1336 and 1479 before two breseq folders of
one name stopped collapsing into one sample, and 1268 and 1404 before the ALE and the isolate became
text columns, and 1257 and 1393 before an owner learned to leave a project by transferring
rather than by removing themselves, and before `locked_reason` went.
The count went *down* because the shared filter's model tests went
with the model; 22 new ones cover the reader's filter and its session. They were 1287 and 1424
before filtering became per-reader, 1264 and 1401 before functional change moved onto
`snp_type`, and 1239 and 1370 before the dashboard stopped filtering and `aledb_stats` stopped
storing -- **19 of that earlier jump is `aledb_dashboard`'s tests running for the first time**,
see the `__init__.py` gotcha below, so the derived-table removals added fewer than the
arithmetic suggests. They were 1213 and 1344 before the global filter went and the filter
summary arrived, 1201 and 1332 before the collected manual, 1197 and
1328 before `./aledb docs` learned to refuse,
1190 and 1321 before the plugin API docs, 1178 and
1293 before the tree learned to go stale,
1163 and 1278 before the lazy-rebuild sweep, 1156 and
1271 before the frequency cutoff was fixed,
1136 and 1251 before the mutation-change page, 1116
and 1231 before the cross-sample grid, 1109 and 1222 before the caller flags were dropped,
1081 and 1194 before the genomediff bump, 1028 and 1141
before the experiment lock and the bulk sharing editor, 938 and 1051 before the add form, and
852 and 965 before the mutation editor itself.

**An assembled project's venv needs the genomediff pin installed too**, and `./mutint install`
will not do it on its own — pip sees an installed 0.4.2 and leaves it. Use the
`--force-reinstall` line from `requirements.txt` against `mutint/env/main/bin/pip`, or the
suite runs green against the wrong package. The suite is green — treat *any* failure as yours.
(These said 642 and 688 for a while and were wrong by more than the sharing work added —
standalone was already 698 before it. Re-count rather than adjusting the number by what you
think you added.)

**A bare `test` runs the installed first-party apps, not whatever discovery finds.**
`aledb_common/test_runner.py` substitutes them when no labels are given. Standalone this
changes nothing; in an assembled project it is the difference between running the suite and
not. `./mutint test` used to report `Ran 0 tests ... OK` — unittest discovery walks the
working directory, and an assembled project's code lives in submodule directories named
`aledb-core`, `aledb-compare` and so on, which can never be Python packages, so discovery
could not descend into them however they were laid out. Renaming them would not have helped:
a directory is skipped unless it holds an `__init__.py`, and giving one to a submodule root
would make every app importable by two dotted paths at once — `aledb_sample.models` and
`aledb_core.aledb_sample.models` are two module objects, which means two sets of model classes.

The app set comes from `about_registry.first_party_app_configs()`, the same predicate the
About page inventories with, so "which apps are ours" is stated once.

**Core's tests must not assert on what is *absent* from a shared registry.** Nav entries,
About sections and export types are contributed by whatever is installed, so
`assertNotIn("Compare", nav_labels)` is a statement about the install set, not about core —
it passes standalone and fails the moment a plugin is added. Assert core's own registrations,
and leave a plugin's to the plugin. Three tests said otherwise and were wrong; the About one
now counts entries per *checkout*, which is the invariant it always meant.

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
- **`Project.objects.create()` is enough now, and used to not be.** `can_view_project` read
  the django-guardian grant and never `Project.user`, so a project could name an owner who
  could not open it, and five test modules carried a `POST /project/create/` workaround
  for it. `effective_role` reads `Project.user` directly, so that trap is gone. Call
  `set_primary_owner(project, user)` when you want the grant row as well — it is what the
  create view, the CLI importer and `load_example` all use.
- **Access is explicit; staff have no blanket read.** `can_view_project` used to end
  `return bool(user.is_staff)`. A test that gives someone `is_staff=True` and expects them to
  see a project is asserting the old behaviour.
- **A `tests/` directory with no `__init__.py` is not run at all, and says nothing.**
  `aledb_dashboard/tests/` had none, so its nine tests had never run in the suite -- and the
  symptom is not a failure, it is a total that is quietly nineteen short. A bare `./aledb test`
  runs *app labels* (see above), and label discovery cannot descend into a directory that is
  not a package; naming the module explicitly (`./aledb test aledb_dashboard.tests.test_x`)
  works fine, which is what makes it invisible when writing a new test. `./aledb test
  <app_label>` reporting `Found 0 test(s)` for an app that plainly has tests is the tell.
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
./aledb ncbi_accessions --list            # which contigs are confirmed NCBI records
./aledb ncbi_accessions 4 --seq-id NC_000913 --accession NC_000913.3
```

An accession is normally recorded in the web UI instead, on an experiment's **Reference**
page; the command is for bulk work and for a deployment being set up from a shell.

Migrations are tracked in version control. Commit the files generated by
`./aledb makemigrations` alongside the model changes that produced them.

**There is no Docker in this repo.** This section used to give
`docker-compose -f docker-compose-prod-asgi-host-nginx.yml up`; that file does not exist here
and neither does any other compose file, Dockerfile or Procfile. It described the pre-refactor
deployment. See **Infrastructure (production)** below.

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

**The title and the page's content share one left edge, at `#aledb-content`'s 25px padding.**
Two things used to break that, and they broke it in opposite directions, which is why the
misalignment looked inconsistent rather than uniform:

- The header block sits in a Bootstrap `.col-lg-12`, and a grid column carries a 15px gutter
  each side -- so the title alone stood 15px further in.
- A `.row` placed straight into `#aledb-content` bleeds 15px *out*, because its -15px margins
  are meant to be cancelled by the 15px padding of a `.container`, and this box is not one --
  it has 25px of its own. Most pages use `.row` as a plain wrapper round a form or a table,
  so their content sat left of the title while the edit pages, which use no row, sat flush.
  The Add Data page manages the same trick one level up with a bare `.col-lg-8`.

`common.css` flattens the gutters that have nothing to cancel them: the header's column, a
row that is a direct child, and a column used outside a row. A genuine multi-column row keeps
its inter-column gutters -- only the outermost edges go -- so the Overview page's three panels
still breathe. Measured across twelve pages with headless Chrome, all now at the same x.

**The paginate control lays its buttons out with flex, not floats.** Bootstrap 3 builds
`.pagination` as an inline-block `ul` whose `li`s are `display: inline` and whose `a`s are
floated, and the shrink-to-fit width that gives the `ul` came out one button short -- so
**"Last" sat on a second line** on every page with a paged table, at any window width, with
any amount of empty space to its right. It is not a width problem and moving things around
does not fix it; `display: inline-flex` on the `ul` sizes the row to its contents and takes
the float arithmetic out of it.

Worth knowing because it looks like a consequence of whatever was last changed nearby, and
is not: it was measured identical before and after the alignment rules above.

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

**A list whose highlight is its selection carries `.aledb-select-list` as well**, and
`aledb_common/staticfiles/js/aledb_select_list.js` drives every one of them -- the genome
browser's sample menu and the three mutation-editor pages that pick a set of samples. `active`
on the `<li>` is the selection; there is no checkbox anywhere to hold a second opinion about it.
The gestures are the ones a list normally has: plain click selects only that row, ctrl/cmd
toggles one, shift takes the range from the anchor, ctrl/cmd+shift adds a range.

**`{toggle: true}` is the other mode, and the genome browser's sample menu is why it exists.**
There every click toggles the row it lands on and shift adds a range -- a list of checkboxes
rather than a selection. The distinction is whether the rows are independent of one another:
the mutation editor's three lists are choosing *a* set of samples to act on, where "only this
one" is a useful gesture, while each row of the browser's menu is a BAM that is either loaded
or not, and there a plain click silently unloaded every other sample to show one. Getting back
then meant reloading each by hand. The default stays select-only, so nothing but the browser
changed.

Two things the CSS has to do that are easy to miss -- `user-select: none`, or shift-click drags a text
selection across the rows it is selecting; and the fill written on `> li.active > a` rather than
on the `<li>`, for the reason in the next paragraph.

**That reason was a live bug, not a hypothetical.** `common.css` carried
`.active, .dot:hover { background-color: #717171 }` -- a rule about *carousel dots*, written so
that it painted grey behind **any** element carrying the class. Bootstrap puts `active` on a
selected nav tab, a selected dropdown row and the current sidebar entry. Mostly the inner `<a>`
covered it and nobody saw it. On a nav tab it did not: `.nav-tabs > li > a` has
`margin-right: 2px` and no bottom border, so 2px of grey showed down the right-hand side and
along the foot of the *selected tab on every tabbed page in the suite* -- which reads as a badly
drawn drop shadow, and was reported as one. It is `.dot.active` now. Nothing renders
`class="dot"` any more, so the block it belongs to is dead as it stands; narrowing the selector
is the fix for the bug, and removing the carousel is somebody else's commit.

**The gap from the column beside it is on the column, not on the list.** These pages lay a
form or a table beside the sample list as two bare `col-lg-*` under `#aledb-content`, and the
rule a few paragraphs up zeroes a bare column's gutter so its content lines up with the page
title -- which leaves the list touching the form, measured at 0px. The column carrying the list
puts 30px back on its left, which is what Bootstrap puts between two columns anyway. Doing it on
the list instead would indent it away from whatever else the column holds, which on the Change
page is the table underneath.

The browser's menu is a dropdown and gets its row box and fill from Bootstrap; the editor's
three are not, and cannot borrow `.dropdown-menu` to get them -- `position: absolute; display:
none` comes with it. So `.aledb-select-list` writes both out to match. `select_list.html` in
`aledb_common/templates/` is the markup the editor's three share; the browser writes its own
rows, because they carry a track's URLs and a mutant flag.

### The account pages, and the sidebar block that reaches them

Login, logout and changing a password. The routes are `aledb_common/account_urls.py` and the
templates `aledb_common/templates/accounts/`, shared by both auth apps -- see **Auth slot**
below for why they are not in the slot.

**`registration/` is a template namespace `django.contrib.admin` owns, and this is the trap to
know.** Django's `PasswordChangeView` defaults to `registration/password_change_form.html`, and
admin ships a file at exactly that path -- along with `password_change_done.html`,
`logged_out.html` and the four `password_reset_*` ones. `django.contrib.admin` is **first** in
INSTALLED_APPS, so with `APP_DIRS=True` its copy wins over any app registered later, whatever
that app intended. The failure is not an error: the page renders, correctly, in admin's chrome.
Every template here is therefore named `accounts/...` and passed explicitly as `template_name=`,
which sidesteps the race rather than depending on app order. `test_accounts` asserts
`assertTemplateNotUsed('registration/password_change_form.html')` as the tripwire.

**`PasswordChangeView.success_url` must be given, and its default fails only on success.** The
default is `reverse_lazy("password_change_done")` -- *un-namespaced* -- and these patterns are
included under `namespace='accounts'`, so left alone it raises `NoReverseMatch` after the new
password has already been saved. The test follows the redirect through rather than asserting a
302, because a test that stops at the 302 passes straight through this.

**The validators reach the page for free, and the page has to say so.**
`AUTH_PASSWORD_VALIDATORS` is configured with all four of Django's and the length minimum raised
to **9**; `PasswordChangeForm.clean_new_password2` calls `validate_password`, so nothing here
implements any of it. But `validate_password` runs *only* from a Django auth form, which until
now meant the admin page ordinary users were bounced from -- so this is the first place an
ordinary user meets those rules at all. Because the fields are hand-written, as every form in
this repo is, `{{ form.new_password1.help_text }}` has to be rendered deliberately or the rules
reach people only as a rejection. It needs `|safe` (Django builds a `<ul>`) and a `<div>` rather
than the `<small>` the rest of the repo uses for `help-block`, since a `<ul>` may not sit inside
a `<small>`.

**Hand-written fields are checked against the form.** The house style is markup, not rendered
Django forms, and its one hole is that a renamed field or a deleted input yields a form that
silently fails validation forever. `test_accounts.HandWrittenFieldsTestCase` compares the
`name=` attributes the rendered page posts against `set(PasswordChangeForm(user).fields)` and
`set(AuthenticationForm().fields)`.

**The login page was the one template following no house convention.** It was written against
Bootstrap **4** class names -- `form-signin`, `form-label-group`, `align-content-lg-center` --
which exist in no stylesheet here and in no version of the vendored Bootstrap 3.3.7. Being
inert is exactly why it had no space between its two inputs: nothing supplied a margin, where
`form-group` supplies 15px. Its button was the only `btn-lg btn-block` in the codebase, its
`<div class="row ` never closed its quote (swallowing the spacer div after it), and it rendered
`{{ form.errors }}` nowhere, so a wrong password silently re-rendered a blank form. The
replacement is `ale/group_new.html`'s shape. A test names each dead class, because "it looks
like every other page" is a claim that rots quietly.

**The sidebar's account block is the shell's own, not a nav entry.** Username, Logout, Change
Password and -- for a superuser -- Django admin, indented under the username with `class="small"`
and three `&nbsp;`. It is written into `base.html` rather than registered, because
`nav_registry` has no per-user visibility concept and `base.html` already has `user`; adding a
`visible_to=` predicate for one entry would be a mechanism with a single producer.

**The admin link is gated on `is_superuser`, not `is_staff`, and the difference is not
pedantic.** Django's admin admits anyone with `is_staff`, so a staff gate would be the one that
matches who gets in -- but `load_projects` creates every imported user with `is_staff=True`, so
on a real deployment that is nearly everybody, most of whom would land in an admin with nothing
in it. There is a test for the staff case specifically.

**Change Password used to link `/admin/password_change/`**, which is wrapped in
`AdminSite.admin_view` -- so it bounced every non-staff user to the admin login with "Please
enter the correct username and password for a staff account". It was broken for exactly the
people most likely to click it, and for staff it worked by leaving the product.

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

`aledb_sample/views/breseq_table.py` renders one sample at `/mutations/breseq` in breseq's own
column order and colouring, as the per-sample companion to Compare, which pivots the whole
experiment and lives in the **aledb-compare** plugin at `/compare/` (see **Compare is a
plugin** below). A picker moves between samples; the evidence cell links into the genome
browser when the sample has a stored alignment.

The picker is the same menu as the genome browser's Samples control and the Metadata page's
column menu, but picking one sample rather than any number, so the button carries the chosen
name as its value and exactly one row is `active`. Its rows are **links, not a `<select>` in a
form**: one request per sample either way, and links need no script at all, which is what the
`<noscript>` submit button used to cover. The href is the same `?experiment_id&sample_id`
pair the form submitted.

The cell markup is not a lookalike -- it is generated by `aledb_import.annotate.display`,
the port of the code that wrote the report the sample was imported from. A row is
`{**mutation.gd_data, **mutation.annotation}` handed to `add_html_fields()`, which is why
annotation lives in one JSON column rather than twenty scalar ones: rendering is a dict
merge, not a rebuild. It is server-rendered rather than fed to DataTables as a JSON blob,
because the markup already exists by the time the view runs.

**Because that markup is generated in Python, its behaviour cannot live in a page.** Past
`MAX_GENES_BEFORE_SUMMARY` (15) genes a deletion's Description collapses behind a **Show**
button, and the click handler for it was an inline `<script>` in *this* page's template --
while three pages render rows from `build_rows`: this one, the genome browser and the mutation
editor's Edit/Delete listing. On the other two the button rendered, was styled by the shared
stylesheet, and did nothing. The handler is `aledb_common/staticfiles/js/breseq_table.js` now
and travels with `breseq_table.css`: **link the stylesheet, load the script**, with no
exception for a page that has no Description column today -- the Copy tab loads it and does not
need it, because deciding that per page is what went wrong.
`aledb_common/tests/test_templates.py` keeps the pair together, and the three pages each assert
the script in their *rendered* HTML, since a `<script>` outside a `{% block %}` is discarded
silently.

It is called **Mutations** in the nav and on the page. **Compare** used to sit beside it
here; it is registered by the aledb-compare plugin now, so on a deployment without that
plugin this page is the only mutation table core offers. The table markup
itself lives in `aledb_sample/templates/breseq_table/_mutation_table.html`, shared with the genome
browser, which renders the one mutation it is open at through the same `build_rows` — the two
must not drift, because the cell contents come from `annotate.display` and mean nothing without
these columns around them.

A mutation imported before a reference was available has no annotation to render and falls
back to the flat columns, with the page pointing at `./aledb reannotate`. That fallback is
the usual reason the page looks plain: nothing is wrong with the rendering, the rows simply
have no annotation yet. `Mutation.gd_data` is kept for every mutation, so `reannotate`
recomputes them in place against the stored reference -- re-importing is not needed.

### The plugin API documentation

`docs/`, built with `./aledb docs` (add `--serve` for live reload on :8001, `--strict` to fail
on a broken link). Output goes to `site/`, which is git-ignored. Nothing is hosted.

**The toolchain is MkDocs + Material + mkdocstrings, and the reason is the docstrings.** The
eight registries carry **465** non-blank lines of docstring containing **143** single-backtick
code spans, written as markdown. (This said 360 and 90, of seven registries; re-counted over
every docstring in `aledb_common/*_registry.py`, the seven were already 413 and 117 before the
eighth was added. Measured, not adjusted -- the same drift the test counts above warn about.)
Sphinx's `autodoc` parses docstrings as reStructuredText, where a single backtick is a *title
reference* -- all 143 would render as italics and warn. MyST changes
how `.md` pages parse, not how docstrings do. `mkdocstrings` parses them as markdown, so the
reference renders correctly with no edit to any docstring. (Secondarily: on this repo's Python
3.9, pip caps Sphinx at 7.4.x while `mkdocs-material` is current.)

**`requirements-docs.txt` is separate from `requirements.txt` on purpose** -- the entry script
installs the latter into every deployment, and production has no use for a site generator.
`./aledb docs` installs it on first run, the way `./aledb start` bootstraps the venv.

**`DEVELOPER.md` moved into the site** (`docs/assembling/`) and is now a stub pointing at it.
Two descriptions of how `config/settings.py` is wired would have drifted.

**Where a fact belongs**: how something behaves goes in the `aledb_common` docstring, because
the Reference pages are generated from those; how to do something goes in a guide under
`docs/plugin/`; why *this repo* is built as it is stays here, for a different reader.

`aledb_common/tests/test_docs.py` guards two kinds of drift, by reading files rather than
importing mkdocs or PyYAML -- neither is installed in a normal environment. It fails when a
registry has no reference page, when a page names the wrong module, when the nav omits one,
and when a public `register_*` is named nowhere in `docs/`. That last one caught five
undocumented hooks the first time it ran. **Neither guard catches prose going out of date**,
which is said out loud in `docs/contributing/docs.md`.

**The same command builds a deployment's whole manual.** Both entry scripts end at
`aledb_common.cli.manage()`, so `./mutint docs` reaches this command -- and rather than
building aledb-core's docs from inside the submodule, it builds MutInt's manual: MutInt's own
pages plus every installed component's. `aledb_common/docs_manual.py` collects them.

There is no eighth registry. A component contributes by having `docs/` and `mkdocs.yml`;
discovery is `about_registry.first_party_app_configs()` → `component_dir()`, so an uninstalled
submodule contributes nothing and the manual is an inventory of what is installed, the way the
About page is.

Four things in that module are load-bearing:

- **The project is excluded from its own component list.** Standalone, aledb-core *is* the
  project and its apps are installed; without the exclusion every page renders twice, once at
  the top level and once nested under a component heading.
- **`ALEDB_TOOLS_DIR` says which project is being built**, because the entry script exports it
  and is the one place that knows -- settings cannot, for the reason `templates/` and
  `staticfiles/` have to be re-pointed.
- **Component docs are symlinked**, not copied: mkdocs walks `docs_dir` with
  `followlinks=True`, so a build reads through to each repository and can never serve a stale
  copy.
- **`mkdocstrings.paths` must name every component**, or a plugin's `:::` reference does not
  resolve once its pages are built from somewhere else.

**The manual is organised by audience, not by component.** `Using ALEdb` and `Extending ALEdb`
are top-level nav headings a component uses in its *own* `mkdocs.yml` to say who each page is
for, and the collector merges each heading across components. Anything under an unrecognised
heading lands under `About this deployment` named for its component -- visible rather than
dropped, so a component that has not thought about audience still builds.

**Cross-component links are not supported and `--strict` catches them.** A page is at
`aledb-core/plugin/testing/` in a manual and `plugin/testing/` when built alone, so such a link
is broken in one of the two. One was written and caught this way.

Versioning is deliberately not configured. `mike` is the intended path and needs one block in
`mkdocs.yml`; adding it now would render a version picker with nothing in it.

**A caveat with a clock on it**: mkdocs-material warns that MkDocs 2.0 removes the plugin
system entirely with no migration path. `requirements-docs.txt` pins `mkdocs<2`, which holds
today and is not a plan.

### `present` says whether it is there; `source` says who said so

`MutationCall` used to carry `breseq_present` and `gatk_present` beside `present` -- one
flag per variant caller -- and **every read path asked the caller flags rather than `present`**:
`_get_table_mutation_entry` for a filled cell, `browse._samples_calling` for the genome
browser's `*`, and `aledb_interop_query`. That was harmless while breseq was the only thing
that ever wrote a mutation, and stopped being harmless the moment `aledb_mutation_editor` let a
person add one. A hand-added call is `present=True` with no caller flag, so it answered
no to all three: it was stored, it showed on the editor's own per-sample page, and it was
**absent from Compare, fixation, converge and search** -- which reads as the add having
silently failed rather than as a rendering rule being wrong.

Both columns are gone. The two questions they conflated are now asked of separate columns:

- **`present`** -- is this mutation in this sample. True is an assertion that it is, False
  that it was looked for and found absent (which renders the read-count cell), null that
  nothing was recorded.
- **`source`** -- who asserted it: `"breseq"` from the importer, `"manual"` from the editor.
  Null means imported before that column existed, which is its own thing and must not be
  read as "unknown caller".

`aledb_sample.0010` backfills `present=True` wherever a caller flag was set and `present` was
null, then drops the columns -- in that order, because a row whose presence was recorded only
in a flag would otherwise become a row about which nothing was ever recorded, and those render
nowhere. `aledb_sample/tests/test_caller_flag_migration.py` stands the database up at `0009`
through the real migration executor to check it, which is the only way to test a data migration
whose columns the live model no longer has.

**Old change-log snapshots needed no migration.** `history._call_kwargs` builds its
kwargs by walking `CALL_FIELDS` and calling `snapshot.get(field)`, so the two keys left
behind in stored `MutationEdit.call` blobs are simply never read again.
`aledb_mutation_editor.migrations.0002` writes those blobs and had its own copy of the field
list; it now skips a column the model does not have, because which side of the drop it runs on
depends on where it falls in a given database's graph.

### Editing a sample's mutations, and the history that makes it safe

`aledb_mutation_editor` owns four operations -- **edit** a mutation, **delete** a call
from a sample, **add** one nothing carries yet and **batch-copy** one from a sibling sample --
and an append-only edit log that makes all of them reversible. The toolbar is
`/mutation-editor/` (Edit), `/mutation-editor/delete`, `/mutation-editor/add`,
`/mutation-editor/copy` and `/mutation-editor/history`, and `/mutation-editor/edit` is the one
mutation's form the Edit tab links to.

**Edit and Delete are two tabs over one listing**, and that is a split rather than a
duplication. The page used to carry a checkbox column *and* a per-row link, so the next thing
you clicked might have meant either -- and one of the two is destructive. `_listing(request,
mode)` builds the rows once and the mode decides one column and one button; two views over two
templates would be two places for the "listings here are unfiltered" rule to drift apart.

**Both tabs put their control in the first column** -- a checkbox, or the `edit` link. Last
would have been tidier to write and unusable to read: the gene column alone can run to
thousands of characters, so this table is wider than the window on a real experiment and a
control at the far right is one nobody reaches without scrolling sideways. Both modes adding
exactly one leading column is also what lets the JavaScript hold one set of column indices
rather than two.

Every write is `<what>/apply` -- `delete/apply`, `add/apply`, `copy/apply`, `edit/apply`.
Deleting used to be a bare `^delete$`, the odd one out, and the Delete *tab* needed that name.

**What it deletes is an `MutationCall`, never a `Mutation`.** That distinction is the whole
design. Mutation primary keys are stored as bare integers, with no foreign key and nothing that
prunes them, in aledb-phylogeny's `branch_mutations` JSON -- whose
docstring says *"ids do not move"*, and which is not on the
rebuild hook -- and in every exported CSV's "Mut ID" column. Deleting a Mutation and letting a
re-import recreate it through `gd_import`'s seven-field `get_or_create` would mint a new pk for
the same biological mutation and quietly invalidate all of it. Removing only the sample's
call changes nothing any stored id means.

**The rows are hard-deleted, and that is what kept every read path untouched.** An
MutationCall is read by `mutation_table_builder`, `breseq_table`, `aledb_export`,
`aledb_stats`, `aledb_dashboard`, `aledb_search`, aledb-fixation and aledb-converge, and this
repo's default managers are deliberately unfiltered. A soft-delete flag would have needed all
eight taught to filter, and the one that was missed would have gone on showing deleted
mutations in an export or a fixation table. Nothing was added to any query.

The log is two tables in `models.py`. A `MutationEditSet` is one user action against one
experiment; a `MutationEdit` is one call it added or removed, carrying a **full
snapshot** of the row (`call`) and of its mutation's identity (`mutation_identity`).

- The call snapshot is every column, so a restore is exact rather than approximate.
  `frequency` is a `DecimalField`, so it is stored as a string -- a round trip through `float`
  moves it at the fourth decimal place, which is where that column keeps its precision.
- **`mutation_identity` exists because the Mutation row may not outlive the log.**
  `aledb_import.ale_experiment._delete_all_orphaned_mutations` hard-deletes any Mutation with
  no MutationCall, and runs after an experiment delete and after `delete_sample` -- so
  removing a mutation's last call makes it eligible for a sweep triggered by something
  else entirely. The snapshot is the exact `get_or_create` key plus `gd_data` and `annotation`,
  which is enough to put it back indistinguishable from an imported row. `aledb_import` needed
  no edit for this, and `test_restore.SweptMutationTestCase` is what pins it.

**Restoring is a new edit set, not a rewind.** `history.state_after` derives the state at a
version by taking the live rows and undoing every edit set newer than it, newest first;
`plan_restore` diffs that against the present and `restore` applies the difference with
`kind=RESTORE`. So the log is never rewritten, a restore can itself be restored past, and
restoring twice to the same point is a no-op the second time rather than a second identical
entry. Identity throughout is `(sample_id, mutation_key, source)` and **not** a primary key: a
restored row is a new row, and its Mutation may have been recreated. "Newer than" is decided on
`pk`, not `created_at`, because two edit sets written in the same microsecond need a total
order; the timestamp is what a person picks a version by.

**The editor's listings are unfiltered.** `breseq_table` runs its rows through
`filter_mutation_calls`; these pages do not. Filtering is a display concern, and a mutation
excluded by a gene or frequency filter has to stay visible here or it cannot be removed and
returns the moment somebody widens the filter.

Rebuilds run through `history.rebuild_after_edit`, outside the transaction as
`gd_import.run_post_processing` does, and deliberately **without `only=`** -- unlike
`samples.rebuild_after_structural_change`, which refuses to pay for the dashboard's totals
because a renumber cannot change a mutation count. Adding or removing a call changes
every registered rebuild.

That used to have a sharper second reason -- aledb-fixation cached MutationCall *ids*, in a
column only its delete-and-recompute rebuild cleared, and those are exactly the rows an edit
hard-deletes. Fixation stores nothing now, so no registered rebuild holds a call id.

**Two traps in the templates.** The selection tables are DataTables with the Select extension,
which is safe here only because no cell is an input -- selection lives in DataTables' data
model, so a row selected on another page or behind a search box still comes back from
`rows({selected:true})`. `ale/experiment_samples.html` avoids DataTables for the opposite
reason: `deferRender` never builds the DOM for undrawn rows, so a *typed* value on page two
would not exist to read back. If a cell here ever becomes editable, the table has to become a
plain one. And the initialisation must sit inside **`$(document).ready`**: base.html loads the
Select and Buttons extensions in the last `<script>` of `<body>`, after `{% block content %}`,
so an IIFE in the content block runs with only plain DataTables loaded and `select:` and
`buttons:` are silently dropped -- the table still draws and the checkbox column still gets its
class from the stylesheet, so it reads as a styling fault rather than a load-order one.

### Derived data is only as fresh as its reader makes it

`rebuild_registry` splits marking from running on purpose -- `request_rebuild` is one UPDATE
and safe from any request, `run_rebuilds` is expensive -- and the design says the page that
reads the data closes the gap by calling **`ensure_fresh`**. For a long time exactly one reader
did. Six rebuilders were registered; `aledb_stats.get_experiment_summary` was the only one that
refreshed itself.

So a filter edit, which deliberately marks and rebuilds nothing, left the needle plot, Fixed
Mutations, Convergence and the dashboard showing what was true under the *previous* cutoff, with
nothing short of `./aledb rebuild` that would ever correct them. The global filter view's own
comment -- *"Each page rebuilds its own on next view"* -- described something that had never
been implemented.

The sharpest case was a single page: `/stats` renders `get_experiment_summary`, which
refreshed, beside the needle plot's data, which did not -- two counts of the same mutations
disagreeing in the same viewport. Every reader calls `ensure_fresh` now.

**That page then went further and stopped storing either of them**, which is the better answer
where it is available: an `ensure_fresh` closes the gap between two caches, and having no cache
means there is no gap to close. `/stats` is two queries over the same rows now, so the two
halves cannot disagree about how fresh they are. The remaining readers that do call
`ensure_fresh` -- `aledb_dashboard` and `aledb-fixation` -- are the ones whose answer is
genuinely expensive to produce.

**A plugin must not spell its own rebuilder's name.** `register_post_experiment_hook` derives
it from the app label, suffixes a second registration, and **returns** what it used;
aledb-fixation captures that in `AppConfig.ready()` as `util.REBUILD_NAME`. A literal
`'aledb_fixation'` would be a second opinion about a name `_candidate_names` owns.

**`ensure_fresh` cannot raise**, which is what makes it safe on a read path: a rebuild that
fails is logged, recorded in `last_error` and left stale, and the page renders whatever was
stored before. A broken plugin rebuild degrades its own page instead of 500ing it.

#### What the edit path pays for, and what it does not

`rebuild_after_edit` is **unnarrowed by name and narrowed by scope**, and those are different
questions. Never `only=`, unlike `rebuild_after_structural_change`: adding or removing an
call changes every derived thing an experiment has. (It used to also be because
aledb-fixation cached MutationCall ids that only its own rebuild cleared; it stores nothing
now, and the first reason stands alone.) But `request_rebuild`
marks the **site-scoped** totals stale too, correctly, and running them here made a single
delete recount every MutationCall in the installation -- measured at 4.9s for the read half
alone on 74,859 rows, which is exactly the bill `rebuild_after_structural_change` refuses. They
stay marked; the dashboard's own `ensure_fresh` pays it once on the next view. Ten deletes cost
one recount rather than ten.

**That 4.9s was measured on an implementation that no longer exists** -- `rebuild_mutation_counts`
materialised every call as a model in order to filter it, and reads three columns as
tuples now. The behaviour above is unchanged and not up for revisiting on that account: it rests
on ten deletes costing one recount rather than ten, which is true at any per-row price.

`run_rebuilds` takes `scope=` for this; `get_rebuilders` already did.

#### Being told without being rebuilt

`register_rebuilder(..., auto=False)` is derived data that is **tracked and marked stale but
never rebuilt on its own**. Until it existed the two were welded together -- you could only be
told your data had gone stale by promising to recompute it -- and `aledb-phylogeny` refused
that bargain and so registered nothing at all. Its stored tree was never invalidated by
anything, and `/phylogeny` drew a topology inferred from mutations that had since been deleted.

`force=True` does not override it. `run_post_experiment_hooks` forces on every import, so an
import would otherwise build every opted-out thing there is. Being named in `only=` is what
runs one.

**Nothing registers `auto=False` today.** The plugin it was added for stopped needing it: what
`aledb-phylogeny` registers now is a *deletion* of its cached trees, and a DELETE is cheap
enough to run wherever any other rebuild does. The flag and its tests stay, because the next
expensive stored answer will want them, but there is no live example to read.

The skip lives in `run_rebuilds`, **not** in `get_rebuilders`: `request_rebuild` and
`./aledb rebuild --list` both go through the latter and must still see manual rebuilders --
one to mark them, the other to show them, tagged `(manual -- --only runs it)` so a stale marker
beside one does not read as a failure.

**Opting out is a new way to be wrong.** A page that registers `auto=False` and then forgets to
ask `is_stale` is worse off than one that never registered: it has a staleness record nobody
reads.

#### A rebuild used to declare what it reads

`inputs=` took `INPUT_MUTATIONS`, `INPUT_FILTERS` or both, and `request_rebuild(changed=...)`
said which had moved, so a filter save marked only what read through the filter. It existed for
`aledb_phylogeny`, which reads `MutationCall` directly: marked by a cutoff edit, its stored
tree would have been thrown away and redrawn to the identical topology -- the false alarm that
teaches people to ignore the real one.

**All of it is gone**, because its only producer was the page that edited an experiment's shared
filter row. Filtering belongs to the reader now and lives in their session, so there is no
shared filter for anything to be invalidated by, and `INPUT_FILTERS` had nobody left to pass it.
With one value remaining the mechanism could only ever have said "everything", which is the
default -- so it went by its own rule: *a word nothing passes as `changed=` is a word with no
meaning behind it.*

#### Freshness belongs where the data is written

`aledb_phylogeny.rebuild_phylogeny` settles its own staleness row rather than leaving it to the
view. **A missing `DerivedDataState` row already counts as stale**, so anything built by a route
other than the registry -- the page's own button, `load_example`, `./aledb rebuild_phylogeny` --
is born stale and is thrown away by the very next read unless the write says otherwise. That is
how it was found the first time: four of the example suite's branch tests started answering 409.

It is now the *first* thing `rebuild_phylogeny` does rather than the last, and it discards
rather than marking fresh -- `ensure_current`, which is `ensure_fresh` under a local name. The
ordering is what matters and the reason is worth keeping: making the cache current *before*
writing to it means a row written while a discard was still pending cannot end up sitting
beside rows inferred from the mutations as they were, with the mark cleared afterwards to make
the whole lot look current. Clear-at-the-end is the version of this that looks equivalent and
is not.

#### Derived data cannot notice that the *rules* changed

Every write path marks what it invalidated. Nothing marks anything when the code that decides
what counts changes instead -- no experiment moved, so no `request_rebuild` fires, and the
tables sit there holding pre-change values while `stale_since` says they are fresh. Measured
right after the frequency cutoff started filtering: the dashboard stored **74,859** calls
where the filter yields **73,857**, marked fresh, so `ensure_fresh` would have left it
indefinitely.

`aledb_common.0002` stamps every `DerivedDataState` row stale for that reason -- one UPDATE, no
rebuilding, and each page recomputes on its next view. Measured end to end on the dev database
after migrating: the first `/dashboard` view took **5.4s** and corrected the stored total from
74,859 to 73,857; the second took **0.00s**. **Any future change to filtering or
counting logic needs the same migration**, because there is no way for the data to work it out
for itself. `./aledb rebuild --all --force` is the manual equivalent.

**`aledb_common.0003` is that rule being followed**, and it moves the same total back: the
dashboard stopped applying the filter, so 73,857 becomes 74,859 again. It marks only
`mutation_counts`, because only that changed -- `0002` marked everything because the filter
itself had changed and every derived table read through it.

**`0004` is the third application**, when the functional-change buckets moved onto
`Mutation.snp_type`. It marks `mutation_counts` for the same reason and **depends on
`aledb_dashboard.0003`**, which adds the `nonsense` column the ensuing rebuild writes. That
ordering is not tidiness: `ensure_fresh` cannot raise, so a rebuild scheduled before the column
existed would log a `FieldError` into `last_error` and leave the dashboard stale indefinitely --
the quiet failure the registry's isolation deliberately trades for.

#### The dashboard applies no filter

It is an inventory of what the installation **holds**. An experiment's frequency cutoff or
ignored-gene list is one person's view of one experiment, and a site-wide total computed
through every experiment's filters answers a question nobody asked. Under per-user filtering it
stops being computable at all: a shared table cannot be keyed by user.

So `mutation_counts` declares `inputs={INPUT_MUTATIONS}` and a filter save no longer marks it.
That matters more than it sounds: a filter edit marks *every* experiment at once, and this is
the most expensive rebuild registered, so being marked by one was both wrong and the costliest
way to be wrong.

Two things fell out of removing the filter, and both are worth knowing:

- **Nothing has to be materialised any more.** Filtering needed the gene column parsed per row,
  so every row had to be built; counting needs three columns, read as `values_list` tuples.
- **`synonymous` and `nonsynonymous` had never been written.** The branch filling them tested
  for `snp_type_synonymous`/`snp_type_nonsynonymous`, which are not tokens in
  `FUNCTIONAL_CHANGE_TYPE_LIST`, so both columns sat at zero from the day they were added. They
  are filled now, which is the second reason `0003` exists.

They still read zero after that, though, because `protein_change` was the wrong column
altogether -- which is the change described next.

### A gene list has two separators, and a ceiling

`Mutation.gene` is written by `aledb_import.gene_annotation.get_annotated_gene_list` and read
by `aledb_common.util.get_gene_list`, and for a long time the two disagreed about what
separates a name.

- A mutation spanning a **gene range** stores every name breseq listed, and those come through
  `annotate.annotator`, which joins with a bare comma (`GENE_LIST_SEPARATOR = ','`).
- Every other shape -- intergenic, single gene -- is joined by the import path with `", "`.

`get_gene_list` split on `", "` only, so a 4,318-gene inversion read back as **one gene name
23,003 characters long**. Nothing about that was visible as an error: the Gene column's
expander is gated on `len(...) > 10`, so it never appeared on any real mutation; ecocyc
rendering made one broken link out of the whole string; and a reader's ignored-gene list could
not name a single gene inside it. Measured on the dev database: 0 cells with an expander before,
218 after. A reader typing `thrA,thrB` without the space had the same problem -- `_gene_tuple`
parses with the same function, so the filter matched nothing.

`GENE_LIST_SPLIT` (`,\s*`) accepts both. **The writer is deliberately not corrected**, and
that is the load-bearing part: `gene` is one of the seven fields
`Mutation.objects.get_or_create` keys on, so re-joining a range's names with `", "` would
change the stored string for every range mutation and fork all of them on the next import. The
column holds two spellings and the reader takes both.

**Past `GENE_LIST_LIMIT` (1,000) genes the names are neither recorded nor rendered.** A
structural variant can span most of a chromosome, and 23,003 characters had already overflowed
`gene`'s own `CharField(max_length=19000)` -- silently, because SQLite does not enforce it and
Postgres would have refused the row. Over the limit the importer records the **range**
(`mokC–[fimA]`, breseq's own Gene column for such a mutation) and both renderers show a count
rather than a list, with no expander to open. The limit lives in `aledb_common/util.py` because
the importer and the renderers have to agree: a row written under one limit and read under
another would show a truncation nothing performed.

`aledb_sample.0012` moves the rows written before the cap -- 7 of 41,671 in the dev database. It
is not tidying: a row left holding the long string no longer matches what the importer computes,
so re-importing that sample would mint a second `Mutation` and split its calls across
both. What it writes is `annotation['gene_name']`, which is exactly what
`get_annotated_gene_list` now returns, and `test_gene_cap_migration` asserts that equality
rather than asserting the string merely got shorter.

### Functional change is counted from `snp_type`

`Mutation.snp_type` is breseq's own functional class, written by the annotator, promoted to an
indexed column, and for a long time **read by nothing**. Both pages classified functional change
by substring-matching `protein_change` instead -- a *display* string, `I34S (ATC→AGC)`,
containing neither "synonymous" nor "nonsynonymous". On the dev database 19,982 of 24,088
mutations bucketed as `unannotated`, including every one of the 12,793 nonsynonymous and 4,878
synonymous SNPs. The comment on `FUNCTIONAL_CHANGE_TYPE_LIST` said as much all along: *"these
names match with Breseq's HTML annotations"* -- they are `snp_type`'s vocabulary.

`aledb_sample/functional_change.py` owns the vocabulary and the rule; `aledb_sample/views/common.py`
re-exports both so no importer changed. Four things about it are load-bearing:

- **`nonsense` was missing from the vocabulary**, though `annotator.py:470` has always written
  it. 392 dev-DB SNPs carry it. Both dashboard count models gained a column.
- **A compound resolves to its most severe token.** `annotator.py:508` joins one value per
  overlapping gene, so `nonsynonymous|synonymous` is one base in two reading frames. The order
  of `FUNCTIONAL_CHANGE_TYPE_LIST` *is* the hierarchy — `nonsense > nonsynonymous > synonymous >
  noncoding > pseudogene > intergenic > unannotated` — and there is deliberately no second
  ordered tuple to keep in step. Splitting on `|` and comparing whole tokens is also what
  removed the substring hazard that used to force `nonsynonymous` to precede `synonymous`.
- **The breakdown is SNP-only.** breseq assigns `snp_type` for SNP and RA entries only, so every
  DEL, INS, MOB, AMP, SUB and INV is `unannotated` — 2,720 dev-DB mutations, of which 822 used
  to borrow an `intergenic` bucket from `protein_change`. `annotation['gene_position']` could
  recover them, but it answers a different question (which feature it sits in, not what it did
  to a protein), and 1,631 non-SNPs are `coding`, a word with no bucket on this axis.
- **`_count_in_sql` groups rather than matching.** `values('mutation__snp_type').annotate(...)`,
  then resolve each distinct value in Python. Summing per-group distinct counts is *exact*
  because the group key is a column of `Mutation` reached by a forward FK, so every call
  of a mutation lands in one group. Group by anything reached through a reverse or m2m relation
  and the sums silently exceed the true distinct count.

**Both pages now render these counts**, which they never did: the four context keys
`aledb_stats/views.py` pushes had no reader in `stats.html`, and the dashboard's six columns had
none in `dashboard.html`. That is how two of them sat at zero unnoticed. The two apps also agree
now — the Overview used to count a mutation under *every* token it matched, so its
functional-change sums exceeded its mutation count; with one bucket per mutation they equal it,
while the *type* sums remain lower because an unknown type is dropped rather than bucketed.

Gone with it: `SEQ_COLORS`, `GENE_COLORS`, `COLORS`, `DEFAULT_COLOR`, `_set_colors` and the
`seq_color_set`/`protein_types` context keys — palettes for a chart never built, whose one live
effect was that adding a token silently reshuffled a colour list nobody rendered.

#### The dashboard counted what had been deleted

`rebuild_mutation_counts` read `MutationCall.objects.all()` and `rebuild_sample_counts`
counted every `Population`/`TimePoint`/`Isolate`, neither excluding soft-deleted rows -- and nothing
marked the totals stale when a project or experiment was removed, so even a rebuild would have
produced the same numbers. Both halves are fixed. Both conditions are needed: **deleting a
project does not stamp its experiments**, so a check on `Experiment.deleted_at` alone would
go on counting everything underneath it. `get_general_count_dict` counted deleted projects and
experiments outright, behind a comment saying no filtering was needed.

The two delete views mark only the aggregates by name: removing one experiment cannot make
another's needle plot wrong, and `request_rebuild()` with no experiment would mark every one.

### The frequency cutoff, which excluded nothing

`aledb_filter`'s min/max cutoff is the oldest user-facing filter here and it did not work, in
any configuration reachable through the form. Two independent faults in one block of
`filtered_mutation_call_queryset`, neither with a test. That queryset builds a `Q` and
hands it to `.exclude()`, so every term describes something to **hide**.

- **A `frequency_gatk__lt` term was ANDed in whenever `min_gatk_cutoff` was set** -- which was
  always: it defaulted to 20 and no form, view or template ever exposed it. No import path has
  ever written `frequency_gatk` (0 of 74,859 rows in the dev database), and a comparison
  against null is never true, so the AND could not be satisfied and nothing was excluded.
- **The floor and the ceiling were ANDed together**, which reads "below the floor *and* above
  the ceiling at the same time". No row can be both, so setting a maximum silently switched
  the minimum off as well.

Two smaller things fell out with them: one branch was a straight duplicate of the term above
it, and both gatk branches compared against `min_cutoff`/`max_cutoff` rather than their own
settings -- so those settings were never values, only switches.

`frequency_gatk`, `min_gatk_cutoff` and `max_gatk_cutoff` are gone (`aledb_sample.0011`,
`aledb_filter.0004`), and the two remaining terms are **OR**ed. The block is now two lines and
does what the page has always said it does.

**This changes what every read-only table shows**, which is the point and is still worth
saying out loud. In the dev database it hides 1,002 of 74,859 calls, and **all 1,002 are
in one experiment** -- `Population tree`, where 1.9% of 53,141 calls sit below the default 20%
floor. Every other experiment loses nothing, because a clonal isolate rarely carries a call
that low. So the filter finally working is most visible exactly where it was designed to
matter, and somebody will notice that one experiment got shorter.

They are still *stored*, and still listed in `aledb_mutation_editor`, whose listings are
deliberately unfiltered -- which matters more now than it did, because a mutation hidden from
every table has to stay somewhere it can be removed. There is a test for that.

`aledb_interop_query` carries a hand-rebuilt copy of the same block and had every one of the
same faults; it was fixed in the same shape.

### The whole experiment at once: `?sample_id=all`

The Edit page has two modes in one sample picker -- a sample, or **All samples**, which lays
the experiment out as a grid with mutations down and samples across and lets a selection span
any number of both. It exists because `mutation_delete` has always taken a list of
calls scoped to the experiment rather than to a sample, so removing the same bad call
from twelve samples was already one edit set' worth of work and twelve page loads' worth of
clicking. Per-sample stays the default: the grid is the more useful view of a large experiment
and also much the more expensive one.

**It renders at most `GRID_ROW_LIMIT` (250) mutations, and narrows server-side.** That is not
a display preference. The largest experiment in the dev database is 5,076 mutations across 51
samples, and laying all of it out produced a **32.8 MB** page -- built by the server in 1.6s,
so the whole cost is what the browser is then handed. Capped it is 1.75 MB, and a real search
(`q=thrA`) is 0.10 MB. The search box is a `GET` that re-renders rather than DataTables' own,
because a client-side filter still ships every row; the box decides what gets built. A number
matches `position` exactly, since a substring match on a coordinate is never what anybody
means.

**Only mutations something observes are listed.** A `Mutation` is never deleted here, so
removing its last call leaves the row behind -- and a grid keyed on
`Mutation.objects.filter(experiment=...)` went on rendering it with every cell empty, which
is what "the page does not update when I delete" turned out to be. The page reloads; the row was
genuinely still there. Restricted to the *shown* samples rather than to the experiment, because
`get_reseq_ordered_dict` applies the sample tag filters and a mutation observed only in a hidden
sample is an all-empty row for the same reason. This is not the filtering the editor forbids: a
mutation no sample observes is stored in no sample, so there is nothing on its row to select and
nothing on it to delete.

**Selection lives in a `Set` of call ids and never in the DOM.** DataTables detaches
the rows of undrawn pages, so `.selected` on `<td>`s can only ever see the current page. The
page carries two maps through `json_script` -- `by_mutation` and `by_sample` -- and the row
and column selectors read those, so they reach rows that do not currently exist. Measured in
headless Chrome: one click on a sample header selected **66 calls, of which only 32
were in the DOM**, split 32/23/11 across three pages, and the count survived paging back and
forth. Walking the table would have found 32 and silently reported success.

**The maps cover only the rendered rows, deliberately.** They could just as easily cover the
whole experiment -- and then a column selector would put calls into the selection that
the person cannot see and does not know about, on a page whose next button deletes them.

Two smaller things the browser found:

- **A column header with nothing on the page is not a link.** An experiment's mutations
  concentrate in a subset of its samples, so several headers select nothing; one that looks
  live and silently does nothing reads as broken. Each header carries its own count and says
  it in the tooltip.
- **The per-sample script guards on `#me-table`, not on `#me-apply`.** Both modes render a
  Delete button with that id, so the old guard bound the per-sample handler on top of the
  grid's and every delete went through whichever won.

The grid is a **second** mutations-by-samples table beside Compare's, and that is allowed: this
one is unfiltered, selectable, capped and shows uncalled calls, and Compare is none of
those. `mutation_table_builder` could not have been reused anyway -- its cells are `<a>`
elements into the genome browser, which would fight a click meaning "select", and
`get_table_body` filters through `filter_mutation_calls` while this page must show what is
stored.

### Editing a mutation, in every sample or in some of them

`/mutation-editor/edit?mutation_id=<pk>` opens the Add form prefilled from the mutation's
`gd_data`, beside a list of the samples carrying it. It is reached from a row of the Edit tab
rather than from the toolbar, because it needs a mutation to be about.

**Which of them start highlighted depends on where the link came from**, and the two callers
differ because the question they were asked differs. A sample's own mutation table adds
`&sample_id=`, and the page opens on **that sample alone** -- correcting a call you are looking
at, in the sample you are looking at it in, is what following that link means, and defaulting to
every sample sharing the mutation would quietly change eleven of them. The grid's link
(`_grid.html`) carries no sample, because its row spans all of them and there is no one sample
that was clicked; there the whole set is still the default. `?sample_id=all`, the editor's
whole-experiment sentinel, lands on the default too rather than on an empty selection, because
`int("all")` is not a sample -- and an empty selection would open the page on a Save button that
refuses. **The scope is a
chosen set of those samples**, which it did not use to be: the page refused a subset outright
and said so, on the grounds that changing a mutation for some samples splits one mutation into
two. It does split one into two. That is a thing worth being able to do -- a call right in eight
samples and wrong in three should not mean deleting it from three and retyping it by hand -- and
`apply_edits` could already do it.

**Which of two paths runs is decided by two independent questions**: does the whole set move,
and do the new values already name a mutation this experiment has?

| samples | values already held by another row? | what happens |
|---|---|---|
| all of them | no | `apply_mutation_edit` moves the row itself; **its primary key never changes** |
| all of them | yes | the calls move onto that row; the emptied one is left in place |
| some | no | the chosen calls move onto a newly minted row |
| some | yes | the chosen calls move onto the row already holding those values |

Only the first row keeps the primary key, and it is kept there because it can be: mutation ids
are stored as bare integers, with no foreign key, in aledb-phylogeny's `branch_mutations` and in
every exported CSV, and nothing refreshes them.

**The last three paths are one call, and it is the one that was already there.**
`history.mutation_for_identity` `get_or_create`s on the six `MUTATION_KEY_FIELDS` -- which *is*
"the existing row if these values name one, a new row otherwise", asked once rather than
branched on -- and `apply_edits(removals=..., additions=...)` moves the calls. It was
extracted from `_resolve_mutation`, which needed the same thing to put a swept mutation back, so
there is still one definition of how a `Mutation` is minted from an identity. Nothing else in
`history.py` changed: `KIND_EDIT` labels the edit set and its rows are `OP_ADD`/`OP_REMOVE`
either way, so `state_after`, `plan_restore` and `restore` needed nothing.

**A restore across a subset change reuses the original row**, which is the opposite of what a
restore across a whole-set change does and is not arranged -- it falls out. The row never moved,
so `_resolve_mutation` finds it still holding the old identity and hands the call straight
back to the same primary key. On the whole-set path the row *has* moved, so `get_or_create` from
the logged identity finds nothing and mints one; see below.

**A merge is allowed and is not announced as one.** `_refuse_collision` is gone. What the result
names instead is the *samples*: a chosen sample that already carries the target gets the removal
and no addition, so it observes the mutation once rather than twice and keeps the frequency and
read counts it already had. That is the same choice `_plan_add` and `_plan_copy` make, and it is
the one part of the outcome a person cannot read off the page afterwards. Add and Copy report
their skipped samples by name now too, for the same reason -- a count of skipped calls is
not a thing anybody can act on.

**An emptied row is left in place, not deleted.** That is the posture delete takes with a
`Mutation` as well, and it is what lets the restore above resolve back to the same pk.
`aledb_import.ale_experiment._delete_all_orphaned_mutations` sweeps it if something else triggers
a sweep.

**The mutation the unchosen samples were left on is not re-annotated**, and that is a live trap
rather than a call: the old code called `record_builder.apply_annotation(mutation, ...)`
unconditionally after the edit, which is right only when the row itself moved. On the move path
the annotation belongs to the row the calls landed on, and only when that row was minted
by this request -- one that was already there keeps what it has.

**The calls are logged as removed and re-added, and that is not bookkeeping.** The
edit log is keyed on `(sample_id, mutation_key, source)` and `mutation_key` is derived from
the six `MUTATION_KEY_FIELDS`. Move a Mutation without saying so and `live_state` starts
computing a different key than every earlier entry recorded, with no edit set for
`state_after` to undo -- so "restore to before the edit" would silently leave the edit in
place. `KIND_EDIT` labels the *edit set*; its rows stay `OP_ADD` and `OP_REMOVE`, which is why
`state_after`, `plan_restore` and `restore` needed no change at all -- and why the subset paths,
which are `apply_edits` with removals and additions, needed nothing either.

**What the whole-set path does not re-create is the Mutation row.** `_resolve_mutation` returns
the row it is handed, so the additions point back at the one the removals came off and the
primary key never moves. Mutation ids are stored as bare integers, with no foreign key, in aledb-phylogeny's
`branch_mutations` and in every exported CSV -- and **nothing refreshes them**. What
aledb-phylogeny does on a mutation edit is throw its cached trees away, which corrects the ids
it held by no longer holding them; an exported CSV cannot be reached to correct at all. Minting
a new row would leave all of that pointing at a mutation with no calls; reusing it
leaves them resolving, to the corrected call.

**The order inside `apply_mutation_edit` is the whole of that path.** The removal snapshots are
taken *before* the row moves. Taken after, both sides of the edit set would record the new identity
and `state_after` would read the edit as having changed nothing.

Two refusals, before anything is written:

- **A change that changes nothing.** Checked on the six key fields *and* `gd_data`, because the
  record can move without the identity -- a MOB's `strand`, say -- and that is a real change to
  what `to_gd_line()` writes.
- **A sample that does not carry the mutation.** `target_sample_ids` is scoped to the samples
  observing it, for the reason the mutation is scoped to the experiment: a hand-typed id must
  not reach past what the page offered, and there is nothing to change in a sample that does not
  have it. An **absent or empty** list means every carrying sample, which is what the page posted
  before it could pick a subset -- so the endpoint's older contract still holds and most of
  `test_change.py` still exercises it unchanged.

Two rows sharing the six-field `get_or_create` key is still a state `gd_import` cannot produce,
and this still cannot reach it: `mutation_for_identity` joins the existing row rather than
minting a second.

**`_resolve_mutation` now checks that the row still *is* the identity**, not merely that its pk
still exists. That is a consequence of reusing the row, and the bug it fixes was silent: a
restore to before an edit arrives holding the old identity and a row that has since become
something else, and the old code handed the calls back to the *edited* mutation and
reported success having undone nothing.

So **a restore to before a whole-set edit mints a new Mutation row** -- `get_or_create` from the
logged identity finds nothing matching and creates it, leaving the edited row with no
calls, as a swept mutation would be. It is the one place an edit does not preserve the
pk, and making it do so would mean `state_after` replaying mutation-level state, which nothing
else needs. A restore across a *subset* edit does not have this problem, for the reason above:
the row it is restoring to never moved, so the same check that rejects it here accepts it there.

**The two forms share their fields and their machinery.** `_mutation_fields.html` is every
input any type can ask for, and `_mutation_form.js` -- a template inside `<script>`, the
`table_template.js` idiom -- is the type switching, the values that survive a type change, and
the per-field error display. `select_list.html` is now shared too: both pages pick a set of
samples, and Add's "Add to" and Change's "Change in" are the same control over different lists.
Add and Change differ only in what they post. A field added to
`genomediff.schema.TYPE_SPECIFIC_FIELDS` should need one edit, not two.

### breseq's own field guards, and where we are stricter

The `genomediff` pin is a **git SHA, and has to be**: every commit in that repo reports itself
as `0.4.2`, so a version pin cannot tell one from another and pip will not upgrade an installed
`0.4.2` on the strength of the requirement alone. Bumping an existing environment needs the
`--force-reinstall` line spelled out in `requirements.txt`.

At `43a0f72` the package grew `genomediff.schema` — breseq's `genome_diff_entry.cpp` tables,
mirrored and checked against `gdtools VALIDATE` over breseq's own 306-file test suite — behind
`Record.get()` / `set()` / `validate()`. `aledb_mutation_editor.validation` defers to
`check_field` for those rules rather than keeping a second opinion about what breseq accepts.

**`check_field` is well-formedness only.** It guards about twenty field names with five rules
(base sequence, positive / non-negative / any integer, strand) and knows nothing semantic — a
SNP to the base already there, an AMP to one copy and an inversion of a palindrome all pass it,
and breseq's own reference-aware check is equally generic. So the whole semantic validator
stays; what was deleted is the hand-rolled range and character checking that duplicated the
table.

**Two rules are deliberately stricter than breseq's**, and a tidy-up that "simplifies" them
away would silently widen what the form accepts:

- **`size`** is a `NonNegativeInteger` to breseq, so a size of **zero passes its guard**. A DEL
  covering no bases deletes nothing, and this form is where somebody types it by hand.
- **`new_seq`** as a base sequence accepts the **empty string** — every character of nothing is
  a base. It also has no notion of a SNP taking exactly one.

`PackageGuardTestCase` asserts both, by checking that `check_field` accepts the value and that
we refuse it anyway.

**Wording.** `check_field` returns breseq's phrasing — *"Expected positive integral value for
field [position] instead of [0]."* — which is right in a `.gd` and wrong beside a form input.
`GUARD_MESSAGES` maps the four fields where we have a better sentence; anything else shows the
package's own, which beats inventing wording for a rule we do not own. A key in that map for a
field breseq does not guard would never be reached, so there is a test that every key names a
real guard.

**`Record.get` is not `dict.get`** — with no default it raises `KeyError`. Every call site
passes one.

### Reading a `.gd`: one bad line no longer costs the file

At `43a0f72` the parser stopped raising on a line it cannot fully read. Problems collect in
`document.parse_errors` and the rest of the file loads. `gd_import.parse_warnings` carries them
into the per-file import summary as `warnings`, beside the existing `error`, and the Add Data
page lists them under the file — so a truncated line is named rather than silently worth fewer
mutations.

**`strict=True` restores the old raise, and is deliberately not used.** It raises on *any*
problem including the field-guard violations breseq's own output contains, so it would start
refusing files that import cleanly today.

**Leniency is not enough on its own**, which is the part that is easy to miss: a truncated line
parses with its missing fields set to `None`, and `Mutation.position` is NOT NULL — so passing
such a record on turns a reported bad line into an `IntegrityError` that rolls back the whole
sample. `gd_import._is_storable` skips them, and the reason is already in `parse_warnings`.

Two smaller changes from the same bump: an entry whose id column is `.` now parses with
`id=None` instead of failing the file (18 of breseq's 306 test files could not be read at all
before), and `Record.__str__` writes `.` back for an absent value where it used to write the
literal string `None` — a file breseq cannot read. Both are fixes we get for free.

**Numeric notation does not survive the database.** The parser now returns `PreservedInt` /
`PreservedFloat` for values whose text would not format back identically, so
`frequency=8.39314286e-01` writes back byte-for-byte — but JSON has one number type, so storing
it in `gd_data` flattens it and an exported line says `frequency=0.839314286`. Verified, and
unchanged from before the bump; `gd_data` is verbatim in content, not in bytes.

The sharp edge that comes with those wrappers: **`str()` on one returns the source text**, and
`synthesize_sequence_change` formats parsed values into `sequence_change`, which is one of the
seven fields `Mutation.objects.get_or_create` keys on. Left alone, `size=42` and `size=0042`
would have become two rows for one mutation. `gd_import._plain` coerces numerics first, and a
test imports the same mutation spelled both ways and asserts one row.

### Adding a mutation by hand

`/mutation-editor/add` records a mutation no sample carries yet, on one or more samples at
once. It is the third thing the editor does, and the only one that has to invent a `Mutation`
rather than move an existing one about.

**The form's fields come from `genomediff.schema.TYPE_SPECIFIC_FIELDS`.** That table is what
the parser fills a record from and what `Record.__str__` serialises in order, and it is
mirrored from breseq's `genome_diff_entry.cpp`. (It used to live in `genomediff.records`, which
still re-exports it.) `validation.form_schema()`
hands it to the page through `json_script`, so the type dropdown, the visible inputs, the
required-field check and the emitted `.gd` line read one table. Restating the field sets in the
template would give the form a second opinion about what a MOB needs. The nine offered types
are exactly the three-letter keys — `GenomeDiff.read` classifies by type-string length, which
is also why `Mutation.mutation_type` is `max_length=3`.

**Values survive a type change because they are keyed by the spec's field name.** The spec
already uses one name for one meaning, so `new_seq` is the same box in SNP, SUB and INS and
`size` in the six types that have it: switching type hides and shows rows and repopulates them
from a single `values` object, and a field the new type does not use keeps its value rather
than being cleared. The only translation beyond same-name is that SNP → SUB fills a blank
`size` with 1, because a SNP *is* a one-base SUB and the commonest widening of a call should
not fail for an empty field. A multi-base `new_seq` carried into SNP is *not* truncated —
validation names the problem instead of silently discarding what was typed.

**Validation is four stages and the order is load-bearing** (`validation.py`): shape, then the
no-ops that need no sequence (an AMP to one copy — refused even with no reference stored, since
nothing about it depends on one), then contig and bounds from `ExperimentReference.seq_ids`
(which carries a `length` per contig, so no file is opened), then the sequence no-ops. Only
that last stage loads the reference, which is why `load_references` is a *callable* and why
`SEQUENCE_CHECKED_TYPES` exists: a DEL's validity never depends on which bases it removes, and
loading parses the whole genome. Stage 4 must never run before stage 3 —
`ReferenceSequences.get_sequence_1` slices a plain string, so an over-long end returns a short
one and a start of 0 returns the wrong bases, both without raising.

The no-ops worth knowing: a SNP to the base already there, a SUB matching its span, **an
inversion of a palindrome** (a span that is its own reverse complement inverts to itself), a
CON/INT whose source region already matches its target, and a MOB naming a repeat family the
reference does not annotate. DEL and INS can never be no-ops. The interval each is checked over
comes from `annotate.annotator.mutation_interval`, reused rather than re-derived so the form
and the annotator agree about what a mutation covers.

**An experiment with no reference gets stages 1 and 2 only**, and the page says so in a banner
rather than quietly appearing to have checked.

**`record_builder.py` mirrors `gd_import._database_gd_mutations` for a single record**, because
a hand-entered mutation has to be indistinguishable from an imported one. Three details carry
that:

- `sequence_change` comes from `gd_import.synthesize_sequence_change`, which stopped being
  private for this. It is one of the seven fields `Mutation.objects.get_or_create` keys on, so
  a second rule for it would fork the mutation the next time the same call was imported.
- `gd_data` carries **no `id`** — `to_gd_line()` falls back to the row's own pk, and a record
  built before its row exists has no id to give — and **no `frequency`**, which is
  per-call while `gd_data` lives on the `Mutation` every observing sample shares.
- `annotation.apply_to` runs *after* `apply_edits`, because `history._resolve_mutation` sets
  only what the identity carries and the promoted columns (`snp_type`, `gene_name`, …) are not
  in it. Without that call the new row renders through the unannotated fallback.

The call is written with `source="manual"`, which distinguishes a typed call from a
called one everywhere that column is read; null would mean "imported before the column
existed", which is a different thing.

**A trap this shook out, left alone deliberately.** `get_annotated_gene_list(None)` stringifies
its argument, so `gd_import` has always written the literal string `"None"` into `Mutation.gene`
for a mutation with no annotation. This path reproduces it, because `gene` is part of the
get_or_create key and writing `""` here would fork every unannotated mutation on re-import.
Fixing it means changing both paths at once plus a data migration, and is its own change.

### The old way of deleting a mutation, and why it is gone

`AleExperimentFilter.ignored_mutations`, `AleExperimentFilter.starting_strain_mutations` and
`GlobalFilter.ignored_mutations` were comma-joined `Mutation.id` strings that
`filter_mutation_calls` excluded from every table. They were a delete that kept the row:
scoped to a whole experiment rather than a sample, recording nothing about who did it, with no
way back, and with nothing that ever pruned an id that had stopped meaning anything. The
mutation table's first column carried the same idea in miniature -- a close icon that removed
the row from the client-side DataTable until the next reload.

All of it is gone, along with `add_to_exp_filter`, its `mutation_to_exp_filter` route, its
dropdown entry, and `save_to_experiment_filter`/`deleteRow` in `table_template.js`.
`aledb_mutation_editor.migrations.0002` converts whatever those columns held into delete
edit sets before `aledb_filter.0003` drops them, so what was hidden stays hidden and becomes
inspectable and restorable; `aledb_filter.0003` depends on it, which is what stops the drop
running first. Those edit sets have `created_by` null and render as "system".

### There is one filter, and it belongs to the reader

**A filter is a value a reader carries, not a row.** `AleExperimentFilter` held one frequency
range and one ignored-gene list per experiment, edited at `/filter` by anyone with write access,
and it was *shared*: changing your own view changed everybody's, silently, with no record of who
did it. That conflated curating a dataset -- `aledb_mutation_editor`'s job, logged and
reversible -- with choosing what you want to look at, which is nobody else's business.
`aledb_filter.0006` drops the table, and logs what it discards, because there is nowhere to fold
it forward to and "calls below 20% here are noise" is a fact about the data that somebody may
have recorded in it.

`aledb_filter/view_filter.py` is the value and where it lives; `util.py` is what applies it.
Separate modules so a plugin importing the filter does not drag in `MutationCall`'s joins,
and so the value's tests need no database -- pinning the gene-subset rule used to take six model
rows. `ViewFilter` normalises `0` and `100` to `None`, which collapses "configured" and
"actually hides something" into one question: `is_empty`.

**It lives in `request.session`**, the repo's first session write, and the traps are in that
module's docstring rather than left to be rediscovered: the JSON serializer forbids sets,
integer dict keys come back as strings, and mutating a nested dict leaves `session.modified`
False and loses the write with no error. Query parameters win over the session and are
remembered into it; presence is what is tested, not truthiness, which is how a URL says "no
filter" and how the clear link works.

**No permission gate and no lock check.** `can_add_experiment_filter` and the experiment lock
guarded a shared setting; a reader's own view has nothing to protect. That absence is the
clearest statement of what the change is for.

Two consequences worth knowing. **`/stats` and Search do not filter at all** -- neither is a page
you read rows through, and the Overview summarises a dataset the way the dashboard does; that is
what made `_count_in_python` unreachable, since it existed only because the gene half of a filter
has no SQL. And **core has no experiment-scoped rebuilder left**: `experiment_filter` was the
last one, so several tests register their own rather than borrowing whatever was lying around.

`GlobalFilter` was a still earlier layer: one row for the whole installation, superuser-only,
reachable only by typing the URL, and empty in practice. `aledb_filter.0005` folded its genes
into each experiment before dropping it -- convert, then drop, the posture
`aledb_mutation_editor.0002` took with the ignored *mutation* lists.

**`can_add_global_filter` became `can_curate`, and the reason is not the one you would guess.**
Two call sites gated the tag dropdowns on
`can_add_global_filter(user) or can_add_experiment_filter(user, experiment)`. The left half was
`is_superuser`, so the `or` short-circuited before the lock on the right was ever consulted --
which is why `table_actions._may_curate` had to test the lock first and left a comment saying
so.

Deleting the left half outright looked safe because `effective_role` already answers `owner` to
a superuser. It is not: the case it really covered is a **`Mutation` with no experiment**, where
`can_edit_experiment(user, None)` returns False for everybody and an unscoped mutation becomes
uncurateable by anyone. `aledb_sample.tests.test_table_actions` pins it. `can_curate` keeps that
case and asks the question once, so the lock is no longer something to route around.

Two things that fell out with it: `aledb_interop_query` had been assigning `global_filter_genes`
and never reading it, and `aledb_stats._count_in_python` -- which carries its own copy of the
gene loop -- lost both its cross-experiment `deleted_global_mutations` cache and the copied
`and`/`or` precedence quirk, since a global gene meaning the same thing everywhere was the only
reason either existed.

### The ancestor belongs to the dataset, not to the reader

An ALE starts from an ancestor, and that ancestor already differs from the reference genome.
Those differences are the starting line rather than evolution, so
`Experiment.ancestor` names one sample and everything follows from that column:
its mutations are subtracted from every other sample before anything is computed, and the
sample itself leaves every listing. `aledb_experiment/ancestor.py` is the whole mechanism.

**It sits next to the section above and is the opposite of it on every count**, which is why it
is not in `aledb_filter`. A `ViewFilter` is per-person, session-scoped, ephemeral and clearable
in a click; this is shared, permanent, has no toggle and no query parameter, and reaches
aledb-phylogeny, which never touches the filter layer. Putting an unconditional exclusion inside
that package would undo the distinction it exists to draw.

**The idea was already here four times, and none of them subtracted anything.**
`Population.starting_strain`, a FK to `Isolate` that no code path ever wrote. `filter_out_wt_reseq`
and `get_wt_reseq_id`, helpers with no callers -- the subtraction that was meant to happen and
never did. `STARTING_STRAIN_ALE_ID = "0"`, which kept ALE 0 out of four pickers and three
dashboard counts while its samples went on landing in every analysis the moment no ALE was
picked, *which is the bug that convention actually had*. And
`AleExperimentFilter.starting_strain_mutations`, a hand-curated id list already migrated into
delete edit sets. All four are gone; `aledb_experiment.0010` drops the columns and backfills
the designation.

**That migration is the load-bearing part of retiring the label.** Nothing reads `"0"`
afterwards, so without a backfill every existing A0 starting strain would silently reappear
everywhere. Each experiment with exactly one sample under ALE `0` gets it as `ancestor`;
**ambiguity is skipped, not guessed** -- several samples under ALE 0 is a question about the
data only whoever ran the experiment can answer, and choosing for them would be a silent wrong
answer rather than a visible absent one.

**Two defaults, pointing opposite ways, and the asymmetry is the reason.**
`get_mutation_call_queryset` stays raw and `get_evolved_call_queryset` is the one
that subtracts; `get_ordered_reseq_queryset` subtracts by default and takes
`include_ancestor=True`. Forgetting to opt *in* hides the ancestor from a curation page, which
is visible and gets reported the same day. Forgetting to opt *out* leaves ancestral data in an
analysis, which is invisible and wrong. So each is defaulted to whichever mistake is louder.

`calls_for_samples(sample_ids, experiment_id)` is what a plugin derives from -- it was
`get_all_calls`, which had no callers because four repos had each written that one
line out by hand. **Dropping the ancestor from a sample list is not enough**: that removes its
column while its mutations sit in every other sample, and since an ancestral mutation is in
every ALE by construction, convergence reports all of them as convergent and fixation all of
them as fixed. The subtraction has to reach the derivation, not the render.

`exclude_all_ancestry` is the cross-experiment form, for search, the interop API and the
dashboard. One global exclusion is unambiguous because `Mutation` rows are per experiment, so an
id observed in one experiment's ancestor cannot appear in another's samples.

**Four deliberate exceptions**, and each one is stated where it is taken:

- **`/mutations/breseq` keeps the ancestor entirely** -- its rows tinted `ancestral_table_row`,
  and the sample itself listed in the picker, first, tinted the same red. It is what breseq
  called in one sample, not a conclusion drawn from it; a row silently missing would make the
  page disagree with the report it was imported from. And **nothing aggregates here**, so
  there is nothing for an ancestral call to contaminate -- while this is the only picker that
  could reach the ancestor, so hiding it made the sample unreachable rather than merely
  excluded. (It was hidden at first, on the general rule. One sample at a time is the case the
  general rule does not fit.) It passes `{% view_filter_summary ancestor_subtracted=False %}`
  so the shared summary does not claim a subtraction it did not do -- that rule cuts both ways.
  **Listed first is not selected first**: `_selected_reseq` opens on the first *non*-ancestor
  sample, because this view reads as "what evolved in this sample" and the one sample whose
  answer is "nothing, by definition" is a poor first thing to show. Its scoped fallback stays,
  for the ALE, sample-type and tag filters, which can still legitimately drop a requested id.
- **The mutation editor and the Edit-samples page** pass `include_ancestor=True` everywhere.
  They curate; they must be able to change what they are hiding.
- **The genome browser** keeps it, because the ancestor's own evidence link lands there and
  `is_current` would match nothing.
- **The `mut` export does not subtract**; a derived export (`fixed_mut`, `converged_mut`) is
  exactly what its page showed, because there is no un-subtracted version of "what converged".

**Designating is attributed** (`ancestor_set_at` / `ancestor_set_by`) and gated on
`can_edit_experiment`, so a locked experiment refuses it -- including refusing to *clear* it.
This is the only setting in the product that changes what everyone sees, which is exactly what
`AleExperimentFilter` got wrong.

Deleting the designated sample is the quiet failure: `SET_NULL` clears the column and the
derived data does not notice. `ancestor.note_sample_deleted` is a `pre_delete` receiver --
`pre`, because afterwards there is no way left to tell it was the ancestor -- and it marks
rather than runs, since it can fire in the middle of a cascade destroying the whole experiment.

### Every table says what filtering produced it

`{% view_filter_summary %}` renders a line under a mutation table naming the cutoffs and ignored
genes behind it. It exists because filtering is shared state that nothing announced: the four
plugins each chose differently what to do about it, and once the frequency cutoff started
actually working, `Population tree` began rendering 1,002 fewer calls in the filtered
views than phylogeny counts, with nothing to explain the difference.

**One resolution, two consumers**, and more directly than before: `describe_filters` and
`filtered_mutation_call_queryset` take the *same* `ViewFilter`, where they used to read the
same rows twice. Deriving the description separately would be a second opinion about what is
being hidden, and a page confidently describing
filtering it is not doing is worse than one saying nothing. Taking one value rather than reading
one source twice is what makes drifting hard rather than merely tested against.

**`applied` is not "is a filter configured".** A 0-100 range with no ignored genes is
configured and hides nothing; calling that "filtered" teaches people to ignore the word. It used
to need care; `ViewFilter` normalises those ends away at construction, so the two questions have
become one and `applied` is simply `not is_empty`.

**Template tags, not context keys**, and that is what makes them generic: they read
`experiment_id` and the request out of the context every table page already sets, so
including `{% view_filter_fields %}` and `{% view_filter_summary %}` once in
`base_table_template.html` reaches aledb-compare, aledb-fixation and aledb-converge **without
touching any of their repositories**. `{% view_filter_form %}` is the standalone variant, for a
page with no view-control form of its own to join -- the per-sample breseq table uses it.

**The tags gate themselves**, rendering nothing without an `experiment_id`. That replaced a
`show_filter_toggles` flag each page had to remember to set, which existed because a *Show
Experiment Filtered* checkbox had rendered inert on three pages that never read it back. The
rule it encoded still holds -- a control that does nothing is worse than no control -- but it is
a property now rather than a thing to remember.

A page filtering by its own rules passes `own_rules=` rather than rendering an empty summary
that reads as "no filtering here" when the truth is "different filtering here". Search does,
because it spans experiments; aledb-phylogeny does, because it encodes frequency in three
states rather than excluding on it.

**What it does not claim**: criteria a view hardcodes. `get_table_body` passes
`filter_type='AMP'`, so amplifications are excluded from the fixation and converge tables and
the summary does not say so. Widening it means every caller declaring what it passed.

**"Show Experiment Filtered" is gone.** It offered to see through the *shared* filter -- what
somebody else's setting was hiding from you -- which is not a question a reader has about their
own, where clearing it is a click away.

**A plugin gets both halves in two lines**, and `docs/plugin/filtering.md` is the page that says
so; there was none before, and `quickstart.md`'s worked example queried unfiltered. The half
worth repeating here: **a filter has to reach the derivation, not the render.** Fixation asks
what is in an ALE's last two flasks and convergence asks which genes were hit in more than one
ALE, so hiding a call changes the answer -- both plugins apply it when they compute their set
and pass `view_filter=None` to `get_table_body`.

Two things this shook out that are worth knowing:

- **Removing the close icon shifted every column of the shared mutation table left by one.**
  `REFSEQ_COLUMN_IN_MUT_TABLE` went 3 -> 2 and `aledb_export.util`'s `mut_pos_index` with it;
  everything in `table_template.js` is expressed relative to that constant, and aledb-compare,
  aledb-fixation, aledb-converge and `aledb_search` all import it rather than hardcoding an
  index, so they followed for free. Getting it wrong renders a table labelled one way and
  sorted another, which reads like CSS. `test_mutation_table_builder` now asserts the header
  and every row are the same width and that the constant points at "Reference Seq".
- **A filter with no cutoff at either end used to exclude the whole experiment.** An empty `Q`
  handed to `.exclude()` excludes everything. The guard survives in a new form -- with no cutoff
  the queryset is returned untouched and `.exclude()` is never called -- and the second copy of
  that block is gone with it: `aledb_interop_query` had rebuilt the whole thing by hand, skipping
  gene filtering as too slow, and now goes through the shared path. Its six endpoints take
  `min_freq`/`max_freq`/`ignore_genes` and default to unfiltered, because an anonymous caller
  used to get a view shaped by a setting they could not see.

### Creating and importing are pages, not dialogs

`/project/new/`, `/experiment/new/` and `/import/add/` are all full pages. The
first two were modals over the list tables and are not any more: a create form wants a
heading, room to explain its fields and a URL you can link someone to. The modals were also
where two bugs lived -- an inline panel overlapped the DataTable beneath it, and Bootstrap's
data-api was bound twice so a dialog opened and closed in the same millisecond.

`?project=<pk>` on the experiment page fixes the project, so arriving from a project there
is no picker to get wrong. Each page checks permission itself -- 403 signed out, 403 for a
project you cannot edit -- rather than relying on the button being hidden. The POSTs still
go to `project_create` / `experiment_create`, which is where the real checks live.

### Editing is three pages, and one of them is not what it looks like

`/project/<pk>/edit/`, `/experiment/<pk>/edit/` and `/sample/<pk>/edit/`, plus
`/experiment/<pk>/samples/` for the whole experiment at once. Same split as creating: a
GET page that checks permission itself, a `@require_POST` JSON endpoint that checks again.
All four gate on `can_edit_project` -- `Project.user` or a superuser -- so staff, who may
*view* every project, cannot edit one they do not own.

The project summary on `ale/project_detail.html` **stays read-only**; editing did not go
back into it. It now shows `description` and `status` as well, which were editable-but-never-
displayed before: a save has to be visible somewhere or it reads as having done nothing.

**Changing a sample's identity never writes a number.** `aledb_experiment/samples.py`
resolves (or creates) the `Population`/`TimePoint`/`Isolate`/`TechnicalReplicate` row for the *target*
A/F/I/R and re-points `Sample.tech_rep` at it. Those four rows are shared --
`gd_import._get_or_create_chain` reuses one `Population` and one `TimePoint` across every sample under
them -- so `flask.flask_number = 30; flask.save()` renumbers every sibling in that flask. The
FK re-point also keeps `reseq.pk` fixed, which the store paths (`samples/<pk>/aligned.bam`)
depend on, and makes a swap need no ordering logic: both samples move to freshly resolved
targets and the rows they vacated are pruned afterwards.

Three lookups there deliberately differ from `gd_import`, and each is a correction:

- `TimePoint` keys on `(ale_id, flask_number)` with `media` in `defaults`. `gd_import` passes
  `media=` as a *lookup* kwarg while `TimePoint` is unique on that pair, so it raises
  `IntegrityError` against an existing flask carrying different media.
- `Isolate` is `filter().order_by("pk").first()`, not `get_or_create`. `Isolate` has no
  `unique_together` and `gd_import` get_or_creates it on six fields including `sequencing_date`, so
  real databases already hold two rows at one `(flask, isolate_number)` and `get_or_create`
  raises `MultipleObjectsReturned` on them. Adding the constraint needs a data-repair
  migration and is a separate change.
- A newly created row inherits `media`, `freezer_box`, the isolate description and the
  replicate's text from the row it replaced. A renumber re-labels a sample; it does not move
  it to different growth conditions or discard what was written about the run.

Two samples may not share a coordinate, and the save refuses it. There is no constraint
saying so, but `aledb-fixation` builds `flask_isolate_mutation_dict[(flask, isolate)] = qs` by
plain assignment, so the second sample at a coordinate silently overwrites the first and its
mutations vanish from fixation with no error. Emptied rows are pruned bottom-up for the
opposite reason: `rebuild_sample_counts` counts `Population`/`TimePoint`/`Isolate` **rows**, and the
ALE picker is built from `Population` rows. An `Isolate` still referenced by another isolate's
`parent_isolate` or an `Population.starting_strain` is kept instead -- both are `DO_NOTHING`, so
the database would reject the delete, and nothing in the product writes either column.

A structural save calls `run_post_experiment_hooks` and `rebuild_sample_counts`, once per
POST. It deliberately does **not** call `rebuild_dashboard_data`, which pulls every
`MutationCall` in the database into Python -- nothing about a renumber changes a mutation
count, and paying for the whole database on every rename is what would make this feel broken
in production. A descriptive-only save rebuilds nothing.

**`TimePoint.flask_number` is labelled "Time point" on the edit pages.** It is the only ordinal
in the schema that places a sample along an ALE -- fixation sorts by it and takes the last two
to decide what has fixed -- and real data carries values like 30000, so it is plainly being
used to record cumulative divisions rather than a count of flasks. The column keeps its name;
only the UI changed, including the validation message, which is the one place the internal
name would otherwise reach a user. Its input is plain text, not `type="number"`: steppers are
useless on a five-figure value. **It is still an `IntegerField`, and is now the only member
of the coordinate that is** -- the ALE and the isolate became text (`aledb_experiment.0008`)
and this one deliberately did not, because it is the ordinal fixation reads. A fractional
time point is therefore still refused.

**No edit page touches `person`.** The field is absent from every form *and* from what the
endpoints assemble, so it is not merely ignored -- there is nowhere for a posted value to go.
Changing who owns or ran something is its own workflow: folding it into a details form means
every save rewrites it, and a form that dropped the field would silently blank it.

**The trap to know about:** a renumber often changes no visible label.
`label` returns `Isolate.description` verbatim whenever it is set, and the
import path fills it with the filename for every sample whose name is not `A-F-I-R`. So both pages show the computed `A# F# I# R#` beside the effective label and
keep the description editable in the same form. A duplicate `sample_name` within an
experiment is refused for a related reason -- re-import finds an existing sample by name --
but only when the name actually *changed*, or an experiment that already had a duplicate pair
could never be saved at all.

### Sharing: four roles, at the project level only

Access is granted on a **project** and nowhere else. An experiment, a sample and a mutation
are all reached through `experiment.project`, so there is one place to ask the question and
one place to change the answer. Nothing below the project has an owner.

    read  <  write  <  admin  <  owner

`read` sees the project and its data. `write` adds, edits and curates — data, samples, tags,
experiment filters. `admin` additionally manages access and may soft-delete the project.
`owner` additionally grants and revokes ownership.

`aledb_experiment/roles.py` holds the ordering and nothing else — it imports nothing from
Django, so `models.py` (which needs `ROLE_CHOICES` for a field) and `permissions.py` (which
needs `rank`) can both use it without importing each other. Roles are strings, not integers,
so the column reads in `/admin/` and in a sqlite dump; `roles_at_least()` is what turns
"role ≥ write" into one `role__in=[...]` lookup.

`aledb_experiment/permissions.py` is the whole policy, and `effective_role(user, project)` is
the whole of *that* — every `can_*` is a comparison against it. Three things confer a role
with no row: a superuser is owner everywhere, `Project.user` is owner of their own project,
and `is_public` gives everyone `read`. **There is no blanket grant for staff**; that clause
used to end `can_view_project` and, since `load_projects` marks every imported user staff, it
made nearly everything readable by nearly everyone. `./aledb project_access` is the escape
hatch for a deployment that relied on it.

**`ProjectAccess` is one row per (project, subject, role)**, where the subject is a user *or*
a group. Its unique constraints are conditional (`condition=Q(user__isnull=False)`) because a
plain `unique_together` would treat NULLs as distinct and permit unlimited duplicate group
rows. A group may not hold `owner`: ownership has to be answerable about a person, and
otherwise a group's manager could add themselves and own every project the group owns.

**`Project.user` stays, as the *primary* owner, mirrored by a `ProjectAccess` owner row.** It
is read by `Project.owner()`, two templates, `ProjectAdmin`, `load_projects`,
`try_creating_project` and `load_example` — which looks projects up *by* it. Multiple owners are
allowed, so a project whose only owner leaves is not stranded.

**The invariant is that `Project.user` always holds an owner grant**, and it is worth stating
that way rather than as "one writer", which this said for a while and which was never true.
Three functions write the field, and each has to maintain it: `set_primary_owner` (creation, and
`ProjectAdmin`), `revoke_project_access` (removing the primary owner's row re-points at the
longest-standing remaining owner) and `grant_project_access` (**demoting** the primary owner
re-points the same way).

That last one was missing, and its absence was not cosmetic. `effective_role` grants owner from
`Project.user` **alone** and short-circuits past the grant query, so a demotion that left the
field alone did nothing at all: the sharing page showed the new lesser role beside somebody the
server still treated as an owner. It sat directly on the path the product recommends — the
refusal an owner gets says *"Give ownership to someone else, then lower your own role"*, and
lowering it was the no-op. The end-to-end transfer test walked through the state and asserted
only the final one, because the third step happened to repair it.

**`_remaining_owner_count` counts `Project.user` as an owner**, with or without the row, for the
same reason: a guard reading `ProjectAccess` by itself would demote away the last owner of a
project whose owner is named only by the field — which is exactly what
`Project.objects.create(user=...)` produces and what the suite deliberately blesses.

`effective_role`'s shortcut stays. It is a safety net, not the source of truth: a missing mirror
row should be a display bug rather than an owner locked out of their own project, which is the
trap the guardian scheme had.

**`Project.user` is `on_delete=PROTECT`** (`aledb_experiment.0009`). The column is NOT NULL and
carries a real FK, so deleting a project's primary owner always failed — but under `DO_NOTHING`
it failed as an `IntegrityError` from SQLite at commit, after the admin's confirmation page had
promised otherwise. PROTECT refuses up front and names the projects in the way. `ProjectAccess.user`
stays `CASCADE`: a grant is disposable, and the primary owner always keeps theirs.

**`/admin/` goes through the same helpers.** `ProjectAdmin.save_model` calls `set_primary_owner`
and `ProjectAccessAdmin` routes saves and deletes through `grant_project_access` /
`revoke_project_access`, reporting an `AccessError` as a message. It is a superuser tool, but it
should not be the one place able to express a state the application forbids — editing that table
directly used to bypass the last-owner rule and the re-point together.

**The role cache is not an optimisation.** `mutation_table_builder` calls
`can_add_experiment_filter` once per sample column *and* once per mutation row, so a table of
400 mutations across 20 samples asks 420 times. django-guardian absorbed that in its own
per-user cache; `permissions.py` keeps a `{project_pk: (generation, role)}` dict on the `User`
instance, whose lifetime is naturally one request. A module-level generation counter, bumped
by every write helper, invalidates it — which is what stops a `User` object that outlives a
grant (every test, and the CLI) from answering with a stale role.
`test_permissions.CacheTestCase` asserts the query count directly.

**django-guardian is gone.** It stored a single `view_project` object permission and nothing
else, and the two lookups built on it filtered on the app label `'ale'` while the real label
is `aledb_experiment`, so they matched nothing. `0005` converts its grants to `read` rows and
every `Project.user` to an `owner` row. It reads guardian's table with **raw SQL behind an
introspection guard**, not `apps.get_model("guardian", ...)`: the ORM version only works while
guardian is still in `INSTALLED_APPS`, which would have forced migrate-then-uninstall across
two releases.

**That rule about `0003_backfill_view_project_grants.py` is retired**, along with the whole
migration history. It is worth one paragraph, because the shape of what it got wrong outlives
it. The file was inert and had to stay under its name, because a dependency on an uninstalled
app makes `migrate` fail with `NodeNotFoundError` on a fresh database — and this note listed
the four apps that named it: `aledb_sample.0005`, `aledb_stats.0003`, `aledb_filter.0002`,
`aledb_common.0001`. **There were five.** The fifth was
`aledb-phylogeny/…/0001_initial.py`, invisible to a note written from inside this repo, and it
is why the migration reset had to span four repositories at once. `./mutint check` passes
with a broken migration graph, so the two checks the suite's rules prescribe after a bump are
both blind to exactly that failure; only `migrate` or `test` finds it.

### Groups

`UserGroup` + `UserGroupMembership`, in `aledb_experiment`. Deliberately not
`django.contrib.auth.Group`: that one is administered from `/admin/`, has no owner, and
carries a `permissions` m2m that would sit unused inviting someone to wire model permissions
into a per-object scheme.

`is_manager` is a flag on the membership row rather than a second m2m, so "every manager is a
member" is true by construction. **The group's owner holds a membership row too**, written
when the group is created — that is what lets `effective_role` reach them through the same
join as everyone else instead of needing a `Q(group__owner=user)` special case in the hot
query. Owner renames/manages/appoints/transfers/deletes; a manager renames and manages plain
members only; a member just sees the page. The owner's row can never be removed or demoted —
transfer first, which leaves the outgoing owner a manager.

**Project roles and group roles are disjoint vocabularies with no bridge**, which is itself
the guardrail: holding `admin` on a project lets you add "Barrick Lab" to it and gives you
nothing whatever over that group. The corollary is that **the add-a-group box resolves against
`visible_groups(user)`, not every group**. There is no autocomplete — a box that resolved any
name would be an oracle: type names until one is accepted and you have enumerated every group
on the installation. A group you cannot see returns the same message as one that does not
exist, and `test_permissions` asserts the two strings are equal.

### The sharing and group pages

`/project/<pk>/access/` (admin or owner) and `/group/`, `/group/<pk>/`. Same
shape as every other page here: function-based views, permission checked inline, `403.html`
with a status, hand-written Bootstrap markup posting to `@require_POST` JSON endpoints through
`aledbPost` — no Django `Form` classes, no autocomplete, usernames typed in and resolved
server-side.

The role dropdown on a row posts to the **same** grant endpoint as the add boxes: it is an
upsert keyed on the subject, so there is no second endpoint that could disagree with it. A row
the actor could not re-grant — an owner, seen by an admin — renders as text rather than a
dropdown, so the page never offers a control whose every use the server would refuse.

**`project_access_bulk` applies one role to several subjects at once**, and does not overturn
the per-row design it sits beside. It **loops the same `grant_project_access` helper** every
other path calls, so each guardrail still delivers its own message and the endpoint knows none
of the rules. What it saves is page loads: adding ten people was ten reloads, each clearing
the box you were typing in. The add box takes a textarea of usernames for the same reason,
resolved one at a time because `resolve_username` answers one name at a time and each can fail
for its own reason.

It is deliberately **partial**, unlike `experiment_samples_update`, and that is the same
reasoning read forwards: a half-applied sample renumbering is incoherent, a half-applied set of
independent grants is just the subset that was allowed. It answers
`{applied, added, errors: {subject: message}}` and the page leaves those messages on screen
rather than reloading over them.

**The loop applies sequentially, and must.** The last-owner invariant is the one cross-row
dependency, and `_remaining_owner_count` reads the database on each call — so a batch validated
up front against the starting state would find every individual demotion allowed and leave the
project ownerless. Ticking every owner and setting them all to `read` demotes all but the last
and names the one it refused.

### Locking an experiment

`Experiment.locked_at` / `locked_by`, shaped like `SoftDeleteMixin` above — the timestamp
is the flag, with no boolean beside it to disagree. `/experiment/<pk>/lock/` sets it,
gated on `can_admin_project`.

**There is no `locked_reason` any more** (`aledb_experiment.0007`). It was a third column fed
by a text box on the lock dialog, which is a plain confirm now: the questions a lock has to
answer are whether the dataset is closed and who to ask about it, and the two remaining
columns carry both. `lock()` takes no `reason`, `lock_message()` is the experiment's name plus
the way out, and the endpoint ignores a posted `reason` rather than 500ing on an old client.

**A lock is not a fifth role.** It answers "is this dataset still open", which outranks "who
are you": a locked experiment refuses every web write from everyone, admins, owners and
superusers included. An admin unlocks, edits, and locks it again. That is the point — no
permission tier protects a finished dataset from someone who legitimately has permission and
did not mean to touch it.

`can_edit_experiment(user, experiment)` is the predicate, and
`can_add_experiment_filter` / `can_delete_experiment` delegate to it — which is what carries
the lock into the mutation editor's four endpoints, both tag endpoints, the filter page and
every `can_edit` context key without those files knowing about it. The nine call sites that
used to ask `can_edit_project(user, experiment.project)` now pass the experiment, because a
predicate handed the project cannot see a flag on the experiment.

Three places it would have silently not held, all now tested:

- **`_may_curate` short-circuits.** It reads `can_add_global_filter(user) or
  can_add_experiment_filter(...)`, and the first is `is_superuser` — so a lock tested only on
  the right-hand side is never reached for a superuser. The lock is tested *before* the
  disjunction.
- **`finalize_upload` had no permission check at all.** Permission was asked when the upload
  session was created and never again, so a session opened before the lock would still ingest
  after it. The backstop is in `import_registry.run_import`, the one funnel every import type
  passes through including plugin-registered ones, plus a check at the view for a clean
  refusal.
- **`project_delete`** refuses while the project holds a locked experiment, naming them.
  Otherwise the lock is sidestepped by the most obvious adjacent button.

`aledb_mutation_editor.history.apply_edits` raises `ExperimentLocked` too, trusting no
caller: it is the lowest layer that still knows which experiment it is writing to, and a write
path added later is exactly what forgets.

**What it deliberately does not stop.** Derived-data rebuilds — those recompute what the
mutations already imply, and a locked experiment whose counts silently went stale would be
worse, not safer. That covers more than it looks: aledb-phylogeny's build button writes a row,
and does not ask, because what it writes is a cached tree keyed by a selection one reader made.
The test is whether the write is *shared*, not whether it is a write. Reading and exporting. Access changes, since locking is per experiment and
access is per project. And **management commands**: the lock guards the web, so `./aledb
upload` still writes to a locked experiment, matching by name as it always has.

An experiment with **no project can never be locked**: `effective_role` answers `None` for a
null project before it reaches its superuser branch, so nobody holds admin on one. It cannot
be unlocked either, but it cannot get locked in the first place.

### Compare is a plugin

`/compare/` -- mutations as rows, samples as columns -- lives in the **aledb-compare** repo,
not in core. It is one way of looking at an experiment rather than a core function, which is
exactly what aledb-fixation and aledb-converge are: all three are a function view that builds a
context and renders `base_table_template.html` through `mutation_table_builder`. Compare was
simply the one that had never been moved out.

What stayed, and why none of it could go:

- **`mutation_table_builder`** -- `aledb_search`, `aledb_export`, aledb-fixation and
  aledb-converge all call it.
- **`base_table_template.html` and `table_template.js`** -- rendered by three other pages.
- **The curation endpoints**, now at `/mutation-table/` (`aledb_sample/views/table_actions.py`,
  `aledb_sample/table_urls.py`). Every table posts to them, not just Compare, and the state is
  shared: `TechnicalReplicate.tags` is what the Show/Hide Tag control filters sample columns on
  in `get_reseq_ordered_dict`, so tagging from one table changes what the other three show.
  Replicating them per plugin would have meant four write paths to core-owned tables.

**`/mutations/` is not a page any more** and returns 404. `aledb_sample.urls` has no `^$`, and a
plugin cannot reclaim that path: Django does not backtrack out of a matched `include()`.

Two consequences worth knowing before wondering whether something is broken:

- **Compare's nav entry sits at the end of the experiment section**, after Filter and the other
  plugins, because nav order is `INSTALLED_APPS` order and assembled projects append plugins
  after every core app. Listing `aledb-compare` first in `.gitmodules` makes it the first
  *plugin* entry, which is as close to its old position as the design allows.
- **Core's suite cannot reach it.** `./aledb test` has no plugin discovery, so Compare's tests
  run only as `./mutint test aledb_compare`.

`breseq_table.html` links to it through `{% url 'compare' as compare_url %}` inside an `{% if %}`,
so the "all samples" link disappears rather than dangling where the plugin is not installed --
the posture `nav_registry` takes with a `url_name` that will not reverse.

**The three endpoint URLs in `table_template.js` are reversed by name, not written out.** That
file is a Django template included inside a `<script>`, so `{% url %}` works there -- and so does
anything else tag-shaped, **including inside a `//` or `/* */` comment**, which the template
engine does not recognise as a comment at all. Writing a tag name in a comment there executes it.

Nothing in core rendered that file until this change: `aledb_search` has no tests and the other
three consumers are plugin pages. `aledb_sample/tests/test_table_actions.py` renders it directly now,
which is what lets core notice a broken tag before four pages do.

### Example datasets

`./aledb load_example` lists what is registered; `./aledb load_example <name>` loads it.
Components own their own data -- `aledb-fixation` ships `aledb-fixation-example` -- and
register it from `AppConfig.ready()` through `aledb_common/example_registry.py`, the sixth
registry.

**A dataset is a directory of files the import path already understands**, not a Django
fixture. Loading one runs the registered handlers in priority order exactly as a drop on the
Add Data page does, so the derived data it exists to show is genuinely computed. A fixture
would load faster and prove nothing. A `README` and dotfiles in the directory are skipped, so
the expected answer can live beside the files that produce it.

The command refuses a second load, naming `--replace`; `--replace` soft-deletes the previous
experiment rather than importing on top of it. Everything lands in one project called
**Examples**, created with the django-guardian grant -- `Project.objects.create` alone leaves
the owner unable to view what they own.

**Why this exists.** Fixed Mutations renders an empty table when an experiment has nothing
fixed, and an empty table when the feature is broken, and there was no data anywhere in the
suite that could tell the two apart.

Three are registered, one per plugin, each with a `README.md` beside its data stating the
expected answer as a table, and each asserted by that plugin's tests:

| dataset | shape | what it demonstrates |
|---|---|---|
| `aledb-fixation-example` | 2 ALEs x 4 flasks | a mutation in the last two flasks is fixed; one lost earlier is not |
| `aledb-converge-example` | 3 ALEs x 2 flasks | a gene hit in two lineages converges; one recurring in a single lineage does not |
| `aledb-compare-example` | 2 ALEs x 3 flasks | a pivot with full, partial and single rows across all six mutation types |

The first two have a computed answer to check. **Compare's does not** -- it derives nothing --
so what its dataset supplies instead is a *pattern*, because a table whose rows all look alike
demonstrates nothing. It is also the only one carrying every mutation type, `AMP` included:
that used to appear solely on `/mutations/amplifications` because Compare passed a
`filter_type` whose value means *exclude*, and the `AMP` row is what stops that returning
unnoticed.

### Reading a sample's identity out of its filename

**Two breseq folders of one name are one sample, and the second is refused.**
`find_sample_dirs` walks, so `a/s1` and `b/s1` are both samples called `s1` -- and a sample is
named by its directory's basename. That name is its identity: an A-F-I-R name parses to one
coordinate, and an auto-numbered one reuses the `Sample` already matching it.
So the second folder never arrived *beside* the first, it **replaced** it --
`_database_gd_mutations` deletes the sample's calls before writing its own -- and both
folders were reported as imported. Measured: two folders of two mutations each left two
mutations, the first folder's gone, with nothing said.

`_import_samples` now imports the first and reports every later one as an error naming both
paths. **Renaming the second automatically would be worse**, which is why it is not done: the
name is what `parse_sample_identity` reads the ALE, flask and isolate out of, so a name this
code invented would file the sample at a coordinate nobody chose and the drop would import
looking entirely successful. Which of the two was meant is a question only the person who made
them can answer.

`aledb_import/sample_names.py` is the one place a filename becomes a coordinate, asked by
`gd_import.import_document_as_sample` -- so a bare `.gd` and the breseq directory of the same
sample cannot answer differently. Two shapes are read and anything else is auto-numbered:

| name | ALE | flask | isolate | replicate |
|---|---|---|---|---|
| `3-30000-1-1` | `3` | 30000 | `1` | 1 |
| `Ara-2_500gen_763A` | `Ara-2` | 500 | `763A` | 1 |

**`Population.ale_id` and `Isolate.isolate_number` are `CharField`s** (`aledb_experiment.0008`),
which is what makes the second row expressible at all: `Ara-1` and `Ara+1` are two LTEE
populations that both end in 1, and `763A` and `763B` are two clones from one flask that
differ only in the trailer. Any rule reducing either to an integer merges rows that are not
the same sample -- and a merge is invisible, because `aledb-fixation` builds a dict keyed by
`(flask_number, isolate_number)` by plain assignment, so the second sample's mutations simply
vanish.

**`TimePoint.flask_number` stays an `IntegerField`**, and is the reason the middle field is the
only one whose trailing text is stripped: `500gen` is 500 because a time point is a genuine
ordinal that fixation sorts by. A middle field with no leading digit (`t0`) is not a time
point, so the whole name falls through rather than being half-read.

Three rules that look arbitrary and are not:

- **Exactly three underscore-separated fields.** With two there is no telling whether
  `Ara-2_500gen` omits the isolate or the ALE; with four, which extra field is the replicate.
- **A-F-I-R stays strict** -- all four dash-separated fields must be integers. It is checked
  first, so a name satisfying both shapes reads as A-F-I-R.
- **A name of neither shape is auto-numbered**, as before: ALE `1`, flask 1, one isolate per
  distinct sample name, and `Isolate.description` set to the filename so it still displays by
  name. `_next_isolate_number` counts in Python because `Max()` over a text column answers
  `"9"` for a flask holding 1 to 10.

`util.parse_ale_name` and `AleName` are **gone**. They read the same fields with a bare
`except: return 1`, so every field they could not read became 1 and a whole drop of
non-conforming files collapsed onto one sample. Nothing should reintroduce a lenient reader:
answering None and auto-numbering is what keeps a misread name addressable.

**Ordering had to follow.** A text column sorts `10` before `2`, which on an auto-numbered
import -- one isolate per sample, fifty-one of them in the dev database's largest experiment
-- reorders every mutation table's columns. `aledb_experiment/ordering.py` `sample_order()`
is what every sample listing orders by, in core and in aledb-phylogeny: it pads each text
field with zeros for the comparison, so digits sort by value and labels still sort as text.

### A mutation is fixated in the last two flasks, so the time axis must be the flask

`aledb-fixation` intersects an ALE's final two flasks by `flask_number`, so **an ALE with one
flask can never fix anything** -- and neither can an experiment made entirely of such ALEs.
That is the usual reason for an empty Fixed Mutations page and it is not a bug.

It used to be easy to arrive at by accident, and the second filename shape is what fixed
that. `gd_import` read a strict `A-F-I-R` filename (`1-1500-1-1.gd` = ALE 1, flask 1500,
isolate 1, replicate 1) and **anything else fell to auto-numbering, which puts every sample
under ALE 1 / flask 1 as separate isolates** -- so a 51-timepoint series imported as
`Ara-1_500gen_762B.gd` and friends became 51 isolates of one flask, with the generations in
`isolate_number`. That is exactly the shape of the MutInt dev database, and why fixation has
never produced a row there.

`Ara-1_500gen_762B` is read now: ALE `Ara-1`, flask 500, isolate `762B` -- see **Reading a
sample's identity out of its filename**. Data already imported does not move on its own; it
takes a re-import, or the sample editor, to put those samples on a time axis.

`./aledb fixation [<experiment_id>]` reports the count, and says when no ALE has more than one
flask -- which is the difference between an empty page and an impossible one, and the reason
the command still exists.

**It was `rebuild_fixation`, and there is nothing left to rebuild.** Fixation was stored in
`FixatedMutation` and recomputed by the post-experiment hook, so an experiment whose data
predated the plugin -- or whose filters had changed since, which altered what fixation would
find and rebuilt nothing -- stayed stale with no way to catch up. It is computed by the page
now, so staleness is not a state this data can be in, and only the diagnostic half remains.

### Which import types the Add page offers

Two rules, because the reasons differ. Both are declared on the handler and applied by
`get_import_types_for(has_reference)`, never in the template, so the dropdown and the JSON
the page classifies a drop with cannot disagree:

- `only_without_reference` -- `reference` stops being offered once the experiment has one.
  Establishing a reference is a one-time act; replacing the *annotation* is
  `replace_annotation`, and swapping in a different genome is deliberately shell-only.
- `requires_reference` -- `replace_annotation` and `genomediff` are **absent** until there is
  a genome. There is no greyed-out presentation and no `unavailable` parameter; a dropdown
  entry you can see and cannot pick is a dead end. The page's own banner carries the answer
  instead, and names the bare-`.gd` case explicitly, so the short menu is explained whether
  or not an entry is there to point at.

`breseq_folder` is never blocked: it brings its own reference. Auto-detect is unaffected --
a reference dropped alongside data still runs first on `priority`, which is how a first drop
establishes one. `/import/types/` stays unscoped; it has no experiment to scope by.

**The order they are listed in is not the order they run in**, and `menu_order` is what
separates the two. `priority` decides which handler runs first and is correctness -- a
reference genome must be established before mutations that are hash-checked against it -- so
it cannot be moved to make the menu read better without changing what a mixed drop does. Sorted
by it, the menu led with the two reference types and put `genomediff` last, which is the
commonest thing anybody opens this page to do. `menu_order` defaults to `priority`, so a type
that says nothing keeps its position; core sets `.gd` first, breseq folders second, and
`replace_annotation` last, that one being the rarest and least reversible entry. **Only
`get_import_types_for` sorts by it** -- `get_import_types()` stays in priority order, because
the page walks that list to name what an unrecognised file *looks* like and wants the handler
that would really claim it named first.

Because both the menu and the banner are rendered from `has_reference` at page load, a drop
that establishes one leaves the page stale. `finalize` therefore returns `has_reference`, and
the page **reloads** when it flips rather than patching the menu and the banner in the client,
where the two could drift from what the server would render. The import summary is the only
record that the drop happened, so it rides across the reload in `sessionStorage` under
`aledb-add-summary-<experiment>` and is re-rendered on the way back.

### An import reports itself, sample by sample

An import is long -- `coverage.build_quietly` walks the BAM and runs `bedGraphToBigWig` under a
900-second timeout **per sample**, and the derived-data rebuild after the last one counts every
call in the installation. Reported as a single POST, all of it was a page that had
stopped moving, which is indistinguishable from one that had broken.

`aledb_common/import_progress.py` is the seam. `run_import` **announces every unit before any
handler runs**, and each handler brackets its own work with `begin` / `report`; the Add page
lists every sample as *waiting* immediately and fills each in as it lands, above a bar counting
samples rather than bytes.

**It polls, and it deliberately does not stream.** A `StreamingHttpResponse` emits by
*yielding*, and progress arrives at `import_progress.report` in a callback several frames below
`run_import` -- a callback cannot yield. Any generator wrapping the import would queue every
event and emit the lot once it had already finished, which is the single POST it was meant to
replace. Streaming would need the import on a worker thread, and that is `WORKERS.md`, not a
progress bar. So `_SessionProgress` writes a snapshot onto `UploadSession.progress` as it goes
and `/import/uploads/<id>/progress` serves it. This works with no worker because **every
progress write lands outside the per-sample `transaction.atomic()` block**, in autocommit, so
the polling connection sees it at once.

Four things about it are load-bearing:

- **A snapshot, not an event log.** The page re-renders whole from whatever the last poll
  returned, so a poll that is slow, lost or doubled costs nothing and there is nothing to
  reconcile. It is also what the pre-listed table wants: a picture of the present.
- **A unit's announced name must equal the `file` key its result carries.** The two are paired
  by position, and the handlers disagree about what that name is -- a directory basename, a
  filename, a path relative to the drop root -- so `list_units` mirrors each handler's own
  reporting rather than there being one rule. `test_import_progress.AnnouncedNamesTestCase`
  pins each of them; getting it wrong renders a row that never fills in.
- **`list_units` exists because a breseq sample is five claimed files and one unit.** Counting
  claimed paths would report five times the work and count nothing anybody waits on.
- **`run_import` sets the cursor per handler** rather than letting reports count themselves. A
  plugin handler is free to report nothing -- its rows still arrive in the final summary -- and
  without an explicit cursor its silence would shift every row after it.

Progress writes are swallowed on failure, and a failed poll is a skipped tick rather than an
error. Commentary must not be able to fail an import, and the finalize response stays the
authority on what happened.

### Every sample lands, and it took three things

**Two of the three are left, and the reason to keep them changed rather than expired.** This
section is the history of a SQLite failure, and the suite is on PostgreSQL now; read it for why
`import_lock` and `retry` exist, not for what they defend against today.

- **The backend piece is gone.** `aledb_common.db.sqlite_immediate`, then
  `OPTIONS={'transaction_mode': 'IMMEDIATE'}`, then nothing: PostgreSQL has no deferred-`BEGIN`
  refusal to work around.
- **`retry` matches SQLSTATE now** (`40001`, `40P01`, `55P03`), not message text. It matched
  SQLite's and MySQL's wording, so on PostgreSQL it recognised *nothing* while still reading
  like live protection. Matching PostgreSQL's wording would be the same bug one step on --
  the server translates messages under `lc_messages`. Its premise is stronger here than it was:
  MVCC produces serialisation failures a single-writer database structurally cannot.
- **`import_lock` stays, and its stated reason is now the wrong one.** It was justified by
  "SQLite permits exactly one writer". What it actually buys, and buys more dearly now, is that
  it makes the suite's unconstrained `get_or_create` calls -- `Instrument`, `Experiment`,
  `Media`, `FreezerBox`, `Isolate` -- and `_next_isolate_number`'s unlocked read-then-write
  **unreachable by two importers at once**. SQLite's whole-database lock hid that; MVCC does
  not. Do not remove it on the grounds that its original justification expired.

The original account follows, because the measurements are the argument.

An LTEE drop lost five `.gd` samples to `database is locked`, reported per unit and genuinely
absent afterwards. The cause is not slowness. **Django 4.2's `atomic()` issues a deferred
`BEGIN`**, `Mutation.objects.get_or_create` reads before it writes, and SQLite **refuses that
read-to-write upgrade outright the moment another connection has written in between -- without
consulting `busy_timeout`**, deliberately, because waiting could deadlock two readers each
wanting to upgrade. No timeout and no journal mode can reach it.

Measured, three concurrent importers, one transaction per sample:

| | samples landed |
|---|---|
| deferred `BEGIN`, no retry -- what ran before | **42 / 150** |
| `BEGIN IMMEDIATE`, no retry | 150 / 150, but **19 / 30** once a transaction outlives the timeout |
| `BEGIN IMMEDIATE` + retry | 29 / 30 -- **bounded retry is not a guarantee either** |
| `BEGIN IMMEDIATE` + retry + one import at a time | **30 / 30** |

So all three are needed and none is sufficient:

- **`aledb_common/db/sqlite_immediate/`** -- the stock SQLite backend with
  `_start_transaction_under_autocommit` issuing `BEGIN IMMEDIATE`. **Delete it at Django
  5.1**, which has `OPTIONS={'transaction_mode': 'IMMEDIATE'}`; 4.2 has no such setting, which
  is the only reason a subclass of a private method exists. `test_concurrent_imports` asserts
  both the statement issued *and* that Django still has the hook, so an upgrade that moves it
  fails loudly rather than silently reverting every transaction to deferred.
- **`aledb_import/import_lock.py`** -- one import at a time, and the piece that actually
  closes it. **A row, not a Python lock**: the dev server is threaded and a deployment runs
  several processes, neither of which an in-process lock is visible to. Acquisition is the
  row *insert* rather than `select_for_update`, which is a documented no-op on SQLite and
  would look like mutual exclusion while providing none. A second finalize is **refused with
  409, not queued** -- holding a request open for somebody else's drop is the hang the
  progress reporting exists to prevent, and the staged files survive so retrying costs only
  the button. Stale locks are reclaimed after two hours, because a process killed mid-import
  cannot run its own cleanup.
- **`aledb_import/retry.py`** -- a sample that loses to some *other* writer tries again. Safe
  because each sample is already its own transaction and re-import is idempotent, so a failed
  attempt rolls back whole. Deliberately narrow: it matches lock wording only, since a
  malformed file fails identically every time and retrying it turns a clear message into a
  slow one.

**A poll must not write, and that is a consequence of the above rather than a detail.** Under
WAL a reader is never blocked, so a poll that only reads answers instantly however long the
importer's transaction runs -- but `BEGIN IMMEDIATE` holds the write lock for the *whole* of
each sample, so a poll that writes has to queue for it. `SESSION_SAVE_EVERY_REQUEST` made every
poll write one `django_session` row, and that was enough. Measured on a 30-second three-sample
drop:

| | polls | median latency | table appeared |
|---|---|---|---|
| poll writes a session row | **2** | **30.4s** | after 30.6s -- the whole import |
| poll reads only | **190** | **0.003s** | after 0.18s |

It was not slow, it was starved: the poll never got the write lock until the importer was
finished, so the page showed *Scanning the upload for samples…* for the entire run and the
table arrived with the result. `aledb_common/session_middleware.py` is a `SessionMiddleware`
subclass that skips the save for a request that sets `aledb_skip_session_save`, and
`upload_progress` is the only thing that sets it. **Deliberately not
`SESSION_SAVE_EVERY_REQUEST = False`**: that would fix one endpoint by changing when everybody
gets logged out.

**What survives all three is reported, not silent** -- a sample that still cannot be written
appears in the status table with its error, which is how the original five were found. That is
the honest ceiling here: everything lives inside one request, so none of it survives a crash or
a restart. A literal guarantee is durability of the *work item*, which is `WORKERS.md`.

**Postgres was considered and is not needed for this.** The 30/30 above is SQLite. What
Postgres would buy is *simultaneous* imports rather than queued ones -- a throughput question,
not a correctness one -- at the cost of the no-external-services property `./aledb start` is
built around. It is also not a guarantee by itself: it still raises serialisation failures and
deadlocks, so the retry would be wanted there too.

**And shortening transactions does not help.** Batching the per-record queries is worth doing
for import *speed* -- 2,599 per-mutation SELECTs measure 1.28s against 0.05s for one indexed
fetch, and 77% of the annotation UPDATEs rewrite identical values -- but measured against the
same contention it made failures *more* frequent, 50% to 66%. The risk is per attempt, not per
second held, so more and shorter transactions means more attempts. Batching is not a lock fix
and must not be counted as one.

**Polling a database that is being written is what forced `aledb_common/sqlite_tuning.py`,
which is deleted with SQLite.** PostgreSQL's readers are never blocked by a writer, so there is
nothing here to configure and no deployment ruled out — the paragraph is kept because it is the
measurement that explains why the progress endpoint is careful, not because the pragmas exist.
SQLite ships in rollback-journal mode, where a writer locks the whole file against *readers* --
so the first sample whose transaction outlives the 5s busy timeout made every poll fail, and the
contention pushed `database is locked` back onto the import itself. Reported against a real
Ara-3 import, then reproduced: `journal=delete, 5s` fails on poll number **zero**; `journal=wal,
30s` is clean. A `connection_created` receiver sets WAL, a 30s busy timeout and
`synchronous=NORMAL`, for SQLite only. WAL is what stops readers and writers blocking each
other at all; the timeout covers the writer-against-writer case WAL does not, since the poll
also writes a session row (`SESSION_SAVE_EVERY_REQUEST`). **It rules out one deployment:** WAL
coordinates through shared memory, which NFS and SMB do not implement, so a database on a
network share would need the receiver disabled.

**A page must be able to end an import without being told.** The finalize response is the
authority on the summary, and it can simply never arrive -- a dropped connection, a restarted
server -- which used to leave the page polling a finished import for as long as it was left
open. `upload_progress` reports the session's own state, so a snapshot reading `finalized` or
`failed` ends the polling and renders what the snapshot holds. Deliberately *not* the success
alert: the snapshot knows what this drop imported, not what the experiment now totals, and
inventing that would be a second answer to what the summary already answers properly.

**A late poll used to wipe the finished page.** `clearInterval` stops the next tick, not the
one already in flight, so a poll that resolved after the summary had rendered redrew the table
from a snapshot taken before the end -- taking the success alert with it, and leaving a page
that looked like it had never finished. `stopPolling` bumps a generation counter and a poll
whose generation is stale discards its own result.

**A re-import says what it displaced.** Importing a sample the experiment already holds is
allowed and destructive -- `_database_gd_mutations` clears the sample's calls before
writing its own, which is how a corrected breseq run supersedes the one before it, and
`test_reimport_is_idempotent` pins it. What it must not be is silent, so the row carries
`replaced`, the number of calls removed. That is **not** the same as two folders of one
name inside a single drop, which is refused outright: there, neither is an update of the other.
It is also deliberately not a `warnings` entry -- those are lines the parser could not read, and
the page says so in as many words.

**A reference genome has no mutation count, and reporting one said something false.** Every
file result carries `mutations`, so the reference handler filled it with the 0 it truthfully
imported -- and the Add page rendered that twice, in the table's Mutations column and in
*Added to E (#1): 0 mutations*, both of which read as a mutation file that landed nothing.
The entry sets `mutations` to None and declares `kind=KIND_REFERENCE`
(`aledb_common/import_registry.py`) instead; the page prints **Reference** in that column and
uses it for the headline when the drop imported no mutations. The kind rides through
`import_progress` onto the `UploadSession` snapshot as well, or the row would say one thing
while the import ran and another the moment it finished. Absent `kind` means the count is a
mutation count, which is every other handler -- so no plugin's handler changed.

`replace_annotation` claims only GenBank and GFF3, not FASTA. A FASTA is sequence with no
features, so there is nothing in one to install -- it used to be accepted and then rejected
on the sequence check, which named the wrong reason.

### A GenBank's contig names come from its LOCUS line

`annotate/genbank.py` takes each record's `seq_id` from **LOCUS** (`record.name`,
`NC_000913`), not from **VERSION** (`record.id`, `NC_000913.3`). breseq does the same
(`reference_sequence.cpp` `LoadGenBankFileHeader`), so every seq_id in a `.gd` it writes is
the unversioned one — and `ReferenceSequences.add` matches names **exactly**, on purpose, so
a reference that called the contig `NC_000913.3` would reject every one of those `.gd` files.
Taking VERSION here made a GenBank and breseq's own GFF3 of the same genome disagree about
what its contigs are called, which is the one disagreement the whole normalization design
exists to prevent.

The VERSION accession stays in `references.sequences` as an alias, so a lookup by either
spelling resolves. It is an alias only: `_sequences_of` and `render_breseq_gff3` both dedupe
by object identity and emit `annotated.seq_id`, so the canonical name is what gets stored and
hashed.

Existing references are untouched — their GFF3 was rendered when they were established. A
GenBank re-imported through `replace_annotation` against a reference established under the old
rule renames its contigs, which is exactly the case `reference_rename` asks about before
proceeding.

### Telling someone they picked the wrong type

The one mistake worth engineering for is a `.gd` chosen as a reference genome, or the reverse:
those are the two things an experiment is made of, and each parser's own words for the other
("No GenBank records found in: ...") name what failed rather than what to do. Three layers,
each catching what the one before it cannot:

- **Before anything uploads**, `renderList()` in `import/add.html` groups the files the chosen
  type declined by what they *do* look like, and names the type they belong to -- including a
  type this experiment cannot use yet ("...which needs this experiment to have a reference
  genome first"), which is why the page embeds the **unscoped** registry as
  `all_import_types` alongside the scoped `import_types` that fills the dropdown.
- **Server-side, by pattern**, `import_registry.identify()` turns `run_import`'s "not
  recognised as Reference genome" into "...it looks like GenomeDiff mutations, so import it
  with that type". Patterns only, never a handler's `detect`: the job is to name a likely
  alternative, not to re-decide what the file is.
- **Server-side, by content**, `aledb_import/sniff.py` reads the first non-blank line, which
  is where all four formats declare themselves. This is the layer that survives a wrong
  extension: a `.gd` saved as `.gbk` passes every pattern check above and is caught by
  `reference.detect_format`, and a GenBank named `.gd` by `gd_import._parse_document`.

The sniff is deliberately narrow. It overrides the extension only to *refuse*, never to route:
`detect_format` still trusts `.gff3` over content, and auto-detect still routes on `patterns`.
Only a positively identified other format is refused, so anything whose first line declares
nothing is still the parser's to judge.

### The genome browser

`aledb_sample/views/browse.py` renders igv.js for one `MutationCall` at
`/mutations/browse?mutation_call_id=<pk>`, linked from every mutation-table frequency cell whose
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

### A component can put a panel on the Overview

`aledb_common/panel_registry.py` is the eighth registry, and the first that lets an app put
**its own rendered content** on a core page rather than contribute a link, a heading, a handler
or a name. An app registers from `AppConfig.ready()`:

```python
register_overview_panel(self, name='needle_plot', title='Mutation Needle Plot',
                        template='needle/panel.html', context=needle_panel_context)
```

`/stats` draws the heading and the rule; the panel's template is the **body only**, so two
components cannot disagree about what a section there looks like.

**It exists because there was no seam for something that is one panel and not a page.** Every
earlier way to be seen was `register_plugin_urlpatterns` plus `register_nav_item` -- which is
what aledb-compare, aledb-fixation and aledb-converge are. `context_registry` gets *context*
onto an experiment view and stops there: some template must already be written to render it,
which is exactly the compile-time knowledge of a plugin core is built not to have. So the
needle plot lived in `aledb_stats` beside the Overview's counts for no better reason than that
`/stats` is where it is drawn. **It is the aledb-needle component now**, and standalone
aledb-core has no needle plot at all -- see that repo's `CLAUDE.md` for the plot's own design.

Four things about the mechanism:

- **A template plus a callable**, not HTML in a setting, for the reason `about_registry` gives:
  a template ships inside the app and a deployment overrides any component's panel by writing
  a file at the same path.
- **The callable takes `(experiment, request)`**. The request as well, because a panel may
  legitimately depend on the query string -- the needle plot's sequence picker is a `?contig=`
  away.
- **Each panel renders on its own**, rather than every panel's context being merged into one
  dict for `{% include %}` to sort out: two panels using the name `data` would otherwise
  silently read each other's. It is also what makes the isolation below expressible at all.
  `request=` is passed to the renderer, so a panel sees the same `aledb_version`, user and
  static configuration as the page around it.
- **A panel that raises is dropped with a logged warning** and the rest of the page renders --
  the posture `nav_registry` takes with a `url_name` that will not reverse. The trade is that
  a broken panel is quiet, which is why the warning names the app and the panel: the symptom
  is a missing section, which nobody can work backwards from.

Ordering is INSTALLED_APPS order with no parameter, as with `nav_registry` and
`about_registry` -- a panel's position is cosmetic.

**Core's own tests may only assert on the panels they register.** `test_panel_registry` was
written comparing the rendered list outright; it passed standalone and failed four ways under
`./mutint test`, because aledb-needle's panel is in that list too. Same rule as the About and
nav tests, sprung again in a new place.

### The mutations are drawn from the database, not from a file

`aledb_sample/tracks.py` builds igv features out of database rows. Until it existed `browse.html`
passed igv **`tracks: []`** -- the page drew the reference, the gene track and the reads, and
not the calls the reads were opened to look at. The only thing the database contributed was
the locus string that positioned the view.

igv takes features as an inline array, so this needs **no route, no store whitelist slot and
no `EXTENSION_CONTENT_TYPES` entry**. The module is pure, in the shape `locus.py` and
`functional_change.py` are, and reads `values_list` tuples the way `get_needle_plot_data` does.

One track is offered: **Mutations** (`annotation`, coloured by `functional_change_bucket`).
A second, **Mutations by sample** (`seg`, one row per sample in `get_ordered_reseq_queryset`
order), is still built by `sample_features` and **switched off** by `DRAW_SAMPLE_TRACK = False`.

**A switch rather than a deletion, and the distinction is the point.** What was decided in use
is that the per-sample band did not earn the vertical space it took under the Mutations track
-- not that it is wrong. `sample_features` is unchanged and still tested directly, so flipping
the flag restores a working track rather than resurrecting code that has rotted meanwhile.
`browse.html`'s `showSampleNames: true` is the other half of the switch and is kept for the
same reason: without it a seg track comes back with unlabelled rows, which is subtle enough to
be worth not rediscovering. `test_tracks.py` asserts both halves -- that only Mutations is
offered, and that `sample_features` still returns features -- so the day the flag moves, the
failure says which half moved.

Each track config carries an **`id`** (`MUTATION_TRACK_ID`), which is how the page finds the
clickable track without matching on the label a reader might reword.

**Coordinates are the trap.** igv features are 0-based end-exclusive; GenomeDiff positions are
1-based inclusive. `start = start_1 - 1`, `end = end_1`. Wrong, it draws every mutation one
base from where it is, beside the gene it is actually in, and nothing looks broken.

**Both are reached through the calls, not through `Mutation.experiment`.**
That column can be null -- the unscoped-mutation case `can_curate` exists for -- and two such
rows in the dev database were observed in an experiment while owned by none, so filtering on
it drew an empty Mutations track beside a populated per-sample one. Going through the
calls also makes both ancestor-subtracted, which is correct: an ancestral mutation is
in every sample by construction.

**The per-sample track marks presence, and deliberately does not encode frequency in colour.**
Three browser probes decided that. igv's `seg` scale is diverging around zero and built for
log2 copy ratios: raw frequencies in [0, 1] paint 5% and 100% the identical blue; mapped into
[0.35, 1.5] they rendered *lighter* as frequency rose; a symmetric [-1.5, 1.5] track rendered
*darker* toward both ends. Those are only consistent if the track **autoscales to its own data
range** -- which is the disqualifying property, not any particular direction: the same
frequency would look different on two experiments' pages and nobody could learn to read it.
Every feature carries `value = SEG_PRESENT` and the frequency rides alongside for igv's popup,
where it is a number rather than a suggestion.

`showSampleNames: true` is set on the browser, or a seg track draws its rows unlabelled.

**The sample label cannot come out of `values_list`.** `label` is a property
falling back through the isolate's description to a computed `A# F# I# R#`, so pulling
`...isolate__description` instead -- which this did first -- yields NULL for every sample
without one and collapses the whole experiment onto a single row named "sample". One small
query for the samples and a dict; there are tens of them, not thousands.


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

**The alignment track's own coverage row is off wherever the BigWig replaces it**
(`showCoverage: !t.coverageURL`). igv draws one inside every alignment track, so a sample was
showing the same depth twice in one column -- measured in a browser as two histograms, the
blue BigWig and a grey one directly beneath it, both scaled 0-169. Since coverage started
weighting reads by `1/X1` the two no longer even agree: igv counts every alignment once, so
its row still towers over a repeat while the track above it does not, and two coverage rows
disagreeing is worse than either alone.

Kept where there is *no* BigWig -- a sample imported before coverage existed, or one whose
derivation failed -- because igv's row is then the only coverage that sample has. Unnormalized,
but better than none, and `./aledb coverage` is what replaces it.

A sample imported before coverage existed simply has no wig track until `./aledb coverage` runs.

**Each alignment counts 1/X1, not 1**, and that is most of what `aledb_import/coverage.py` is
for. X1 is breseq's redundancy tag: how many places the read mapped equally well. A read
matching all ten copies of an IS element is written to the BAM ten times, once per copy, so
counting each as 1 gave every copy ten times its real depth and the trace was dominated by
repeats. Weighting by 1/X1 is breseq's own rule -- `coverage_output.cpp` accumulates
`unique_cov++` at redundancy 1 and `redundant_cov += 1.0/redundancy` otherwise, and reports the
sum -- so the browser now agrees with breseq's own coverage plots.

**A read with no X1 counts 1**, which is breseq's rule too (`alignment.cpp`: *"Defaults to 1
when custom breseq tag is missing"*). So a BAM from anything else is unaffected: there is a test
asserting the derivation reproduces what the old `bedtools genomecov -ibam -bg` pipeline
produced, and it was checked byte for byte against real bedtools output on a stored BAM before
that pipeline was removed.

Three things about it are load-bearing:

- **`bedtools` is gone and `pysam` replaced it.** `genomecov` cannot express a per-read weight
  -- its `-scale` is one factor for the whole file -- so the counting moved in-process. Only
  `bedGraphToBigWig` is still external. The obvious alternative, `bedtools bamtobed -tag X1`,
  **must not be used**: on a BAM whose reads lack the tag it writes a partial line, prints its
  error into the middle of its own stdout, and **exits 0**.
- **The tag is not stored as the type it is written as.** breseq spells `X1:i:<n>` in SAM text,
  but htslib stores an integer aux tag in the narrowest type that fits, so in the file it is
  `C`. Measured on a stored BAM, bowtie2's own `AS:i:` is `C` (and `c` when negative), never
  `i`. A hand-rolled reader matching type `i` would find no tags, weight every read 1 and report
  success -- the unnormalized answer, silently. `read.get_tag()` is htslib's own decode; that is
  the main reason this is a dependency rather than forty lines of struct parsing, and
  `test_coverage` pins both the storage type and every integer width.
- **pysam's failures have to become `CoverageError`.** It raises `ValueError` for a header it
  cannot parse, which is not in `build_quietly`'s except clause -- so untranslated it escaped
  and failed the whole sample import over a coverage track. A sample keeps its reads whether or
  not its coverage builds; there is a test.

**Nothing records which rule a stored BigWig was built under**, deliberately -- no column, no
migration. So every file built before this change is still the old unnormalized kind until
`./aledb coverage --force` re-derives it, and the only thing that can say whether a rebuild
changed anything is the tally that command now prints per sample: how much of the BAM was
redundantly mapped, or that it carried no X1 at all. That last line is the answer to "I rebuilt
and the repeats still spike" -- only breseq writes the tag.

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
only the reference and gene tracks. **Clicking a row toggles that one sample** -- it runs
`aledbSelectList` in `{toggle: true}` mode, matching the Tracks menu beside it. It used to run
the helper's default, where a plain click selects only the row it lands on: on a list of
samples that meant showing one silently unloaded every other, and there is no gesture that
puts them back except clicking each again. The four presets still set the whole selection,
which is what makes them presets. It has the same shape as the column menu on the Metadata
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

A `*` marks the samples the mutation is **in**, using the mutation table's own rule
(`present=True`) rather than a second one, so it agrees with the filled cells back on
`/mutations`. A MutationCall row alone is not enough: one with `present=False` records
that the mutation was looked for and found absent, and one with `present` null records nothing
either way. The same `*` is prefixed to the igv
track name, so a stack of pileups says which of them carry the call — igv puts no constraints on
a track name. In the menu a sample without one gets a same-width empty span so the names stay in
a column; on the track there is deliberately no such padding, because an igv track label is its
own shrink-to-fit badge with centred text and has no column to align to.

#### Clicking a mutation makes it the page's mutation

The Mutations track draws every mutation in the *experiment*, so a click on one is a request
to look at that mutation -- which is exactly what the breseq row at the top of the page and
the `*` flags in the sample menu are about. They used to go on describing whichever mutation
the page was opened at, while `tracks.py` had been carrying a `mutationId` on every feature
since the track was written, for a reader that did not exist.

**Swapped in place, not navigated to.** A page load would rebuild igv and re-fetch the BAM in
order to show a locus already on screen. `/mutations/browse/at` (`browse_at`) returns the new
row's HTML, the ids of the samples calling it, and the URL; the page replaces `#mutation-row`,
rewrites the menu's flags, retints igv's track labels and `history.replaceState`s the address.
**The locus is deliberately not moved** -- you clicked the feature, so you are looking at it.

`table_html` is rendered from the same `build_rows` call and the same
`breseq_table/_mutation_table.html` the full page uses, so a swapped-in row cannot drift from
a loaded one. `breseq_table.js` delegates from the document, so the Show button on a wide
deletion's gene list survives the swap with nothing to re-bind.

**The page has a second address, and this is what needed it.** `?mutation_id=&sample_id=` names
a mutation and a sample separately, because a mutation the sample does *not* call has no
`MutationCall` to name -- and the track offers plenty of those. `_resolve` takes either
spelling and `?mutation_call_id=` stays canonical, so every existing link is untouched. The
pair is checked to be one experiment's: two ids arrive from the client, and nothing else stops
a mutation from one experiment being paired with a sample from another.

**A missing call needed no new rendering path.** `_frequency` already answers
`("", False)` for a null frequency, so an **unsaved** `MutationCall` renders the row with
an empty Freq cell and `breseq_report.py` did not change. Nothing announces "not called in
this sample" either -- the Samples menu already says so, because the `*` follows the mutation
and the current sample is simply left without one.

**The `*` in a track's name is recomputed, not baked in.** It is part of the name
`sampleTracks` builds, and the page's mutation can now change under an already-loaded track --
so `markMutantTracks` strips any leading `* ` and re-adds it, rather than knowing what a
sample's two tracks are called.

igv's `trackclick` hands an annotation track the features actually under the cursor. Its
return value decides the popup: `undefined`/`true` gives igv's own, a string replaces it, and
**anything else suppresses it** -- so the popup is dropped for a mutation we switched to,
where the row at the top of the page is the fuller answer, and left alone for every other
track. Verified in a browser both ways: clicking the Mutations track switches with no popup,
clicking the gene track still pops up the gene.

#### The Tracks menu, because a removed track could not be got back

Genes and the database tracks are handed to igv once, at `createBrowser`, and igv's own
per-track gear menu has *Remove track*. Removing the Mutations track by accident meant
reloading the page, with nothing on the page saying so. The samples had a menu that showed
what was on screen and put it back; these had nothing.

`#track-menu` lists every non-sample track, ticked when it is on screen. It is built from
`[GENE_TRACK].concat(dbTracks)` rather than hardcoded, so it lists what the page actually
loaded -- an experiment with no mutations offers Genes alone, and a re-enabled
`DRAW_SAMPLE_TRACK` appears in it with no edit here.

**Deliberately not a Reset button.** Getting a track back should not cost the sample selection
you had arranged or send the Display setting back to Both, and it does not touch either.

**Independent toggles, so not `aledbSelectList`.** That helper is a *selection*, where a plain
click means "only this row"; these are checkboxes. It is a plain `ul.dropdown-menu` whose rows
carry `active`, which Bootstrap's own `.dropdown-menu > .active > a` paints -- the same thing
the sample menu relies on -- and the click handler stops propagation so the menu stays open
across several toggles.

Two things it gets right that are easy to get wrong:

- **`GENE_TRACK` is hoisted out of the `reference: {tracks: [...]}` literal** and given an
  explicit `order`. Without the hoist the menu would hold a second copy of the config to drift
  from; without the order igv appends a re-loaded track at the bottom, so a recovered gene
  track would come back underneath the reads it belongs above.
- **`trackremoved` is the single place a row unticks** -- this menu's own hiding does not clear
  the class itself, it removes the track and lets the handler do it. igv removes tracks by
  itself too, and two paths writing the same class is exactly how a tick comes to disagree with
  what is on screen, which is the fault this menu exists to make visible. igv runs every
  handler registered for an event and takes the first one's return, so this sits beside the
  sample menu's handler without either knowing about the other.

All four margins round the browser measure the same 25px; the header trim that makes the top
one work is in `common.css` and applies to every page (see **The shell's two widths**).

The cell markup is coupled to two things that substring-test it: `_contains_mutation` decides
whether a row renders by looking for `true`, and `table_template.js` colours a cell by testing
for `class="true"`. Keep that class on the anchor, and keep `true` out of the empty-cell literal.

### Everything the browser loads is served from here

`aledb_common/staticfiles/vendor/` holds every third-party asset, with
`vendor/VENDOR.md` recording the source URL and sha256 of each. **This is what makes "works
with no outbound network" true**, and it was not true before: `base.html` could not render
without jQuery from `ajax.googleapis.com`, while the suite pulled **22 assets from seven CDN
hosts**. All of them were reachable at the time, so nothing was broken -- the claim was simply
false, and the reason `igv.min.js` and phylotree are vendored had quietly stopped applying to
anything else.

**Versions are frozen at exactly what the CDNs were serving** -- jQuery 1.12.4, Bootstrap
3.3.7, DataTables 1.10.x, select2 4.0.3, several of them long EOL. Making the app work offline
and modernising a decade-old front end are separate problems; doing both at once leaves no way
to tell which half broke a page.

`aledb_common/tests/test_offline.py` fails on any `<script src>` or `<link href>` naming an
external host in a first-party template. It matches **asset loads only** -- a plain
`<a href="https://ncbi…">` is an ordinary link that costs nothing offline -- and it strips
Django comments first, because a commented-out include is not a load (`dashboard.html` carries
one). Two exemptions, each with its reason in the file: NCBI's sviewer (below), and Google
Analytics, which is now inside `{% if GOOGLE_ANALYTICS_TAG %}`.

**That analytics tag used to render unconditionally.** `GOOGLE_ANALYTICS_TAG` defaults to `''`,
so every page of every deployment fetched `gtag.js` from Google and reported to an empty tag
id, including deployments that had never asked for analytics.

Four things about the vendored layout are load-bearing:

- **A stylesheet drags its fonts with it.** Font Awesome asks for `url('../fonts/…woff2')` and
  Bootstrap for `url(../fonts/glyphicons-…woff2)`, so each `css/` keeps a `fonts/` sibling.
  Flatten the layout and every icon becomes a blank box **with no error at all** -- the page
  renders, the glyphs are simply gone. Verified in a browser with every host but this one
  unresolvable: Font Awesome and glyphicons both paint.
- **The two DataTables bundles are the only files not byte-identical to their source.** They
  embed Bootstrap and reference glyphicons at an *absolute* `/Bootstrap-3.3.x/fonts/…`, which
  resolves against `cdn.datatables.net`'s root and would 404 from ours; their `url()` paths were
  rewritten to a `fonts/` directory beside each bundle. `VENDOR.md` says so, and its hashes are
  of the rewritten files.
- **`sweetalert` had no version at all** -- `unpkg.com/sweetalert/dist/…`, resolving to whatever
  was current (2.1.2 when vendored). A major release would have changed the `swal()` API under
  ten templates with no commit here. Vendoring pinned it.
- **The four DataTables bundles stay distinct.** The pages differ in which extensions they use,
  so consolidating them is a behaviour change wearing a cleanup's clothes.

`?v={{ aledb_version }}` is deliberately **not** applied to these: every vendored path already
carries its version, so a release cannot serve half of one version and half of another.

**Two tests located Bootstrap by searching `base.html` for a `cdn.datatables.net` URL.** When
those strings vanished, one of them did not fail -- it began passing *vacuously*. `test_templates`
now asserts it can still find its subject, which is the general lesson: a guard that cannot
locate what it guards must say so rather than agree.


### The NCBI Sequence Viewer, and the check that has to come first

`aledb_sample/views/ncbi_view.py` renders NCBI's own Sequence Viewer at one mutation's locus, at
`/mutations/ncbi?mutation_id=<pk>`. It is the companion to the genome browser above: that page
shows a mutation as *reads*, this one shows it in *curated annotation* -- the genes, operons
and features NCBI holds, which the stored GFF3 track cannot supply because it carries only
what breseq's reference happened to annotate.

**A `Mutation`, not an `MutationCall`.** That is the one structural difference from
browse, and it follows from the page drawing no sample data at all: the Reference Seq cell it
is reached from is a property of the mutation's row rather than of any sample column.
`sample_id` is accepted and used only to keep the way back pointing where somebody came from.

**Nothing is drawn until the contig's sequence has been confirmed against NCBI**, and that
check is most of the feature. The reason is that getting it wrong is not a visible failure: a
near-miss accession -- a different *E. coli* strain, a different assembly version -- renders a
completely convincing page pointing at the wrong gene.

#### There is no accession stored anywhere, and that is deliberate

`aledb_import/annotate/genbank.py` `_record_seq_id` takes a GenBank's **LOCUS** name
(`NC_000913`) and not its **VERSION** (`NC_000913.3`), because breseq does the same and every
seq_id in a `.gd` it writes is therefore unversioned. The VERSION survives as an in-memory
alias in `load_genbank` and is never persisted -- `sequence_entries()` writes `{id, length,
sha256}`, and `seq_ids[].aliases` is written only by `reference_rename._record_aliases`.

So a contig name is an accession *by convention*, and conventions are what silently draw the
wrong gene. **Nothing here infers one.** There is no regex and no candidate: a person with
write access states which NCBI record a contig is, and the sequence decides whether they were
right. A pattern strict enough to reject `REL606` would also reject accession formats it was
not written for, and that failure is invisible -- the contig simply never becomes eligible.
Letting NCBI answer "no such record" is simpler and more honest than encoding its namespace
here.

`Mutation.ECOCYC_ACCESSION` + `is_ecocyc_gene()` are the pre-existing version of this idea,
hardcoded to K-12 and unchecked. This feature deliberately does not follow them and does not
touch them.

#### The check is a verification, not a search

`aledb_sample/ncbi.py`, two stages, cheapest first:

1. **esummary** -> `accessionversion` and `slen`. A length that differs is a definitive no,
   settled for one small request with **no genome downloaded** -- which matters, because a
   wrong accession is the common case. Verified live: `NC_000913` against a 48,502-base
   contig is rejected after one call.
2. **efetch**, streamed, hashed incrementally, compared to the `sha256` already in
   `seq_ids`. Equal is VERIFIED and stores NCBI's *versioned* accession; unequal at equal
   length is MISMATCH, which is the interesting case.

**No local file is opened at any point.** `seq_ids[].sha256` already is the per-contig digest,
so the comparison is a stored hash against a downloaded one.

Two consequences of it being a verification rather than a search, both worth knowing before
extending it. It **cannot discover** an accession, so a locally-named assembly stays unusable
until somebody states what it is -- the accepted cost of never guessing. And **the sequence
never leaves the deployment**: we send an accession and receive a genome. A BLAST-style
lookup would be the other way round, which for an unpublished ALE reference is a different
proposition entirely.

`sequence_digest_stream` is a **second implementation** of `reference.sequence_digest`,
written so a genome is never held whole in memory. Two digest functions that can disagree
would make every verdict meaningless while still looking like it worked, so their agreement
is a test rather than an assumption.

#### The verdict is keyed on the bases

`NcbiSequence.sha256` is unique and is the whole key -- not the contig name, not the
experiment. **The verdict therefore cannot outlive the sequence it was about**: there is no
path by which a stored accession survives onto different bases. It also means the same genome
in ten experiments is verified once, and that a contig **rename needs no hook**, because a
rename does not change the bases. (Extra keys on `seq_ids[]` would have worked too, but would
need `_update_sequence_fields` taught to carry the accession across a rewrite the way it
already carries `aliases` -- an invariant maintained by hand, in the function whose job is to
rewrite that list.)

Stating an accession is a **cross-experiment write**: someone with write access to experiment
A supplies a candidate experiment B then reads. That is safe because the candidate is not the
answer -- NCBI's sequence is -- so a wrong proposal becomes a rejected verdict, never a wrong
one. The POST is gated on `can_edit_experiment`, not `can_edit_project`: it writes, and a
predicate handed the project cannot see the lock that lives on the experiment.

A missing row means UNCHECKED, so nothing needs backfilling. Failures are stored too: a
MISMATCH somebody already paid for should not be re-fetched by the next reader.

#### The page never blocks on NCBI, and never renders a viewer it has not earned

Verification happens on demand -- a button on the page, or `./aledb ncbi_accessions` -- and
the page renders instantly from the stored verdict, the posture `./aledb coverage` takes.
Four states, each decided in the view and handed to the template as a boolean, and **the
sviewer `<script>` is absent from the page in all four non-verified ones**, the same
discipline `browse.html` applies to `igv.min.js`.

**The script is remote and cannot be vendored.** `https://www.ncbi.nlm.nih.gov/projects/
sviewer/js/sviewer.js` is a client for NCBI's own backend, so the page needs
ncbi.nlm.nih.gov reachable whatever we do with the file -- this is aledb-core's only runtime
CDN dependency. A deployment with no outbound network never reaches VERIFIED, so it never
renders the script either.

**`v=` and `mk=` are written with literal `:` and `|`**, as NCBI's documentation does, rather
than percent-encoded -- their note about escaping marker names by hand suggests the parser may
not decode first. What makes that safe is `_marker_label`, which strips every delimiter,
whitespace and HTML-significant character from the mutation's own text before it reaches the
href. That sanitising is load-bearing, not belt-and-braces.

Both `v=` and `mk=` are 1-based inclusive, as GenomeDiff positions are, so nothing converts
anywhere. The right-hand end is clamped using the `length` in `seq_ids`, so no file is read.

#### The extent rule moved, and there is one of it

`aledb_sample/locus.py` holds `mutation_extent` and `LOCUS_BUFFER_BASES`, extracted from
`browse.py` when this became a second page drawing the same interval. A pure module, in the
shape `functional_change.py` was extracted into and for the same reason: `views/common.py`
imports django.http and the permissions layer, and "which bases does this mutation cover"
should not require either. `browse._extent` survives as an alias. Two derivations would let
the two pages disagree about what a deletion covers while both looking correct.

#### The Reference page is where an accession is stated

`/mutations/reference?experiment_id=<pk>` (`ncbi_view.reference_view`) lists every
sequence in an experiment's reference -- name, length, the names it used to have, and which
NCBI record it is -- and carries the box that records an accession. It has a **nav entry** in
`EXPERIMENT_SECTION` called **Reference Sequence**, registered first, ahead of Mutations:
an experiment reads top-down from what it was aligned to, then what was found in it.

**Nothing showed any of this before.** The Add Data page knew only `has_reference`, as a
yes/no; the contigs, their lengths, their aliases and their NCBI status were visible nowhere
in the product at all.

**The nav entry is not decoration, it is the fix for a bootstrapping bug.** The link into the
viewer was at first gated on the contig already being verified -- so the only page carrying
the accession box could not be reached until the box had already been used. Two things
corrected it, and both are load-bearing:

- this page, reachable from the sidebar with nothing configured; and
- **every contig is linked from the mutation tables, verified or not.** That deliberately
  does *not* follow `_cell_html`'s rule about a sample with no BAM. That rule is right there
  because the page is a dead end -- there is nothing the reader can do about a missing
  alignment. This page is *actionable*: unverified, it names the contig and offers the box.
  The cell's `title` differs between the two states so the link does not promise annotation
  it cannot show.

The lesson worth keeping: **the states were each tested and the journey between them was
not.** `BootstrapJourneyTestCase` walks a fresh experiment from a table to a drawn viewer for
that reason.

`ncbi_check` is keyed on `(experiment_id, seq_id)`, not on a mutation: an accession
belongs to the reference, the Reference page has no mutation to name, and the mutation page
knows both anyway. One endpoint, one contract.

#### Three link sites, and what constrains the first

1. **The shared mutation table's Reference Seq column** -- `mutation_table_builder._refseq_cell`,
   reaching Compare, Fixation, Converge and Search at once. Three constraints, all of which
   fail silently if broken: the cell must not carry `class="true"` **or the literal string
   `true` at all**, since `_contains_mutation` substring-tests the row for it to decide
   whether the row renders and `table_template.js` tests for it to colour a sample cell; the
   column count must not change, because everything in `table_template.js` is indexed
   relative to `REFSEQ_COLUMN_IN_MUT_TABLE`; and the CSV export is unaffected because
   `aledb_export/util.py` re-derives `reseq_reference` itself rather than reusing this cell.
   `test_the_row_holds_the_reference_at_that_index` compares the cell's **text**, since the
   name is now wrapped in an anchor.
2. **The per-sample breseq table**, through a `refseq_url=` callable on `build_rows`, in the
   same shape as the existing `browse_url=`.
3. **The genome browser**, whose own row links across to the annotation at the same locus --
   reads and annotation one click apart rather than two pages that do not know about each
   other.

`verified_contig_names` is resolved **once per table**, not per row, and now decides only the
link's wording rather than whether there is a link.

#### What is deliberately not handled

**Accession versioning.** Whatever is typed is verified verbatim. An unversioned `NC_000913`
whose current NCBI version is not the genome held here reports MISMATCH -- correct, if blunt --
and `detail` carries enough for a person to retry with the version they meant. Walking back
through earlier versions is a non-goal for now.


### Django Apps

All apps use the `aledb_*` namespace. Key apps:

- **`aledb_experiment/`** — Core data models: `Experiment`, `Project`, `Population`, `TimePoint`, `Isolate`, `Media`, `FreezerBox`. Central schema everything else references.
- **`aledb_import/`** — Experiment upload pipeline. **Every path ends in `gd_import`**, so a
  CLI upload and a web drop produce identical rows:
  - `gd_import.py` parses with the external `genomediff` package (`GenomeDiff.read`) and is
    the one place mutations are stored. Each record is kept verbatim in `Mutation.gd_data`
    and round-tripped back out by `Mutation.to_gd_line()` (`aledb_sample/models.py`) for
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
    range support by `aledb_sample/views/alignments.py`, which resolves every path from a
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
    `aledb_sample/views/alignments.py`.
    Sample identity comes from the filename, through `sample_names.parse_sample_identity`
    and nowhere else -- see **Reading a sample's identity out of its filename** below.
- **`aledb_sample/`** — Mutation models and views (`/mutations/breseq`, `/mutations/browse`,
  `/mutations/ncbi`, `/mutations/reference`), the shared `mutation_table_builder`, and the
  curation endpoints at `/mutation-table/`. `/mutations/reference` is the only page that says
  what genome an experiment is called against, and is where an NCBI accession is recorded. `browse` is igv.js over the sample's reads; `ncbi` is NCBI's Sequence
  Viewer over curated annotation, and draws nothing until the contig's sequence has been
  confirmed to be the accession somebody claimed — see **The NCBI Sequence Viewer** above.
  Note `/mutations/` itself is **not** a page: it was Compare, now the aledb-compare plugin.
- **`aledb_fixation/`** — Fixated mutation computation.
- **`aledb_converge/`** — Convergence analysis across experiments.
- **`aledb_filter/`** — Experiment filtering UI and models: frequency cutoffs and
  ignored genes. The three mutation-id hide lists it used to carry are gone — see
  **The old way of deleting a mutation** above.
- **`aledb_mutation_editor/`** — Adding, deleting and copying a sample's mutations, with an
  append-only edit log you can restore from. See **Editing a sample's mutations** and
  **Adding a mutation by hand** above.
- **`aledb_metadata/`** — Parses XPMD metadata files associated with experiments.
- **`aledb_export/`** — Data export in various formats.
- **`aledb_stats/`** — The `/stats` page: the Overview's mutation counts, the sample table,
  and whatever the installed components register as panels. **It has no models.** Its counts
  were stored as `ExperimentSummary` with its own rebuilder, and are computed by the request
  that renders them now, in 0.07s on the largest experiment in the dev database — by reading
  the three or four columns the answer needs as `values_list` tuples instead of materialising
  every MutationCall as a model. The needle plot was the other half of this app, stored as
  `StaticData` and then computed the same way; it is the **aledb-needle** component now and
  reaches the page through `panel_registry`.
- **`aledb_search/`** — Cross-experiment search.
- **`aledb_bibliome/`** — Publication/bibliography management.
- **`aledb_dashboard/`** — Dashboard views and timeline events.
- **`aledb_accounts_noauth/`** — The auth slot's only occupant: Django's built-in login/logout, no enforcement. Swap in any other auth app by changing `INSTALLED_APPS`.
- **`aledb_common/`** — Shared utilities, middleware (`LoginRequiredMiddleware`), the eight
  registries (context, import, plugin, nav, about, example, **panel** and **rebuild**), and
  global static files. `DerivedDataState` is its only model.
- **`config/`** — Django project config: settings, root URLs, ASGI/WSGI entry points.

### Adding and deleting through the UI

Creation and deletion are nested under the objects they act on:

- `/project/` — **+ New project**, optionally creating its first experiment in the same
  step, and delete selected rows.
- `/experiment/` — **+ New experiment** (a project picker plus a name) and delete selected
  rows. `/project/<pk>/` carries the same **+ New experiment**, with the project implicit.
  Both POST `/experiment/create/` and land on the new experiment's Add page. The picker
  lists only projects `can_edit_project` allows, so it never offers one the POST would 403 on;
  a user with no editable project is shown **+ New project** instead.
- **`/project/<pk>/` deletes selected experiments too**, which for a long time it could
  not: it had the checkbox column all along — it includes the same
  `ale/experiment_datatables.js` the flat list does — and nothing that consumed the selection
  but Export. It is the page that shows a project's experiments in context, so it is where
  somebody is standing when they decide one should go. Gated on `can_edit` (project write)
  rather than on being signed in, as the flat list is: that list spans projects and cannot
  tell, while this page knows exactly one. A **locked** experiment in the selection still
  refuses from the endpoint and is named in `#del-error` — the lock lives on the experiment
  and only the experiment can answer for it.
- Shared JS for those controls lives in `aledb_common/staticfiles/js/aledb_crud.js`, loaded
  from `base.html`: `aledbPost`, the two confirm dialogs below, and `aledbDeleteSelected` —
  the whole gather-confirm-post-reload routine, which was inline in `ale/projects.html` and
  `ale/experiments.html` near enough byte for byte, and which the project page wanting it as
  well turned from two copies into an argument for none. (`aledbTogglePanel` was a third
  helper and is **gone**; the create forms are modals opened declaratively by Bootstrap. This
  line listed it for a while after it had been deleted, and
  `aledb_import/tests/test_add_page.py` asserts it is absent.) A page using `aledbPost` must
  render `{% csrf_token %}` somewhere: that is what sets the cookie it reads. Both confirms
  call `swal()`, which `base.html` does **not** load — pull sweetalert in per template.
- **Deleting data makes you type `DELETE`.** `aledbConfirmTypedDelete` is the dialog behind
  the four controls that destroy something — the two bulk deletes above, the project list's,
  and **Delete experiment** on `/stats` — and it resolves true only for that exact word.
  `aledbConfirmDelete`, the plain yes/no, stays for `ale/group_detail.html` and
  `ale/project_access.html`: removing a membership or revoking a grant destroys nothing, and
  a dialog that feels the same for both is what teaches people to click through the one that
  matters. The wording lives with each helper, so a page carries no delete copy of its own.
  - **It is client-side only, deliberately.** The endpoints already check the role and the
    lock, which is what actually protects the data; a server-side "must post DELETE" field
    would be a contract `./aledb delete` does not honour and one `curl` away from bypass,
    while reading like a security control. It guards the mis-aimed click and nothing more.
  - The trap in writing it: sweetalert resolves the **input's value**, not a boolean — `null`
    when dismissed, `""` when confirmed with an empty box. Both are falsy, so the
    `if (!confirmed)` idiom the plain helper's callers use swallows the second silently, and
    somebody who pressed Delete and saw nothing happen cannot tell that from a broken page.
    The two cases are separated so only one of them says anything.
- An experiment's page (`/stats?experiment_id=<pk>`) carries **+ Add data** and **Delete**,
  rendered through `{% block experiment_actions %}` in `aledb_common/templates/base.html`.
  **Deleting lands on the experiment's project**, not on the flat experiment list it used to
  go to — having just removed one experiment out of a project, the project is where the rest
  of them are. The destination rides on the button as `data-after-delete` rather than being
  rebuilt in the handler, so it is decided where the template can see whether there is a
  project at all. Its else-branch is a guard and not a live path: `Experiment.project` is
  nullable, but `effective_role` answers `None` for a null project before it reaches its
  superuser branch, so a projectless experiment is viewable by nobody and this page does not
  render for one — superuser included. There is a test pinning that, so the branch is not
  later read as a case somebody exercised.
- There is no Amplifications page. `/mutations/amplifications` was a copy of `mutation_table`
  differing in one argument, and it was the only page showing `AMP` mutations, because
  Compare passed `filter_type="AMP"` — a value that means **exclude** AMP, not include it.
  Both are gone and Compare now renders every mutation type. `AMP` was never a separate
  feature: it is one of eight breseq/GenomeDiff types, first-class throughout the pipeline.
- `/import/add/?experiment_id=<pk>` is the one place data goes in. It is scoped to an
  experiment **by primary key**, so two experiments may share a name and two people may add to
  the same one — unlike `_prepare_experiment`, whose name+person lookup forks an experiment per
  person. Use `gd_import.prepare_experiment_by_id` for anything web-facing.
  There is **no unscoped form of this page and no sidebar entry for it** — `aledb_import`
  registers no nav item. Both existed briefly and could only ever land on a page with no
  experiment to add to; without a usable `experiment_id` the route is now a plain 404.
- Everything records the logged-in user; there are no person fields to fill in.
- Editing lives beside all of this -- see **Editing is three pages** above. The controls in
  `stats.html`'s `{% block experiment_actions %}` are now gated on `can_edit`; Add and Delete
  used to render for everyone, which was a dead end dressed up as an action rather than a
  hole, since the endpoints refused anyway.

**Deletion is soft.** `Project` and `Experiment` carry `deleted_at`/`deleted_by`
(`SoftDeleteMixin`); only those two are flagged, and children are reached by traversal when
`./aledb purge_deleted --older-than <days>` finally removes them. `objects` is deliberately
unfiltered — a filtered default manager would silence the import paths' `get_or_create` — so
user-facing lists exclude deleted rows explicitly via `aledb_experiment.models.live()`.

### Import types are pluggable

`aledb_common/import_registry.py` is one of eight registries in `aledb_common/` -- alongside
`plugin_registry`, `nav_registry`, `about_registry`, `context_registry`, `example_registry`,
`panel_registry` and `rebuild_registry`. An app registers what it can ingest from `AppConfig.ready()` and it appears in the Add
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

`patterns` does more than route. `identify()` uses it to name the type a rejected file belongs
to, and the Add page serialises it to name the type a file in the drop belongs to before
anything uploads — so a plugin gets both of those by registering, with no edit to core.

### Pluggable App Slots

Some functionality is designed to be swapped by changing `INSTALLED_APPS`:

**Auth slot** — any app with `auth_app = True` in its `AppConfig` and `app_name = 'accounts'` in
its `urls.py` is auto-discovered by `config/urls.py`. `aledb_accounts_noauth` is the only
occupant, and the resolver takes the **first** match, so a second installed one is ambiguous
rather than additive.

**There is no brute-force protection, and that is a gap rather than an omission.** There was a
second app, `aledb_accounts`, whose entire reason to exist was carrying `django-defender`. It
was in no settings module's `INSTALLED_APPS` and defender was in no `requirements.txt`, so the
"production" auth app could not actually be installed — and once defender went, what remained
was identical to the default. An alternative that is not an alternative is worse than one
occupant and an honest sentence, which is what this is. Anything that replaces it inherits the
routes below rather than restating them.

**The routes and templates are shared, and the slot is not where they live.**
`aledb_common/account_urls.py` holds all four — login, logout, `password/` and
`password/done/` — and an auth app's `urls.py` is three lines that serve that list. The
templates are `aledb_common/templates/accounts/`. Both sit outside the slot so that whatever
occupies it next inherits them, rather than being expected to write them again.

**That is the lesson the second app left behind, and it is why this is worth a paragraph.**
When there were two hand-written lists they had already drifted into two live bugs nothing
exercised: `aledb_accounts` passed `{'next_page': '/'}` as `re_path`'s **extra-kwargs dict**
rather than to `as_view()`, raising `TypeError` on the first login, and it had no
`registration/login.html`, so swapping the slot also meant `TemplateDoesNotExist`. Neither was
found by running it — nothing installed it. `aledb_common/tests/test_accounts.py` now asserts
the *mechanism* instead: exactly one app declares the slot, and what it declares is what
`/accounts/` actually reverses to.

**Experiment context providers** — registered via `aledb_common.context_registry.register_experiment_context_provider()` in `AppConfig.ready()`. Used by `aledb_bibliome` to inject publication data into experiment views without a hard dependency.

### Settings Structure

- `config/defaults.py` — Delegates to `aledb_common.base_settings.get_base_settings()`; adds `ROOT_URLCONF` and `WSGI_APPLICATION`. SQLite fallback when `FORCE_SQLITE=1` or running tests.
- `config/settings_local.py` — Local dev (SQLite, DEBUG=True, no Redis/Azure). Created by `./aledb start`.
- `config/settings_private.py` — Production: adds `LoginRequiredMiddleware`, and nothing
  else. It never swapped the auth app, whatever this line used to say.
- `config/settings_public.py` — Public read-only deployment.
- Select with `DJANGO_SETTINGS_MODULE`.

### Data Flow: Uploading an Experiment

1. `./aledb upload <path>` calls `aledb_import.ale_experiment.upload_collection()`
2. Hands each `<exp>/breseq/` to `aledb_import.breseq_folder`, which parses via
   `aledb_import.gd_import` / the `genomediff` package -- the same route a web drop takes
3. Creates `aledb_experiment`, `aledb_sample`, and `aledb_metadata` model instances
4. Ends in `gd_import.run_post_processing`, which asks the rebuild registry to recompute
   everything derived -- the experiment filter defaults, `aledb_fixation`'s table, then the
   dashboard's installation-wide totals. It asks for whatever is registered, so the needle
   plot, the Overview's counts and convergence dropped off it by ceasing to be registered
   rather than by an edit here. See **Derived data and rebuilds** in the suite `CLAUDE.md`;
   none of it is the Django cache framework, which this repo does not use.

### Infrastructure (production)

- Database: **PostgreSQL**, and only PostgreSQL. Either the cluster the entry script manages
  under `env/`, or one you run yourself by exporting `ALEDB_DB_HOST`. See **The database** in
  the suite `CLAUDE.md`.
- File storage: `ALEDB_STORE_DIR`, keyed by database id (`aledb_common/store.py`)
- Background work: **a worker, and it has to be run.** `TASKS` names
  `django_tasks_db.DatabaseBackend`, and `./aledb db_worker` is what executes what has been
  enqueued. Nothing spawns one for you. Today exactly one thing is enqueued -- coverage
  derivation -- and its degraded state is benign, so an installation with no worker running
  imports correctly and simply has no coverage tracks until `./aledb coverage` is run.

There is still **no broker and no scheduler**; `./aledb reap_uploads` is still cron's job.

This section used to claim Daphne, Django Channels, nginx and Redis. **Nothing in
`requirements.txt` supported any of it** and no compose file in this tree referenced it -- it
was inherited from the pre-refactor deployment. It then said everything was synchronous
in-request with no worker at all, which was true until the queue landed. `WORKERS.md` in the
suite root is the design note this half-discharges: the seam and the first task exist; the
enqueue-instead-of-call for the *rest* of `run_post_experiment_hooks` does not.
