"""Refusing to migrate a database this version cannot migrate.

**A migration becomes immutable the moment it is pushed to `main`**, because the update
path's Development channel follows that branch -- so a migration pushed there is applied by
real installations within a day. Before there was an update path the rule was about tags and
the history here was thrown away and regenerated twice.
`docs/contributing/releasing.md` is the rule and the procedure; this module is what stops the
rule being merely written down.

The state a rewrite leaves behind is exact: `django_migrations` holds rows naming files that
no longer exist. Django does not treat that as an error -- the graph loads, `migrate` finds
its own initial migration unapplied, and tries to `CREATE TABLE` over tables that are already
there. What the operator sees is a `ProgrammingError` about a relation existing, several steps
downstream of the thing that actually went wrong.

Two things make that worth catching here rather than trusting `migrate` to explain:

- `start.py` runs `migrate --run-syncdb` **unattended on every launch**, so nobody is standing
  by expecting a schema change when it happens; and
- `--run-syncdb` creates tables for any installed app with *no* migrations at all, so an app
  whose `migrations/` directory vanished gets its tables made with no `django_migrations` rows
  behind them, and the database looks fine until the next real migration lands.

**Restricted to first-party apps.** A third-party package legitimately renumbers its own
history between releases and its update path is pip's, not ours; naming `django_tasks_db`
here would produce an alarming message about something the operator cannot act on and did not
do.
"""

import logging

logger = logging.getLogger(__name__)


def missing_migrations(connection):
    """`[(app_label, name), ...]` the database has applied and this checkout no longer ships.

    Sorted, and restricted to apps belonging to a component of this deployment. An empty list
    is the ordinary answer, including on a database that has never been migrated.
    """
    from django.db.migrations.loader import MigrationLoader

    from mutint_common.about_registry import first_party_app_configs

    ours = {config.label for config in first_party_app_configs()}
    # `ignore_no_migrations` so an app with an empty `migrations/` package is not itself an
    # error here -- that is a normal state for an app with no models.
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    applied = set(loader.applied_migrations or ())
    on_disk = set(loader.disk_migrations or ())
    return sorted(
        (label, name) for (label, name) in applied - on_disk if label in ours)


def describe(missing):
    """The sentence to show an operator, or None when there is nothing wrong."""
    if not missing:
        return None
    named = ", ".join("%s.%s" % pair for pair in missing[:6])
    if len(missing) > 6:
        named += " and %d more" % (len(missing) - 6)
    return (
        "This database has applied migrations that this version of MutInt no longer ships: "
        "%s.\n\n"
        "That happens when migration history is rewritten, which MutInt does not do once a "
        "migration has been published -- on any channel. Migrating now would try to create "
        "tables that already exist, so it has been stopped before anything ran; nothing has "
        "been changed.\n\n"
        "If you updated, the version you came from is the one that can still read this "
        "database: `./mutint update --to <that tag>` goes back, and any pre-update dump is "
        "in data/backups/. If this is a development checkout, `./mutint db reset --yes` "
        "starts again from empty -- move data/store aside first, since its paths are keyed by "
        "primary keys that restart at 1."
        % named)


def check(connection):
    """The message to refuse with, or None.

    Never raises: it is called on the launch path, and a guard that can itself fail is a way
    of not starting that nobody asked for. A guard that cannot answer says nothing, which
    leaves `migrate` exactly as it was before this module existed.
    """
    try:
        return describe(missing_migrations(connection))
    except Exception:
        logger.warning("Could not check the migration history", exc_info=True)
        return None
