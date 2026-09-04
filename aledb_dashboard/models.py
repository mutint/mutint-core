from django.db import models


class InstallationCounts(models.Model):
    """How much of each thing the installation holds, as one row per kind of count.

    **Why any of this is stored**, when almost nothing derived is any more: the dashboard
    applies no filter. Its answer is the same for every reader, so it can live in a shared
    table at all -- and `mutation_counts` is the most expensive rebuild in the suite, walking
    every `MutationCall` there is. `ensure_fresh` pays that once after a change; computing it
    per view would pay it per view, and its cost grows with the whole database rather than
    with one experiment.

    **This was three tables** -- `InventoryCounts` and a pair, `MutationCallCounts` and
    `UniqueMutationCounts`, whose field lists were byte-identical: thirty-five integer columns
    in all. Nothing ever filtered, joined or aggregated them; every read in the suite was *get
    the one row and read attributes off it*.

    Those columns were a fan-out of dicts that already existed. `rebuild_mutation_counts`
    builds its counts keyed by `MUTATION_TYPE_LIST` and `FUNCTIONAL_CHANGE_TYPE_LIST` and then
    spent an eighteen-branch `if/elif` chain spreading them across columns -- while
    `aledb_stats.util._empty_counts` builds the same dicts for the per-experiment Overview and
    stores nothing, and `/stats` renders them dict-keyed. The dict was already the suite's
    shape for these numbers; the columns were a lossy copy.

    Lossy literally: `MUTATION_TYPE_LIST` has nine entries and the chain had eight branches,
    so the `unannotated` *type* bucket was counted and discarded for want of a column, and the
    totals did not equal the sum of what was displayed. They do now.

    The JSON earns the same thing it earns on `Mutation.annotation` and `MutationCall.
    evidence`: nothing queries these, they are read whole to render a page, and a new counter
    needs no migration. A token added to either vocabulary is simply counted and shown --
    where before it raised `FieldError` on the next rebuild if somebody forgot the migration,
    which is how `nonsense` came to be missing for years.
    """

    #: The installation's inventory: `{"population": n, "time_point": n, "sample": n}`.
    INVENTORY = "inventory"
    #: Every call, bucketed. See `data` for the shape.
    MUTATION_CALLS = "mutation_calls"
    #: Distinct mutations behind those calls, same shape.
    UNIQUE_MUTATIONS = "unique_mutations"

    #: Which count this row holds -- one of the three above.
    #:
    #: **Three rows rather than one merged payload**, because the two rebuilders that write
    #: them are invalidated by different things. `aledb_experiment.samples` narrows to
    #: `only=('sample_counts',)` on a renumber, which provably cannot change a mutation count
    #: and must not pay for one. Separate rows mean the cheap rebuild writes its own row
    #: whole, rather than reading and re-writing one the expensive rebuild also owns.
    name = models.CharField(max_length=32, unique=True)

    #: The counts. For the two mutation rows::
    #:
    #:     {"total": 74859,
    #:      "type":              {"SNP": 41203, ..., "unannotated": 2720},
    #:      "functional_change": {"nonsense": 392, ..., "unannotated": 2720}}
    #:
    #: **The nesting is load-bearing.** Both vocabularies contain a token spelled
    #: `unannotated` and they answer different questions -- "no mutation type we know" and
    #: "no SNP class we know". Flattened, one would silently overwrite the other; that
    #: collision is the whole reason this is two sub-dicts and not one.
    #:
    #: Keys are the vocabulary tokens verbatim, so `rebuild_mutation_counts` stores what it
    #: already built and the `'SNP' -> single_base_substitution` translation is gone from the
    #: write path entirely. Display names live beside their vocabularies -- see
    #: `MUTATION_TYPE_LABELS` and `FUNCTIONAL_CHANGE_LABELS`.
    data = models.JSONField(default=dict)

    class Meta:
        verbose_name_plural = "installation counts"

    def __str__(self):
        return "%s counts" % self.name
