# Models and derived data

Almost nothing user-facing in ALEdb is computed at read time. The needle plot, the Overview's
counts, the dashboard's totals, fixation's table, convergence's table and phylogeny's tree are
all the same idea: **a table that is a function of the mutations, recomputed when they
change**. The Django cache framework is not used for any of it — `CACHES` is LocMemCache and
would be per-process.

If your plugin computes anything expensive, it belongs in a model and in this system.

## Storing it

Ordinary Django models in your app, with ordinary migrations. Two conventions worth following:

- **Rebuild whole, do not update incrementally.** Every plugin here deletes its rows for an
  experiment and recomputes them. A sample arriving out of order can change a result anywhere
  in the table, not just at the end.
- **Do not put a foreign key on `Mutation` if you can avoid a hard dependency**, but know what
  you are taking on if you store bare ids instead. `aledb_phylogeny`'s JSON stores `Mutation.id`
  as plain integers, which is only safe because of an invariant core maintains: the mutation
  editor deletes `MutationCall` rows and never `Mutation` rows, so an id keeps meaning what
  it meant.
- **A record that arrives with an import can go on the mutation instead.**
  `Mutation.supplemental_data` is namespaced by component, so you may keep your own import
  record beside core's without a table:

  ```python
  mutation.set_record("my_plugin", "vcf", {"qual": 40, "filter": "PASS"})
  record = mutation.supplemental_data.get("my_plugin", {}).get("vcf") or {}
  ```

  `set_record` merges rather than assigns, so you cannot drop core's key or another
  plugin's. Two things earn their place there and nothing else does: the data **arrives with
  the import** and shares the mutation's lifetime, and it is **read whole** rather than
  queried. In exchange it survives the orphan sweep and a restore, because
  `MutationEdit.mutation_identity` snapshots the whole container.

  **What does not belong there**: anything with its own lifecycle, anything you want to
  filter or aggregate on, and anything large. This column is loaded on every read of a
  `Mutation`, which is the row this codebase works hardest not to instantiate. That is a
  table with a plain foreign key — the rest of this page.
- **Ask whether you need a table at all.** `aledb_converge` had one and dropped it: computing
  convergence on demand came out at 0.17s where keeping the stored answer correct cost a
  rebuilder, a staleness row marked on every edit, an `ensure_fresh` on the read path, and a
  column of bare `Mutation` ids. A cheap query beats a cache you have to keep honest.

## Registering the rebuild

```python
from aledb_common.rebuild_registry import register_rebuilder

register_rebuilder('aledb_yourthing', rebuild_your_table)
```

`register_post_experiment_hook(fn)` is the older name for the same thing and still works; it
derives a name from your app label and **returns it**, which is what you store if you need to
ask about staleness later.

The function takes one argument, the experiment id, and is called after anything changes an
experiment's mutations — an import, a mutation added or deleted, a filter edit.

**Failures are isolated.** A rebuild that raises is logged, recorded in `last_error` and left
marked stale; it does not abort the other plugins' rebuilds and does not fail the import that
triggered it. Stale data is recoverable, a failed import is not. The trade is that a broken
rebuild is quiet, which is why `./aledb rebuild --list` exists.

## Marking and running are separate

```python
request_rebuild(experiment_id)   # "this is stale now" -- one UPDATE, safe from any request
run_rebuilds(experiment_id)      # "so recompute it"   -- expensive
```

Marking is cheap enough to do from any request, including one that invalidates data across
every experiment at once — deleting a project is exactly that, since the dashboard's totals
count the whole installation. Note what that caller does anyway: it passes `only=` naming the
two aggregates, because removing one experiment cannot make another's needle plot wrong, and
`request_rebuild()` with no experiment and no `only=` would mark every derived thing there is. Running happens in one of three
places: eagerly at the end of an import, lazily by the page that reads the data, or in bulk
from `./aledb rebuild`.

**The lazy path is yours to implement.** Core will mark your data stale; nothing will
recompute it before somebody looks unless you ask:

```python
from aledb_common.rebuild_registry import ensure_fresh

def get_your_table(experiment_id):
    ensure_fresh(REBUILD_NAME, experiment_id)
    return YourTable.objects.filter(experiment_id=experiment_id)
```

`ensure_fresh` no-ops when the data is current, and **cannot raise** — a rebuild that fails
leaves the previous answer on screen with the error in `./aledb rebuild --list`, rather than
500ing the page. Every plugin reader should call it. For a long time only one reader in the
whole suite did, and the result was a filter change that never reached three plugin pages.

## Opting out of automatic rebuilds

```python
register_rebuilder('aledb_yourthing', rebuild_your_table, auto=False)
```

`auto=False` means your data is **tracked and marked stale like anything else, and never
rebuilt behind anyone's back**. Nothing runs it unless it is named explicitly —
`./aledb rebuild <id> --only aledb_yourthing`, or a button on your own page. `force=True` does
not override it, because imports force.

Use it when your computation is expensive enough, or answers a question specific enough, that
recomputing it unasked would be wrong. `aledb-phylogeny` uses it: a tree is an inference with
a parameter, not a count to be refreshed, so when the mutations move it hides the tree and
asks rather than silently drawing a different topology.

!!! warning "Opting out is a promise"

    A page that registers `auto=False` and then forgets to check `is_stale` is worse off than
    one that never registered — it now has a staleness record nobody reads, and it shows old
    data with no indication. If you opt out, the reading page owns the check.

## Telling core that *you* changed something

This is the direction that makes `rebuild_registry` the only bidirectional registry. If your
plugin writes something that invalidates derived data — its own or core's — say so:

```python
from aledb_common.rebuild_registry import request_rebuild

request_rebuild(experiment.ale_id, reason='your thing recalculated')
```

Name what changed with `only=` when you can. `get_rebuilders` **skips a name nothing
registered**, so you may name a plugin you cannot know is installed and a deployment without
it simply has less to do.
