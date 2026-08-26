"""State for the rebuild registry -- what derived data is stale, and why.

This is `aledb_common`'s only model, and the app had none before it. It lives here rather than
in `aledb_stats` beside the data it most often describes because it is not about statistics: it
tracks staleness for every registered rebuild, plugins included, and `aledb_fixation`'s state
has no business living inside the stats app. The shared layer is where a table shared by every
component belongs.

See `aledb_common/rebuild_registry.py` for how rows here are written and read.
"""

from django.db import models
from django.db.models import Q, UniqueConstraint


class DerivedDataState(models.Model):
    """Whether one named rebuild's output is current, for one experiment or for the site.

    **A missing row means stale.** Never built and built-then-invalidated are the same question
    to every caller -- "does this need recomputing" -- and answering them the same way means a
    newly registered rebuilder needs no backfill and a new experiment needs no seeding.

    `ale_experiment` is NULL for a SITE_SCOPE rebuild, and that is why there are two constraints
    below rather than one `unique_together`. In SQL a NULL is not equal to another NULL, so a
    unique index over (name, ale_experiment) does not constrain the site-scoped rows at all --
    it would happily hold a dozen rows saying different things about the same rebuild, and
    whichever `is_stale` read first would win. The partial index says the thing the pair cannot.
    """

    name = models.CharField(max_length=100, db_index=True,
                            help_text="the name the rebuilder is registered under")
    ale_experiment = models.ForeignKey("aledb_experiment.AleExperiment", null=True, blank=True,
                                       on_delete=models.CASCADE,
                                       help_text="NULL for a site-scoped rebuild")
    stale_since = models.DateTimeField(null=True, blank=True, db_index=True,
                                       help_text="NULL means current")
    rebuilt_at = models.DateTimeField(null=True, blank=True)
    # Kept as a column and not only a log line: an isolated failure is quiet by design, so
    # `./aledb rebuild --list` needs somewhere to read it back from.
    last_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            UniqueConstraint(fields=["name", "ale_experiment"],
                             name="one_state_per_rebuild_per_experiment"),
            UniqueConstraint(fields=["name"], condition=Q(ale_experiment__isnull=True),
                             name="one_state_per_site_scoped_rebuild"),
        ]
        verbose_name = "derived data state"
        verbose_name_plural = "derived data states"

    def __str__(self):
        where = "site" if self.ale_experiment_id is None else "experiment %s" % self.ale_experiment_id
        return "%s (%s): %s" % (self.name, where, "stale" if self.stale_since else "current")
