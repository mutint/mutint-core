"""Registry of the kinds of data components keep in the store, and how big each is.

The eleventh registry. Apps call `register_storage_kind()` from `AppConfig.ready()` with a
callable that measures one experiment's bytes of that kind and, optionally, one that clears
them. Core registers two -- the alignments (BAM, index and coverage) and breseq's HTML
report, both clearable -- and a plugin that keeps files under `store.component_dir` registers
its own so the dashboard's total is the whole store rather than the part core knows about.

**Why this exists.** Nothing said how much disk an experiment took, and nothing short of
`./mutint purge_deleted` removed a stored file. The BAM and breseq's `output/` tree are most
of what a sample costs, and once its mutations are in the database neither is needed for any
table -- only the genome browser and the report viewer read them. So a deployment that has
finished with an experiment's reads should be able to drop them and keep the mutations, and
the number that tells them it is worth doing has to exist first.

**Sizes are stored, not walked per render.** A report tree is thousands of files per sample
and the dashboard sums across the installation, so this is exactly the "genuinely expensive"
case `rebuild_registry`'s docstring reserves storing for. One `StorageUsage` row per
experiment per kind, rebuilt by the `storage` rebuilder like any other derived data:
`request_remeasure` marks, the import path and the Overview run. Anything that writes or
removes stored files calls `request_remeasure`, and the rows are only as honest as that
discipline -- coverage is built after the import's rebuild ran, and mutint-breseq deletes its
scratch after the import it triggered, so both remeasure afterwards.

**A kind measures the filesystem indexed by the database, never the flags and never a
listing of the store.** `Sample.bam_stored` says a BAM was stored, not that it is still there;
a `listdir` counts directories nothing points at. The registry answers "how much do this
experiment's rows own on disk"; what the store holds that no row owns -- abandoned staging,
directories left by `./mutint delete` -- is the dashboard's site-scoped rebuild, under
`UNATTRIBUTED_REBUILD`, and is deliberately not attributed to anybody.

**Clearing is a kind's own decision.** `clear=None` registers a kind that is counted and not
offered for clearing -- mutint-breseq's run directories are that, because a failed run keeps
its reads on purpose and deleting the run is the way to free them. Reference files and
`sample.gd` are not kinds at all: the mutations are the data, and there is nothing to be
"cleared" about the file every mutation was read from.

**Failures are isolated.** A kind whose `measure` raises is logged with its app and key and
counted as zero, and the rest of the experiment is still measured -- the posture every
registry here takes. The trade is the usual one: a broken plugin measure is quiet, so the
warning names it.

**Ordering is INSTALLED_APPS order**, with no `order` parameter, as with `panel_registry`:
a kind's position in a table is cosmetic.
"""

import logging
import os

logger = logging.getLogger(__name__)

#: The per-experiment rebuilder's name, registered by `mutint_common.apps`. It is what
#: `DerivedDataState` rows are keyed by and what `./mutint rebuild --only storage` names.
STORAGE_REBUILD = 'storage'
#: The site-scoped rebuilder's name, registered by `mutint_dashboard`: bytes in the store that
#: no experiment's rows own. Spelled here so `request_remeasure` and the dashboard cannot
#: disagree about it.
UNATTRIBUTED_REBUILD = 'storage_unattributed'

#: [{'app', 'key', 'label', 'measure', 'clear', 'description'}], in registration order.
_kinds = []


class UnknownStorageKind(KeyError):
    """No kind is registered under that key."""


class NotClearable(ValueError):
    """The kind is measured but registered with no `clear`."""


def register_storage_kind(app_config, key, label, measure, clear=None, description=''):
    """Register a kind of stored data (from `AppConfig.ready()`).

    app_config   the AppConfig itself, i.e. `self` at the call site, so a warning about a
                 measure that raised can name the app it came from.
    key          stable slug, unique across the installation: it is what `StorageUsage.kind`
                 holds and what a Clear button posts. Registering the same key twice replaces
                 the first, so a reload cannot double a kind.
    label        what the kind is called in a table.
    measure      callable(experiment) -> int, bytes on disk for that experiment's rows.
    clear        callable(experiment) -> None, removing the files and correcting whatever
                 rows say they exist. None registers a kind that is counted and not offered.
    description  one sentence on what stops working once it is cleared.

    Returns the key, so a test has something to unregister.
    """
    if not callable(measure):
        raise ValueError("register_storage_kind(%r) needs a callable measure" % (key,))
    if clear is not None and not callable(clear):
        raise ValueError("register_storage_kind(%r) clear must be callable or None" % (key,))
    entry = {'app': app_config.name, 'key': key, 'label': label, 'measure': measure,
             'clear': clear, 'description': description}
    for index, existing in enumerate(_kinds):
        if existing['key'] == key:
            _kinds[index] = entry
            return key
    _kinds.append(entry)
    return key


def unregister_storage_kind(key):
    """Remove a registered kind. For tests; nothing in the product unregisters."""
    global _kinds
    _kinds = [k for k in _kinds if k['key'] != key]


def get_storage_kinds():
    """Every registered kind, in registration order."""
    return list(_kinds)


def get_storage_kind(key):
    for kind in _kinds:
        if kind['key'] == key:
            return kind
    raise UnknownStorageKind(key)


def is_clearable(key):
    return get_storage_kind(key)['clear'] is not None


# --- measuring the filesystem -------------------------------------------------------------

def file_bytes(path):
    """Size of one file, or 0 when there is no such file."""
    try:
        return os.stat(path, follow_symlinks=False).st_size
    except OSError:
        return 0


def directory_bytes(path):
    """Bytes under a directory tree, or 0 when there is none.

    `os.scandir` rather than `os.walk` plus `getsize`: the entry already carries the stat, so
    a report tree of thousands of files costs one syscall per file rather than two. Symlinks
    are not followed and a file that vanishes mid-walk is skipped -- a run directory can be
    removed under a worker while the dashboard is measuring it.
    """
    total = 0
    try:
        entries = os.scandir(path)
    except OSError:
        return 0
    with entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    total += directory_bytes(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def measure_experiment(experiment):
    """`{key: bytes}` for every registered kind, a raising kind counted as 0 and logged."""
    sizes = {}
    for kind in _kinds:
        try:
            sizes[kind['key']] = int(kind['measure'](experiment) or 0)
        except Exception:  # noqa: BLE001 -- one kind's fault must not lose the others
            logger.exception("storage kind %s.%s could not be measured for experiment %s; "
                             "counting it as 0", kind['app'], kind['key'], experiment.id)
            sizes[kind['key']] = 0
    return sizes


# --- the rebuild --------------------------------------------------------------------------

def rebuild_storage(experiment_id):
    """The `storage` rebuilder: one `StorageUsage` row per registered kind.

    Whole-experiment, as every rebuild is. Rows for a kind that is no longer registered -- a
    plugin removed from the assembly -- are deleted rather than left summing into a total
    nothing can explain.
    """
    from django.db import transaction
    from django.utils import timezone

    from mutint_common.models import StorageUsage
    from mutint_experiment.models import Experiment

    experiment = Experiment.objects.get(pk=experiment_id)
    sizes = measure_experiment(experiment)
    now = timezone.now()
    with transaction.atomic():
        StorageUsage.objects.filter(experiment=experiment).exclude(
            kind__in=list(sizes)).delete()
        for key, size in sizes.items():
            StorageUsage.objects.update_or_create(
                experiment=experiment, kind=key,
                defaults={'bytes': size, 'measured_at': now})


def request_remeasure(experiment_id, reason=''):
    """Mark an experiment's sizes stale. Cheap; call it wherever stored files change.

    The one place the two rebuilder names are spelled together: a caller that writes or
    removes files says this and nothing about which rebuilds that touches. Narrowed with
    `only=`, so it deliberately does not mark the dashboard's mutation counts -- a cleared
    BAM changes no mutation. `experiment_id=None` marks every experiment.
    """
    from mutint_common.rebuild_registry import request_rebuild

    request_rebuild(experiment_id, only=(STORAGE_REBUILD, UNATTRIBUTED_REBUILD),
                    reason=reason or 'stored files changed')


def ensure_measured(experiment_id):
    """Rebuild one experiment's sizes if stale. Cannot raise; returns whether they are fresh."""
    from mutint_common.rebuild_registry import ensure_fresh

    return ensure_fresh(STORAGE_REBUILD, experiment_id)


def stale_experiment_ids(experiment_ids):
    """Which of these experiments have no current `storage` row -- never measured, or marked."""
    from mutint_common.models import DerivedDataState

    wanted = set(experiment_ids)
    fresh = set(DerivedDataState.objects.filter(
        name=STORAGE_REBUILD, experiment_id__in=wanted, stale_since__isnull=True)
        .values_list('experiment_id', flat=True))
    return wanted - fresh


# --- reading ------------------------------------------------------------------------------

def usage_for(experiment):
    """One entry per registered kind for an experiment, in registration order.

    A kind with no row yet reads as 0 bytes with no `measured_at`; whether that 0 is
    trustworthy is `DerivedDataState`'s question, asked through `ensure_measured`.
    """
    from mutint_common.models import StorageUsage

    rows = {row.kind: row for row in StorageUsage.objects.filter(experiment=experiment)}
    usage = []
    for kind in _kinds:
        row = rows.get(kind['key'])
        usage.append({
            'key': kind['key'],
            'label': kind['label'],
            'description': kind['description'],
            'clearable': kind['clear'] is not None,
            'bytes': row.bytes if row else 0,
            'measured_at': row.measured_at if row else None,
        })
    return usage


def bytes_by_experiment(experiment_ids):
    """`{experiment_id: total bytes}` over every registered kind, in one query.

    Rows for a kind that is no longer registered are left out, the way `rebuild_storage`
    would delete them, so a list page and a rebuilt Overview agree.
    """
    from django.db.models import Sum

    from mutint_common.models import StorageUsage

    keys = [k['key'] for k in _kinds]
    totals = (StorageUsage.objects
              .filter(experiment_id__in=list(experiment_ids), kind__in=keys)
              .values('experiment_id').annotate(total=Sum('bytes')))
    return {row['experiment_id']: row['total'] or 0 for row in totals}


def bytes_by_kind(experiment_queryset):
    """`[(key, label, bytes)]` per registered kind, summed across the experiments given."""
    from django.db.models import Sum

    from mutint_common.models import StorageUsage

    sums = dict(StorageUsage.objects
                .filter(experiment__in=experiment_queryset)
                .values('kind').annotate(total=Sum('bytes'))
                .values_list('kind', 'total'))
    return [(k['key'], k['label'], sums.get(k['key']) or 0) for k in _kinds]


def total_bytes(experiment_queryset):
    return sum(size for _, _, size in bytes_by_kind(experiment_queryset))


# --- clearing -----------------------------------------------------------------------------

def clear_kind(experiment, key):
    """Remove one kind's files for one experiment. Returns the bytes freed.

    Raises `UnknownStorageKind` or `NotClearable` before touching anything. Remeasures
    eagerly afterwards rather than only marking: the person who pressed the button is looking
    at the number. Permission and the experiment lock are the caller's to check -- a
    management command clears a locked experiment the way `./mutint import` writes to one.
    """
    from mutint_common.rebuild_registry import run_rebuilds

    kind = get_storage_kind(key)
    if kind['clear'] is None:
        raise NotClearable(key)
    # Measured first, so "freed" is the difference between two honest numbers rather than
    # between a row written before the last import and one written now.
    ensure_measured(experiment.id)
    before = usage_for(experiment)
    before_bytes = next((u['bytes'] for u in before if u['key'] == key), 0)
    kind['clear'](experiment)
    request_remeasure(experiment.id, reason='%s cleared' % key)
    run_rebuilds(experiment.id, only=[STORAGE_REBUILD])
    after = usage_for(experiment)
    after_bytes = next((u['bytes'] for u in after if u['key'] == key), 0)
    return max(before_bytes - after_bytes, 0)
