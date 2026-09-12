"""State for the rebuild registry -- what derived data is stale, and why.

This was `mutint_common`'s only model, and the app had none before it. It lives here rather than
in `mutint_stats` beside the data it most often describes because it is not about statistics: it
tracks staleness for every registered rebuild, plugins included, and a plugin's state has no
business living inside the stats app. The shared layer is where a table shared by every
component belongs.

See `mutint_common/rebuild_registry.py` for how rows here are written and read.
"""

from django.db import models
from django.db.models import Q, UniqueConstraint


class DerivedDataState(models.Model):
    """Whether one named rebuild's output is current, for one experiment or for the site.

    **A missing row means stale.** Never built and built-then-invalidated are the same question
    to every caller -- "does this need recomputing" -- and answering them the same way means a
    newly registered rebuilder needs no backfill and a new experiment needs no seeding.

    `experiment` is NULL for a SITE_SCOPE rebuild, and that is why there are two constraints
    below rather than one `unique_together`. In SQL a NULL is not equal to another NULL, so a
    unique index over (name, experiment) does not constrain the site-scoped rows at all --
    it would happily hold a dozen rows saying different things about the same rebuild, and
    whichever `is_stale` read first would win. The partial index says the thing the pair cannot.
    """

    name = models.CharField(max_length=100, db_index=True,
                            help_text="the name the rebuilder is registered under")
    experiment = models.ForeignKey("mutint_experiment.Experiment", null=True, blank=True,
                                       on_delete=models.CASCADE,
                                       help_text="NULL for a site-scoped rebuild")
    stale_since = models.DateTimeField(null=True, blank=True, db_index=True,
                                       help_text="NULL means current")
    rebuilt_at = models.DateTimeField(null=True, blank=True)
    # Kept as a column and not only a log line: an isolated failure is quiet by design, so
    # `./mutint rebuild --list` needs somewhere to read it back from.
    last_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            UniqueConstraint(fields=["name", "experiment"],
                             name="one_state_per_rebuild_per_experiment"),
            UniqueConstraint(fields=["name"], condition=Q(experiment__isnull=True),
                             name="one_state_per_site_scoped_rebuild"),
        ]
        verbose_name = "derived data state"
        verbose_name_plural = "derived data states"

    def __str__(self):
        where = "site" if self.experiment_id is None else "experiment %s" % self.experiment_id
        return "%s (%s): %s" % (self.name, where, "stale" if self.stale_since else "current")


class UserPreference(models.Model):
    """One remembered choice of one person: a key, and a JSON value.

    The second model here, and the first that is about a *person* rather than the data. It
    exists so a page can remember how somebody likes to look at things -- which columns of the
    mutation table they hide, which samples they have turned off in an experiment -- across
    experiments and across browsers, which the reader's view filter (session-scoped, and
    deliberately so) cannot do and localStorage (one browser) cannot either.

    It is deliberately a key/value store and not a column per preference: a plugin that wants
    to remember something writes under its own key and needs no migration in core. Keys are
    dotted names, `mutation_matrix.columns`, and the value is whatever JSON the owner of the key
    understands. See `mutint_common/preferences.py` for the rules on both, and for the endpoint
    a page saves through.
    """

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="preferences")
    key = models.CharField(max_length=100)
    value = models.JSONField()

    class Meta:
        constraints = [
            UniqueConstraint(fields=["user", "key"], name="one_preference_per_user_per_key"),
        ]
        verbose_name = "user preference"
        verbose_name_plural = "user preferences"

    def __str__(self):
        return "%s: %s" % (self.user_id, self.key)


class StorageUsage(models.Model):
    """Bytes one experiment's rows own on disk, for one registered kind of stored data.

    The third model here, and derived data in the `rebuild_registry` sense: a function of the
    files in the store, rebuilt by the `storage` rebuilder when something says they changed.
    Stored rather than walked per render because a breseq report tree is thousands of files
    per sample and the dashboard sums across the installation. The kinds are whatever
    `mutint_common.storage_registry` has registered -- a plugin's as much as core's, which is
    why the table lives here beside `DerivedDataState` rather than in `mutint_sample`.

    **A missing row reads as 0.** Whether that 0 is trustworthy is `DerivedDataState`'s
    question, under the `storage` name; a page that wants the truth asks `ensure_measured`.

    CASCADE on the experiment, and the experiment's soft delete leaves the rows alone: that
    is what lets the dashboard say how much a purge would free. `purge_deleted` takes the
    rows with the experiment.
    """

    experiment = models.ForeignKey("mutint_experiment.Experiment", on_delete=models.CASCADE,
                                   related_name="storage_usage")
    kind = models.CharField(max_length=64, db_index=True,
                            help_text="the key the kind is registered under")
    bytes = models.BigIntegerField(default=0)
    measured_at = models.DateTimeField()

    class Meta:
        constraints = [
            UniqueConstraint(fields=["experiment", "kind"],
                             name="one_storage_row_per_experiment_per_kind"),
        ]
        verbose_name = "storage usage"
        verbose_name_plural = "storage usage"

    def __str__(self):
        return "experiment %s %s: %d bytes" % (self.experiment_id, self.kind, self.bytes)
