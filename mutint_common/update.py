"""Moving this checkout onto a newer version of itself, in place.

MutInt is installed as a git checkout and updates by fetching one. There is no release
artifact and there deliberately is not: the data an update must not lose -- the cluster in
``data/db`` and the file store in ``data/store`` -- lives *inside* the directory an unpacked
archive would replace. Git already knows to leave ignored files alone, which is the whole
reason this works at all.

**A tag is the unit of release.** ``git ls-remote`` reads tags off the remote directly, so
none of this needs a GitHub Release object, an API token, or an API request that could be
rate-limited. Two channels, and they are different in kind rather than degree: ``stable``
takes the highest ``v*`` tag, ``main`` takes the tip of the development branch.

**Not in the entry script**, though the entry script is what calls it -- the same bargain
``pg.py`` makes, and for the same reason: that file is three byte-identical copies with no
tests, and this is code that moves somebody's installation.

**Parseable on Python 3.9, standard library only, no Django import.** The entry script loads
this by path *before* the venv exists, under whatever ``python3`` the host has.
``mutint_common/tests/test_update.py`` asserts all three, because nothing else would catch
them on a machine that has already updated.

### Staged here, applied at the next launch

A running process cannot safely replace its own source and rebuild its own virtualenv: the
autoreloader would fire part-way through the checkout, a new ``requirements.txt`` cannot be
installed into the environment currently executing, and migrations would run against
half-swapped code. So ``/update/`` **stages** a request into ``data/update.json`` and the
next ``./mutint start`` applies it before Django is importable.

Everything after the checkout is machinery that already exists. The entry script's sentinels
hash every component's ``requirements.txt`` and ``tools.txt``, so a version that changed
either rebuilds the venv and re-solves the tools with nothing written here; ``start.py``
then migrates. This module only has to move the working tree.

### What it refuses

The refusals matter more than the happy path, because this same entry script runs in the
development checkouts of this suite, where an update would be vandalism. It stops, naming
what it found, on a dirty working tree, on a HEAD carrying commits the remote has not seen,
and on a checkout with no ``origin``. Nothing is fetched until those pass.
"""

import json
import os
import re
import subprocess

#: Where the channel, the last check and any pending request live. Under ``data/`` because it
#: is state this deployment owns and would miss, and because ``rm -rf env`` -- the documented
#: way to reset the tools -- must not take a staged update with it.
STATE_NAME = os.path.join('data', 'update.json')
#: What the file was called while the feature was called upgrading. An installation that
#: chose the Development channel wrote that choice here; `state_path` moves it across once
#: rather than quietly putting such an installation back on the stable channel.
LEGACY_STATE_NAME = os.path.join('data', 'upgrade.json')

#: Where a pre-update dump goes. Beside the database rather than inside it.
BACKUP_DIR = os.path.join('data', 'backups')

#: How many pre-update dumps to keep. Nothing else in the suite prunes anything under
#: `data/`, and this is the one part of it that grows without anybody asking for it: each
#: dump is the whole database, and the Development channel follows `main`, so an update
#: can happen daily. Five reaches back past a bad update nobody noticed for a few days,
#: and keeps the backups from quietly outgrowing the database they came from.
BACKUP_KEEP = 5

#: The shape `backup` writes, and the only shape `_prune_backups` will delete. Anything
#: else in that directory was put there by the operator and is theirs.
_BACKUP_NAME = re.compile(r'^pre-.+-\d{8}-\d{6}\.sql$')

STABLE = 'stable'
MAIN = 'main'
CHANNELS = (STABLE, MAIN)

#: The branch the development channel follows. Every repo in the suite is on `main`.
MAIN_BRANCH = 'main'

#: A release tag: `v` and a dotted number, nothing else. Deliberately strict -- `git describe`
#: output, release candidates and `testdata-*` asset tags are all things a tag namespace
#: accumulates, and an update should move between reviewed points or not at all.
TAG_RE = re.compile(r'^v(\d+)(?:\.(\d+))*$')

#: How long any single git call may take. An update check runs from a web request; one that
#: hangs is worse than one that says it could not reach the remote.
TIMEOUT = 60


class UpdateError(Exception):
    """This checkout cannot be updated, and the message says why.

    Distinct from `Unreachable` on purpose: "you have uncommitted work" is an answer about
    this installation, and no amount of retrying changes it.
    """


class Unreachable(UpdateError):
    """The remote could not be asked. Distinct from any answer it might have given.

    Mirrors `mutint_sample/ncbi.py`'s `_Unreachable`, and for the same reason: a page that
    cannot tell "there is no update" from "I could not check" will eventually tell somebody
    they are up to date when they are not.
    """


def project_root():
    """The directory of the project being run, or None if no entry script exported it.

    `MUTINT_TOOLS_DIR` is `<project root>/env/tools`, exported by the entry script before it
    re-execs, and it is the only thing that knows: settings cannot, because an assembled
    project reaches `get_base_settings()` through mutint-core's `config/defaults.py`, which
    passes the *mutint-core* directory -- the same trap `templates/` and `staticfiles/` work
    around. So an update run from inside `mutint/` must move `mutint/`, not the submodule the
    code happens to live in.

    `mutint_common.docs_manual.project_root` is the same three lines, and they are deliberately
    not shared: that module imports Django, and this one is loaded before Django exists.
    """
    tools_dir = os.environ.get('MUTINT_TOOLS_DIR')
    if not tools_dir:
        return None
    return os.path.dirname(os.path.dirname(os.path.abspath(tools_dir)))


def git_path(base_dir):
    """The git to use: ``env/tools/bin/git`` first, then PATH, else None.

    This repeats `mutint_common.tools.tool_path()` rather than calling it, and the duplication
    is deliberate: this module is loaded before the venv exists, so it can import neither
    Django nor anything that imports Django. Keeping the *order* the same as `tool_path`'s
    matters more than sharing the code -- a checkout that resolved its own git differently
    from every other tool would be a surprise waiting to happen.
    """
    managed = os.path.join(base_dir, 'env', 'tools', 'bin', 'git')
    if os.path.isfile(managed) and os.access(managed, os.X_OK):
        return managed
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, 'git')
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _git(base_dir, *args, **kwargs):
    """Run git in `base_dir` and return stdout, or raise.

    `check` is the caller's choice of which failures are interesting; a non-zero exit with
    `check=False` comes back as an empty string, which is what the "is this even a
    repository" probes want.
    """
    check = kwargs.pop('check', True)
    exe = git_path(base_dir)
    if exe is None:
        raise UpdateError(
            "No git found. It is normally installed into env/tools by `./mutint install`; "
            "a copy on PATH works too.")
    try:
        completed = subprocess.run(
            [exe, '-C', base_dir] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Unreachable("git %s timed out after %d seconds." % (args[0], TIMEOUT))
    except OSError as exc:
        raise UpdateError("Could not run git: %s" % exc)
    if check and completed.returncode != 0:
        message = completed.stderr.decode('utf-8', 'replace').strip()
        raise UpdateError("git %s failed: %s" % (args[0], message or 'no output'))
    return completed.stdout.decode('utf-8', 'replace')


# ── state ────────────────────────────────────────────────────────────────────────────────


def state_path(base_dir):
    path = os.path.join(base_dir, STATE_NAME)
    legacy = os.path.join(base_dir, LEGACY_STATE_NAME)
    if not os.path.exists(path) and os.path.isfile(legacy):
        try:
            os.replace(legacy, path)
        except OSError:
            return legacy
    return path


def read_state(base_dir):
    """The stored state, or an empty default. Never raises: a corrupt file is not worth
    taking a launch down for, and the next write replaces it."""
    try:
        with open(state_path(base_dir)) as handle:
            state = json.load(handle)
    except (IOError, OSError, ValueError):
        return {'channel': STABLE}
    if not isinstance(state, dict):
        return {'channel': STABLE}
    state.setdefault('channel', STABLE)
    return state


def write_state(base_dir, state):
    path = state_path(base_dir)
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    # Written whole and renamed into place: a launch that reads this file half-written would
    # act on a request nobody made.
    temporary = path + '.tmp'
    with open(temporary, 'w') as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write('\n')
    os.replace(temporary, path)


# ── inspecting the checkout ──────────────────────────────────────────────────────────────


def is_git_checkout(base_dir):
    return os.path.isdir(os.path.join(base_dir, '.git'))


def current_ref(base_dir):
    """What this checkout is on: a tag if HEAD is exactly one, else the short SHA."""
    tag = _git(base_dir, 'tag', '--points-at', 'HEAD', check=False).strip().splitlines()
    for line in tag:
        if TAG_RE.match(line.strip()):
            return line.strip()
    return _git(base_dir, 'rev-parse', '--short=8', 'HEAD', check=False).strip() or None


def origin_url(base_dir):
    return _git(base_dir, 'remote', 'get-url', 'origin', check=False).strip() or None


def changed_tracked_files(base_dir):
    """Porcelain lines for tracked files that differ from HEAD. Untracked files are excluded.

    **Excluding `??` is the difference between this working and not**, and it governs both
    refusing an update and verifying an adoption. Every installation accumulates untracked
    files: `data/` and `env/` are gitignored so they never appear here, but an export somebody
    downloaded into the directory does -- and an installation that stopped being updateable
    the first time a file was saved beside it would be no installation at all.

    It is not leniency about losing work, either. `git checkout` does not discard an untracked
    file: it refuses and names it, which `apply` passes on as its own error. What a *tracked*
    file differing means is the thing worth stopping for -- somebody is working in this
    checkout, and moving it would throw their edits away.

    Not `.strip()` on the output: porcelain's first two columns are the status and the third a
    space, so stripping the whole output eats the first line's leading space and `line[3:]`
    then slices into the filename. Split first, then take the path.
    """
    return [line for line in
            _git(base_dir, 'status', '--porcelain', check=False).splitlines()
            if line.strip() and not line.startswith('??')]


def blockers(base_dir):
    """Everything that stands between this checkout and an update, as sentences.

    Returned as a list rather than raised one at a time, so the page can show a reader every
    reason at once instead of making them fix one and come back.
    """
    problems = []
    if not is_git_checkout(base_dir):
        problems.append(
            "This is not a git checkout, so there is nothing to fetch into. "
            "`./mutint update --adopt` turns an unpacked archive into one without touching "
            "your data.")
        return problems

    if origin_url(base_dir) is None:
        problems.append("This checkout has no `origin` remote, so there is nowhere to ask.")

    dirty = changed_tracked_files(base_dir)
    if dirty:
        names = [line[3:] for line in dirty[:5]]
        more = len(dirty) - len(names)
        problems.append(
            "There are uncommitted changes here (%s%s). An update would discard them, so it "
            "will not run. This looks like a development checkout rather than an installation."
            % (', '.join(names), ' and %d more' % more if more > 0 else ''))

    # Commits the remote has never seen are the other mark of a working checkout. `@{upstream}`
    # is absent on a detached HEAD, which is the normal state of an *installed* deployment --
    # so its absence is not itself a problem, only unpushed work is.
    ahead = _git(base_dir, 'rev-list', '--count', '@{upstream}..HEAD', check=False).strip()
    if ahead.isdigit() and int(ahead) > 0:
        problems.append(
            "This checkout has %s commit(s) that the remote does not, which an update would "
            "leave unreachable." % ahead)
    return problems


# ── asking the remote ────────────────────────────────────────────────────────────────────


def _version_key(tag):
    """Sortable key for a `v1.2.3` tag. Missing parts sort low, so v1.2 < v1.2.1."""
    return tuple(int(part) for part in tag[1:].split('.'))


def remote_tags(base_dir):
    """Every release tag on the remote, highest last.

    `refs/tags/*^{}` entries are the dereferenced targets of annotated tags; both forms are
    reduced to the tag name here, since what is wanted is the name to check out.
    """
    output = _git(base_dir, 'ls-remote', '--tags', 'origin', 'v*')
    names = set()
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) != 2:
            continue
        name = parts[1].strip()
        if name.startswith('refs/tags/'):
            name = name[len('refs/tags/'):]
        if name.endswith('^{}'):
            name = name[:-3]
        if TAG_RE.match(name):
            names.add(name)
    return sorted(names, key=_version_key)


def remote_main(base_dir):
    """The SHA at the tip of the development branch, or None if the remote has no such branch."""
    output = _git(base_dir, 'ls-remote', 'origin', 'refs/heads/%s' % MAIN_BRANCH)
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) == 2:
            return parts[0].strip()
    return None


#: A submodule entry in `git ls-tree`. Gitlinks are mode 160000, which is the only thing
#: distinguishing "a component pinned at a commit" from an ordinary directory.
_GITLINK_MODE = '160000'


def _tree_components(base_dir, ref):
    """`{name: sha}` for the components an assembled project pins at `ref`.

    Empty for standalone mutint-core, which has no submodules -- and for anything else this
    cannot read, since it is asked only to describe an update and never to permit one.
    """
    output = _git(base_dir, 'ls-tree', ref, check=False)
    found = {}
    for line in output.splitlines():
        head, _, name = line.partition('\t')
        fields = head.split()
        if len(fields) == 3 and fields[0] == _GITLINK_MODE:
            found[name.strip()] = fields[2]
    return found


def component_changes(base_dir, target):
    """Which components installing `target` would move, as a list of dicts.

    **This is the half of an update nobody could see.** A version is a commit of the
    assembled project, and what that commit *contains* is a pinned SHA per component -- so
    `apply`'s `submodule update --init --recursive` moves the plugins along with it, and
    "main is available" said nothing about which. Each entry is `{name, from, to, change}`,
    where `change` is `moved`, `added` or `removed`.
    """
    here = _tree_components(base_dir, 'HEAD')
    there = _tree_components(base_dir, target)

    changes = []
    for name in sorted(set(here) | set(there)):
        before, after = here.get(name), there.get(name)
        if before == after:
            continue
        changes.append({
            'name': name,
            'from': before[:8] if before else None,
            'to': after[:8] if after else None,
            'change': 'moved' if before and after else ('added' if after else 'removed'),
        })
    return changes


def describe(base_dir, ref, kind):
    """What installing `ref` would actually do, as far as the remote will say.

    **Best effort by design.** Everything here needs the target commit's own objects, so it
    fetches -- and a fetch that fails must leave the answer poorer rather than turn "there is
    a new version" into an error. Every field can therefore be absent, and `check` keeps the
    ref and the SHA it already had either way.

    `git ls-remote` alone can name the tip and nothing else: not its date, not how far ahead
    it is, and not which components it pins. That is why this is a second network call rather
    than more parsing of the first.
    """
    described = {}
    try:
        _git(base_dir, 'fetch', '--tags', '--quiet', 'origin')
        # A branch is read through its remote-tracking ref for the reason `apply` checks one
        # out that way: a local `main` may not exist, and creating one here would be a lie
        # about what this checkout is on.
        target = 'origin/%s' % ref if kind == 'branch' else ref
        # `^{commit}` throughout, because a release tag is **annotated**: `rev-parse v0.0.1`
        # answers the tag object's own SHA and `show -s --format=%cI` prints its header, so
        # the page said "commit <tag object>, dated tag v0.0.1". Peeling asks about the commit
        # the tag points at, which is what an update actually moves to.
        target = target + '^{commit}'
        described['sha'] = _git(base_dir, 'rev-parse', '--short=8', target).strip() or None
        described['date'] = _git(
            base_dir, 'show', '-s', '--format=%cI', target).strip() or None
        ahead = _git(base_dir, 'rev-list', '--count', 'HEAD..%s' % target,
                     check=False).strip()
        described['commits'] = int(ahead) if ahead.isdigit() else None
        described['components'] = component_changes(base_dir, target)
        described['component_total'] = len(_tree_components(base_dir, target))
    except (UpdateError, ValueError, OSError):
        # Recorded as nothing rather than as a failure: the version is still available and
        # still installable, and this only ever added detail to saying so.
        pass
    return described


def readable_time(iso):
    """`2026-09-07T11:53:23-04:00` as `2026-09-07 11:53`, in the reader's own time zone.

    Public because the component table on the same page formats each installed revision's
    date with it, and `about_registry` formats /about's with it too. This module imports
    nothing from `mutint_common`, so anything there may import this without a cycle.

    The whole timestamp rather than the date: "main" moves several times a day on a project
    being worked on, so a date alone cannot tell you whether what is offered is the commit you
    just pushed.

    **Converted to local time, and the offset then dropped.** `%cI` carries the *committer's*
    offset, which is a fact about where they were sitting rather than about when the commit
    happened; printed raw it makes two commits an hour apart look simultaneous, and printed
    with the offset showing it asks the reader to do the arithmetic. Converting answers the
    question the column is actually asked -- how old is this -- against the clock on the wall
    behind the screen. `astimezone()` with no argument reads the host's zone, which is the
    server's, and for the single-machine installation this page updates that is the reader's.

    Anything not of that shape is returned as it came -- `%cI` is strict ISO 8601, and a
    version of git that answered otherwise should show its answer rather than a mangling.
    The shape is checked *before* parsing rather than left to `fromisoformat`, which accepts
    a bare `2026-09-07` and answers midnight: a time this commit was not made at, printed
    with the same confidence as one it was.
    """
    import datetime

    if not (len(iso or '') >= 19 and iso[10:11] == 'T' and iso[13:14] == ':'):
        return iso
    try:
        return datetime.datetime.fromisoformat(iso).astimezone().strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return iso


def summarize(ref, current, described):
    """The sentence the page shows. Composed here so the page and its poll cannot word it
    differently, and plain text so neither has to escape it."""
    where = "%s is available" % ref
    detail = []
    if described.get('sha'):
        detail.append("commit %s" % described['sha'])
    if described.get('date'):
        detail.append("committed %s" % readable_time(described['date']))
    commits = described.get('commits')
    if commits:
        detail.append("%d commit%s newer than %s"
                      % (commits, "" if commits == 1 else "s", current or "what you have"))
    first = "%s: %s." % (where, ", ".join(detail)) if detail else "%s." % where

    if 'components' not in described:
        return first

    changes = described['components']
    if not changes:
        total = described.get('component_total') or 0
        return first + (" No component changes: every one of the %d stays where it is."
                        % total if total else " No component changes.")

    moved = ", ".join(
        "%s (%s)" % (entry['name'],
                     "%s to %s" % (entry['from'], entry['to']) if entry['change'] == 'moved'
                     else entry['change'])
        for entry in changes)
    total = described.get('component_total') or len(changes)
    return "%s It also moves %d of %d components: %s." % (first, len(changes), total, moved)


def _available(base_dir, ref, kind, sha):
    """The `available` record: what it is, and what installing it would do.

    The ref and the SHA come from `ls-remote` and are always known; everything `describe`
    adds needs the commit itself and may be missing. `summary` is composed here so the page
    and its poll say the same sentence.
    """
    described = describe(base_dir, ref, kind)
    record = {'ref': ref, 'kind': kind, 'sha': described.get('sha') or sha}
    record.update(described)
    record['sha'] = record.get('sha') or sha
    record['summary'] = summarize(ref, current_ref(base_dir), described)
    return record


def check(base_dir, channel=None):
    """Ask the remote what is available. Returns the new state; never raises.

    Failure is recorded as a sentence in the state rather than thrown, because this is called
    from a web request and from a management command and neither wants a traceback for a
    network that was busy. The distinction between "could not ask" and "nothing newer" is kept
    all the way through -- see `Unreachable`.
    """
    state = read_state(base_dir)
    if channel is not None:
        state['channel'] = channel
    channel = state.get('channel', STABLE)
    state['checked_at'] = _now()
    state.pop('error', None)

    problems = blockers(base_dir)
    if problems:
        state['error'] = ' '.join(problems)
        state['available'] = None
        write_state(base_dir, state)
        return state

    try:
        if channel == MAIN:
            sha = remote_main(base_dir)
            if sha is None:
                raise Unreachable("The remote has no `%s` branch." % MAIN_BRANCH)
            local = _git(base_dir, 'rev-parse', 'HEAD').strip()
            newer = sha != local
            state['available'] = _available(
                base_dir, MAIN_BRANCH, 'branch', sha[:8]) if newer else None
        else:
            tags = remote_tags(base_dir)
            if not tags:
                state['available'] = None
                state['error'] = (
                    "The remote has no release tags yet, so there is nothing to update to. "
                    "The development channel follows `%s` instead." % MAIN_BRANCH)
                write_state(base_dir, state)
                return state
            latest = tags[-1]
            here = current_ref(base_dir)
            newer = here != latest and (
                not (here or '').startswith('v')
                or _version_key(latest) > _version_key(here))
            state['available'] = _available(
                base_dir, latest, 'tag', None) if newer else None
    except UpdateError as exc:
        state['error'] = str(exc)
        state['available'] = None

    write_state(base_dir, state)
    return state


# ── applying ─────────────────────────────────────────────────────────────────────────────


def request(base_dir, ref, by=None):
    """Stage `ref` to be applied by the next launch."""
    state = read_state(base_dir)
    state['requested'] = {'ref': ref, 'at': _now(), 'by': by}
    write_state(base_dir, state)
    return state


def clear_request(base_dir):
    state = read_state(base_dir)
    state.pop('requested', None)
    write_state(base_dir, state)
    return state


def void_verdict(base_dir):
    """Forget what the last check found, because this checkout has moved.

    `available` is not a fact about the remote, it is a **comparison**: this ref is newer than
    the one we are on. Move the checkout and the right-hand side of it has changed, so the
    verdict is void whichever ref was installed -- and left in place it is worse than stale,
    because `/update/` renders the Install button from it and would go on offering a version
    this installation now *is*, past the update and past every reload after it.

    `checked_at` goes with it rather than being kept, which is the part that looks like
    over-deletion and is not. The page falls back to *Up to date as of <time>* when there is a
    timestamp and no `available`, and that sentence after an update is a claim nobody made:
    the check it names ran against the previous version. **Not checked yet** is simply true
    here, and this module refuses the reassuring-but-unverified answer everywhere else --
    `Unreachable` exists for the same reason.

    A stored `error` is about that same check, so it goes too. Blockers are recomputed by the
    view on every render and come back on their own.
    """
    state = read_state(base_dir)
    for key in ('available', 'checked_at', 'error'):
        state.pop(key, None)
    write_state(base_dir, state)
    return state


def note_restart(base_dir):
    """Record that the next launch is a restart somebody asked for from the web page.

    `start.py` opens a browser at the site root on every launch, which is right for somebody
    who double-clicked an icon and wrong for this: the person is already looking at MutInt in a
    browser, and that window is polling for the server to come back. Opening another navigates
    them away from the page they pressed the button on.

    Kept in the state file rather than a marker of its own -- `requested` and `last_result`
    already live there, and this is the same kind of thing: a note from one launch to the next.
    """
    state = read_state(base_dir)
    state['restarting'] = True
    write_state(base_dir, state)
    return state


def take_restart_note(base_dir):
    """Whether this launch is that restart -- once. Reading it clears it.

    Cleared on read because it describes one launch. A note left behind by a restart that never
    happened costs exactly one browser window that does not open, on a launch where the Dock
    icon is there and clicking it opens one.
    """
    state = read_state(base_dir)
    if not state.pop('restarting', False):
        return False
    write_state(base_dir, state)
    return True


def _prune_backups(directory, keep=BACKUP_KEEP):
    """Delete all but the newest `keep` dumps in `directory`. Returns what it removed.

    **By modification time, not by the timestamp in the name.** The name carries the ref as
    well, so sorting by it runs `pre-main-*` and `pre-v0.0.2-*` as separate sequences and
    would keep `keep` of each -- which is the bug this would most plausibly have.

    Best-effort, like the dump it follows: a file that will not delete is not a reason to
    fail an update that has otherwise worked. Only files matching `_BACKUP_NAME` are
    candidates, so a dump an operator took by hand and named themselves survives.
    """
    try:
        names = [name for name in os.listdir(directory) if _BACKUP_NAME.match(name)]
    except OSError:
        return []

    dated = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            dated.append((os.path.getmtime(path), name, path))
        except OSError:                  # removed underneath us; nothing to keep or drop
            continue

    removed = []
    for _, _, path in sorted(dated, reverse=True)[keep:]:
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def backup(base_dir, pg, label):
    """`pg_dump` this checkout's database, returning the path, or None if there is nothing
    to dump.

    Best-effort by design: an update that refused to proceed because a backup failed would
    strand somebody on an old version for a reason that is not about the update. The path is
    recorded in the state either way, so a reader can see whether there is one.

    **The database only.** `data/store` -- the reads, the BAMs, the coverage BigWigs, breseq's
    reports -- is not in here and is not what this protects: it is two orders of magnitude
    larger, and an update does not touch it, so the rows this restores still point at files
    that are still there.

    Older dumps are pruned once this one lands, so the directory keeps `BACKUP_KEEP` and not
    a copy of the database per update. Pruning after the new dump rather than before it is
    deliberate: a prune that ran first would drop the oldest to make room for a dump that
    then failed.
    """
    if pg.is_external():
        return None                      # somebody else's server; their backups
    cluster = pg.Cluster(base_dir)
    dump = cluster.bin('pg_dump')
    if not os.path.isfile(dump) or not cluster.is_running():
        return None
    directory = os.path.join(base_dir, BACKUP_DIR)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', label)
    path = os.path.join(directory, 'pre-%s-%s.sql' % (safe, _stamp()))
    with open(path, 'wb') as handle:
        completed = subprocess.run(
            [dump, '-h', cluster.socket_dir, '-U', pg.USER, '-d', cluster.name],
            stdout=handle, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    _prune_backups(directory)
    return path


def apply(base_dir, ref, pg=None, take_backup=True):
    """Move this checkout onto `ref`. Raises UpdateError with a sentence if it will not.

    Deliberately does *not* install dependencies or migrate. The entry script's sentinels
    already rebuild the venv and the tools when a component's requirements change, and
    `start.py` already migrates -- so the whole of an update downstream of here is machinery
    that predates it.
    """
    problems = blockers(base_dir)
    if problems:
        raise UpdateError(' '.join(problems))

    saved = None
    if take_backup and pg is not None:
        saved = backup(base_dir, pg, ref)

    _git(base_dir, 'fetch', '--tags', 'origin')
    # A branch has to be resolved through its remote-tracking ref: `git checkout main` in a
    # deployment that has never had a local `main` would create one, and pin it here forever.
    target = 'origin/%s' % ref if ref == MAIN_BRANCH else ref
    _git(base_dir, 'checkout', '--detach', target)
    _git(base_dir, 'submodule', 'sync', '--recursive')
    _git(base_dir, 'submodule', 'update', '--init', '--recursive')
    return {'ref': ref, 'at': _now(), 'ok': True, 'backup': saved,
            'now': current_ref(base_dir)}


def adopt(base_dir, url, ref):
    """Turn a tree that has no `.git` into a real checkout of `ref`, in place.

    For anyone who unpacked GitHub's source archive, or copied an installation without its
    history. The tracked files at a tag are exactly what such a tree holds, so the reset is a
    no-op on content and the ignored `data/` and `env/` are untouched -- but that is a claim
    worth checking rather than asserting, which is what the status probe at the end does.
    """
    if is_git_checkout(base_dir):
        raise UpdateError("This is already a git checkout; there is nothing to adopt.")
    _git(base_dir, 'init', '-q')
    _git(base_dir, 'remote', 'add', 'origin', url)
    _git(base_dir, 'fetch', '--tags', 'origin')

    # A **mixed** reset: it moves HEAD and the index to `ref` and does not write a single file.
    # `checkout` cannot be used here and neither can `checkout --force`. Plain checkout refuses
    # -- every file of the tree is untracked and would be "overwritten", which is precisely the
    # state being adopted -- and forcing it would overwrite them, which makes the check below
    # vacuous: any difference would have been destroyed before it could be reported.
    #
    # After this, `git status` is exactly "how does this tree differ from `ref`", asked of the
    # files already on disk. An empty answer means the tree *is* the release and adoption cost
    # nothing; a non-empty one means this is something else, and it is reported with every file
    # still where it was.
    _git(base_dir, 'reset', '-q', ref)
    dirty = changed_tracked_files(base_dir)
    if dirty:
        names = ", ".join(line[3:] for line in dirty[:5])
        more = len(dirty) - min(len(dirty), 5)
        raise UpdateError(
            "This tree does not match %s -- %d file(s) differ (%s%s). Nothing has been "
            "changed and no files were touched; the git history added here can be removed by "
            "deleting the .git directory. Adopt the release this actually is, or update from "
            "a fresh install."
            % (ref, len(dirty), names, " and %d more" % more if more else ""))

    # Clean, so this is a no-op on the files and only detaches HEAD -- which is where an
    # installation belongs, pinned to a release rather than following a branch.
    _git(base_dir, 'checkout', '--detach', ref)
    _git(base_dir, 'submodule', 'sync', '--recursive')
    _git(base_dir, 'submodule', 'update', '--init', '--recursive')
    return {'ref': ref, 'at': _now(), 'ok': True, 'now': current_ref(base_dir)}


def apply_staged(base_dir, pg=None):
    """Apply a staged request if there is one. Returns the result, or None.

    Called by the entry script on `start`, before anything reads a requirements.txt. Every
    failure is recorded in the state and swallowed: a launch that refuses to start because an
    update did not work leaves somebody with no MutInt at all, which is strictly worse than
    an old one plus a message.
    """
    state = read_state(base_dir)
    pending = state.get('requested')
    if not pending or not pending.get('ref'):
        return None
    ref = pending['ref']
    print("Applying staged update to %s..." % ref)
    try:
        result = apply(base_dir, ref, pg=pg)
    except UpdateError as exc:
        result = {'ref': ref, 'at': _now(), 'ok': False, 'detail': str(exc)}
        print("Update to %s did not run: %s" % (ref, exc))
    else:
        print("Now on %s." % (result.get('now') or ref))
        # We are on something else now, so what the last check found is void -- see
        # `void_verdict`. Only on success: a failed update leaves this checkout where it was,
        # and the version it was offered is still genuinely on offer.
        void_verdict(base_dir)

    # Re-read: `apply` may have replaced the working tree, and the state file lives in
    # `data/`, which git leaves alone -- but the request must be cleared whatever happened,
    # or every launch would retry a failing update for ever.
    state = read_state(base_dir)
    state.pop('requested', None)
    state['last_result'] = result
    write_state(base_dir, state)
    return result


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _stamp():
    import datetime
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
