"""Registry of derived data and the rebuilds that produce it.

The seventh registry, alongside the plugin, nav, about, import, context and example ones --
and the first that runs in *both* directions. The others let an app contribute something to
core (a nav entry, a URL, an import type). This one also lets an app tell core that something
it contributed has changed, so that core's own derived data can catch up.

Some things in ALEdb are too expensive to compute per request. `aledb_dashboard`'s count
tables are a table that is a function of the mutations, rebuilt when they change. What was
missing was any way to say that they *have* changed, other than from the two hardcoded call
sites inside `aledb_import`.

**The list is much shorter than it was, and the direction is worth knowing before adding to
it.** `aledb_stats.StaticData` (the needle plot), `aledb_stats.ExperimentSummary` (the
Overview's counts), `aledb_converge.ConvergeMutation` and `aledb_fixation.FixatedMutation` were
all on it, and each turned out to cost less to answer than to keep correct once it stopped
materialising rows to do it -- 0.05s, 0.07s and 0.17s respectively on the largest experiment in
the dev database, where the row-instantiating versions were 3.38s and 6.25s. (Fixation's cost
is unmeasured: no local data exercises it, since an ALE needs more than one flask to fix
anything. It was rewritten on the shape of the old code, not on a number.)

What a stored answer costs beyond disk is a rebuilder to register, a staleness row marked by
every edit, an `ensure_fresh` on the read path, and -- where it stores ids -- integers that
must keep meaning the same thing across deletes and re-imports. Register derived data when the
computation is genuinely expensive; a query that reads three columns as tuples usually is not.

The dashboard's totals stay because they are genuinely installation-wide, and they stay
*correct* because the dashboard applies no filter -- see `aledb_dashboard.util`. Anything the
per-user filter reaches could not be stored in any case: a shared table cannot be keyed by
user.

So a rebuild has two halves, and they are deliberately separate calls:

    request_rebuild(experiment_id)      "this is stale now"     -- cheap, always safe
    run_rebuilds(experiment_id)         "so recompute it"       -- expensive

Marking is one UPDATE and can be done from anywhere, including from a request that changes
something about every experiment at once -- a global filter edit is exactly that, and is why
this is not simply a synchronous hook. Running is a whole-experiment recomputation and is done
either eagerly, where a caller is already paying for a long operation and wants the result warm
(the import path), or lazily by the page that reads the data (`ensure_fresh`), or in bulk from
`./aledb rebuild`.

Registering looks like every other registry -- from `AppConfig.ready()`, with no edit to core:

    from aledb_common.rebuild_registry import register_rebuilder
    register_rebuilder('fixation', rebuild_fixated_mutations)

**Order is explicit here, as in `import_registry` and unlike everywhere else.** A nav entry's
position is cosmetic and is settled by moving its app in INSTALLED_APPS; a rebuild's is
correctness. The experiment filter's defaults have to exist before anything that filters
mutations through them, and the dashboard's installation-wide totals are computed from every
experiment and so have to come last -- but `aledb_dashboard` is the second app in the
`aledb_*` block, because that is where its *nav entry* belongs. INSTALLED_APPS order cannot
express both, so `priority` does, and the constants below are the vocabulary.

**Failures are isolated, which the post-experiment hook it replaces did not do.**
`run_post_experiment_hooks` was a bare loop: one plugin raising aborted the remaining hooks and
propagated out of the import, 500ing a request whose mutations were already committed. Here each
rebuilder runs in its own try/except, the exception is logged, the row *stays* marked stale, and
the message is recorded in `last_error`. That is the same posture `run_sequence_rename_hooks`
takes and for the same reason: derived data that is stale is recoverable -- `./aledb rebuild`
recomputes it, and the read path retries on its own -- where a failed import is not.

The trade is real and worth stating: a broken plugin rebuild is now quiet rather than loud.
`./aledb rebuild --list` is where it shows up, and it is why `last_error` is a column rather
than only a log line.
"""

import logging

logger = logging.getLogger(__name__)

# fn(experiment_id) -- derived data belonging to one experiment.
EXPERIMENT_SCOPE = 'experiment'
# fn() -- derived data covering the whole installation, e.g. the dashboard's totals.
SITE_SCOPE = 'site'

_SCOPES = (EXPERIMENT_SCOPE, SITE_SCOPE)

# `INPUT_MUTATIONS` and `INPUT_FILTERS` stood here, with `register_rebuilder(inputs=)` and
# `request_rebuild(changed=)`, so that a caller could say what moved and mark only the derived
# data reading it. There were two values because the vocabulary's own rule said so: an input
# nothing passes as `changed=` is a word with no meaning behind it.
#
# It had exactly one producer -- the page that edited an experiment's shared filter row -- and
# that page is gone. Filtering belongs to the reader now and lives in their session, so there is
# no shared filter for anything to be invalidated by, and `INPUT_FILTERS` had nobody left to
# pass it. With one value remaining, `inputs=` could only ever have said "everything", which is
# the default, so the whole mechanism went by its own rule rather than sitting unused waiting
# for a second input to justify it.
#
# What it bought was worth having at the time: `aledb_phylogeny` reads ObservedMutation directly
# and declared itself independent of filters so that a cutoff edit would not hide its tree behind
# a warning asking for a rebuild that redrew the identical topology. Nothing can edit a cutoff
# for anyone but themselves now, so the false alarm cannot happen either.

# Settings other rebuilds read. The per-experiment filter defaults come first because
# everything counting mutations counts them through it.
PRIORITY_SETTINGS = 10
# Derived data computed from an experiment's mutations. The default, and where a plugin
# belongs unless it has a reason not to.
PRIORITY_DERIVED = 50
# Totals aggregated across every experiment, so computed once the rest are current.
PRIORITY_AGGREGATE = 90

# [{'name', 'fn', 'scope', 'label', 'priority', 'index', 'auto'}, ...]
_rebuilders = []


def register_rebuilder(name, fn, scope=EXPERIMENT_SCOPE, label=None,
                       priority=PRIORITY_DERIVED, auto=True):
    """Register a named rebuild (called from AppConfig.ready()).

    Rebuilds run in registration order: apps in INSTALLED_APPS order, and within an app in the
    order register_rebuilder() is called. There is deliberately no ordering parameter, as with
    nav_registry -- to move a rebuild, move its app in INSTALLED_APPS. Order matters here in a
    way it does not for nav entries: `aledb_stats`'s summary is computed from the mutations, so
    anything that *changes* the mutations has to be registered ahead of it.

    name   stable identifier, e.g. 'fixation'. It is what `only=` and `./aledb rebuild --only`
           name, and what a DerivedDataState row is keyed by, so changing it orphans that row
           and the data reads as never built.
    fn     callable(experiment_id) for EXPERIMENT_SCOPE, callable() for SITE_SCOPE.
    scope  EXPERIMENT_SCOPE or SITE_SCOPE.
    label  human-readable name for `./aledb rebuild --list`; defaults to name.
    priority  lower runs first; see the constants above. Ties break on registration order,
           which is INSTALLED_APPS order, so leaving it alone gives the obvious behaviour.
    auto   False registers derived data that is *tracked* but never rebuilt behind anyone's
           back: `request_rebuild` marks it, `--list` shows it, and `run_rebuilds` skips it
           unless it is named in `only=`. It is what lets a plugin be told its data has gone
           stale without promising to recompute it -- the thing `aledb-phylogeny` wanted and
           could not have, so it registered nothing at all and its tree was never invalidated.

           **`force=True` does not override it.** `run_post_experiment_hooks` forces, so an
           import would otherwise build every opted-out thing there is. Force means "rebuild
           even if fresh", not "rebuild what opted out".

           The cost of opting out: a page that forgets to ask `is_stale` is now worse off than
           one that never registered, because it has a staleness record nobody reads.
    A duplicate name raises, as in import_registry and example_registry: two rebuilds sharing a
    name would share a staleness row, and each would keep marking the other fresh.
    """
    if scope not in _SCOPES:
        raise ValueError("register_rebuilder() scope must be one of %r, got %r" % (_SCOPES, scope))
    if not callable(fn):
        raise ValueError("register_rebuilder(%r) needs a callable" % (name,))
    for existing in _rebuilders:
        if existing['name'] == name:
            raise ValueError("a rebuilder named %r is already registered" % (name,))
    _rebuilders.append({
        'name': name,
        'fn': fn,
        'scope': scope,
        'label': label or name,
        'priority': priority,
        'index': len(_rebuilders),
        'auto': bool(auto),
    })
    return name


def get_rebuilders(scope=None, only=None):
    """Registered rebuilders, in the order they must run: priority, then registration.

    scope  restrict to EXPERIMENT_SCOPE or SITE_SCOPE.
    only   an iterable of names to restrict to. **An unknown name is skipped, not an error.**
           Callers name plugins they cannot know are installed -- `rebuild_after_structural_change`
           asks for 'fixation' and 'converge' -- and a deployment without a plugin must not
           raise where it would simply have nothing to do. `./aledb rebuild --only` checks the
           names itself, because there a typo should be reported rather than silently do nothing.
    """
    names = None if only is None else set(only)
    matched = [r for r in _rebuilders
               if (scope is None or r['scope'] == scope)
               and (names is None or r['name'] in names)]
    return sorted(matched, key=lambda r: (r['priority'], r['index']))


def unregister_rebuilder(name):
    """Remove a rebuilder. Returns whether there was one.

    Exists for tests, which register a hook and have to take it out again -- they used to
    `.pop()` a private list, which said nothing about *which* entry went. Production code has
    no reason to call it: an app that is installed contributes its rebuilds for the life of
    the process.
    """
    for index, rebuilder in enumerate(_rebuilders):
        if rebuilder['name'] == name:
            del _rebuilders[index]
            return True
    return False


def get_rebuilder(name):
    for rebuilder in _rebuilders:
        if rebuilder['name'] == name:
            return rebuilder
    return None


def request_rebuild(experiment_id=None, only=None, reason=''):
    """Mark derived data stale. Cheap, and safe to call from any request.

    experiment_id  the experiment whose data changed, or None meaning "every experiment".
    only           names to narrow to; everything registered by default. Use it to say what
                   actually changed: a sample renumber changes fixation and the sample counts
                   and nothing about a mutation count, and `rebuild_after_structural_change`
                   has always refused to rebuild the dashboard for exactly that reason.
    reason         logged, not stored. It is for reading the log after the fact.

    A `changed=` argument narrowed by what a rebuild *reads* rather than by name. See the note
    beside the scope constants for why it and its vocabulary are gone.

    Site-scoped rebuilds are marked too, because they aggregate across experiments -- one
    experiment changing does make the installation-wide totals stale.

    Marking every experiment is one UPDATE rather than a row per experiment: a row that does
    not exist already counts as stale (see `is_stale`), so there is nothing to insert.
    """
    from django.utils import timezone

    from aledb_common.models import DerivedDataState

    now = timezone.now()
    rebuilders = get_rebuilders(only=only)
    if not rebuilders:
        return

    for rebuilder in rebuilders:
        if rebuilder['scope'] == SITE_SCOPE or experiment_id is None:
            # Everything under this name, however many experiments that is.
            query = DerivedDataState.objects.filter(name=rebuilder['name'])
            if rebuilder['scope'] == SITE_SCOPE:
                query = query.filter(experiment=None)
            query.update(stale_since=now)
        else:
            DerivedDataState.objects.update_or_create(
                name=rebuilder['name'], experiment_id=experiment_id,
                defaults={'stale_since': now})

    logger.info("rebuild requested for %s (%s)%s",
                "every experiment" if experiment_id is None else "experiment %s" % experiment_id,
                ", ".join(r['name'] for r in rebuilders),
                " -- %s" % reason if reason else "")


def is_stale(name, experiment_id=None):
    """Whether `name`'s data needs rebuilding for this experiment.

    A missing row means stale: never built is the same answer as built and invalidated, and it
    saves having to seed a row for every experiment the moment a rebuilder is registered.
    """
    from aledb_common.models import DerivedDataState

    state = DerivedDataState.objects.filter(
        name=name, experiment_id=experiment_id).first()
    return state is None or state.stale_since is not None


def ensure_fresh(name, experiment_id=None):
    """Rebuild `name` for this experiment if it is stale; do nothing if it is not.

    This is what a read path calls -- the Overview does, which is what makes the first page view
    after a change pay for the recomputation and every view after it free. Returns True if the
    data is fresh on return, False if a rebuild was needed and failed.

    A failure is logged and recorded, never raised: a page whose derived data could not be
    rebuilt should render what it has and say so, not 500.
    """
    if not is_stale(name, experiment_id):
        return True
    results = run_rebuilds(experiment_id, only=[name])
    return all(results.values()) if results else True


def run_rebuilds(experiment_id=None, only=None, force=False, scope=None):
    """Run the stale rebuilds (or all of them, with force=True). Returns {name: succeeded}.

    `scope` restricts to EXPERIMENT_SCOPE or SITE_SCOPE. It is how a caller says "recompute
    what I just changed, and leave the installation-wide totals for whoever reads them" --
    `rebuild_after_edit` does exactly that, because counting every ObservedMutation in the
    database is not a cost a single delete should pay.

    Each rebuilder is isolated: one raising neither aborts the others nor propagates. See the
    module docstring for why that differs from the `run_post_experiment_hooks` it replaces.

    Staleness is cleared with a compare-and-set against the `stale_since` read before the
    rebuild started. If something marked the data stale again *while* it was being rebuilt, the
    rebuild that just finished did not see that change, so the row stays stale and the next
    reader rebuilds again. Clearing unconditionally would lose the second change silently.
    """
    from django.utils import timezone

    from aledb_common.models import DerivedDataState

    results = {}
    named = None if only is None else set(only)
    for rebuilder in get_rebuilders(scope=scope, only=only):
        name = rebuilder['name']
        if not rebuilder['auto'] and (named is None or name not in named):
            # Opted out of running on its own. Being named in `only=` is the ask that runs it,
            # which is what `./aledb rebuild --only aledb_phylogeny` is. `force` is checked
            # below and deliberately does not reach here: it means "even if fresh", not "even
            # if you opted out", and `run_post_experiment_hooks` forces on every import.
            continue
        site_scoped = rebuilder['scope'] == SITE_SCOPE
        target = None if site_scoped else experiment_id

        if target is None and not site_scoped:
            # An experiment-scoped rebuild with no experiment named. `./aledb rebuild --all`
            # loops the experiments itself rather than asking for this, so reaching here means
            # a caller wanted the site-wide sweep and this rebuilder cannot answer it.
            continue

        state = DerivedDataState.objects.filter(name=name, experiment_id=target).first()
        was_stale_since = state.stale_since if state else None
        if not force and state is not None and state.stale_since is None:
            continue

        started = timezone.now()
        try:
            if site_scoped:
                rebuilder['fn']()
            else:
                rebuilder['fn'](target)
        except Exception as error:  # noqa: BLE001
            logger.exception("rebuild %r failed for %s", name,
                             "the site" if site_scoped else "experiment %s" % target)
            DerivedDataState.objects.update_or_create(
                name=name, experiment_id=target,
                defaults={'stale_since': was_stale_since or started,
                          'last_error': str(error)[:2000]})
            results[name] = False
            continue

        finished = timezone.now()
        fresh = {'rebuilt_at': finished, 'stale_since': None, 'last_error': ''}
        # Compare-and-set, in both shapes: only clear the staleness this run actually
        # answered. Where there was no row, "unchanged" means there is *still* no row --
        # `request_rebuild` creates one, so a row appearing during the rebuild is a change
        # this run did not see, exactly as a moved `stale_since` is.
        if state is None:
            _, claimed = DerivedDataState.objects.get_or_create(
                name=name, experiment_id=target, defaults=fresh)
        else:
            claimed = bool(DerivedDataState.objects.filter(
                pk=state.pk, stale_since=was_stale_since).update(**fresh))
        if not claimed:
            logger.info("rebuild %r for %s was invalidated again while it ran; "
                        "leaving it stale", name,
                        "the site" if site_scoped else "experiment %s" % target)
        results[name] = True
        logger.debug("rebuild %r took %.3fs", name, (finished - started).total_seconds())
    return results
