# The data model

The tables a plugin queries, and the handful of rules about them that are not obvious from the
field lists. This is the schema as it stands, not a tour of every column — read
`aledb_sample/models.py` and `aledb_experiment/models.py` for those, where the reasoning is in
the docstrings.

## The spine

Five models carry the data. Everything else in the schema hangs off one of them.

```
Experiment ── Population ── Sample ── MutationCall ── Mutation
     └────────────────────────────────────────────────┘
```

| | |
|---|---|
| `Experiment` | one evolution experiment; the unit access is granted below and the unit almost every page is scoped to |
| `Population` | one ALE — a lineage within the experiment, with its strain and species |
| `Sample` | one sequenced flask or isolate, at a `time_point` |
| `Mutation` | one genomic change, **per experiment**, with its annotation |
| `MutationCall` | one caller's assertion about one mutation in one sample |

`Mutation` belongs to the experiment rather than to a sample, because the same change seen in
forty samples is one row and forty calls. That is why a cross-sample table is a join over
`MutationCall` and why `Mutation.id` means something outside the row it came from.

**Do not spell the joins by hand.** `aledb_experiment.paths` holds them in one place:

```python
from aledb_experiment import paths

paths.to_experiment()                    # 'population__experiment'      from a Sample
paths.to_experiment(paths.FROM_CALL)     # 'sample__population__experiment'
```

The chain has changed twice — `TimePoint`, `Isolate` and `TechnicalReplicate` were levels of
their own and are not any more — and every repo that hardcoded a path had to be edited both
times. Nothing that goes through `paths` did.

## Mutation ids are stable; call rows are not

This is the one invariant a plugin is most likely to depend on without noticing.

- **`MutationCall` rows are hard-deleted.** The mutation editor removes an observation
  outright; nothing filters on a "deleted" flag, because there is nothing to filter. A row
  that is gone is gone.
- **`Mutation` rows are never deleted by an edit.** So a stored `Mutation.id` — in a JSON
  column, in an exported CSV — keeps meaning the same mutation. `aledb_phylogeny` stores bare
  ids on that promise.
- **But a mutation can be split.** Editing a mutation in *some* of the samples carrying it
  moves those calls onto a different row, which may be one that did not exist before. Ids
  never *move*; a mutation you recorded may simply have a sibling now.

If you store ids, store them for something you can rebuild. See
[Models and derived data](derived-data.md).

## What is JSON, and where yours goes

Thirteen columns are `JSONField`, and that is deliberate: nothing queries them, they are read
whole to render something, and a caller with a field core has no column for should not need a
migration.

Two of them are **namespaced by component**, and those are the ones you may write to:

```python
sample.set_record("your_plugin", "assembly", {"n50": 41203})
sample.record("assembly", component="your_plugin")     # -> {"n50": 41203} or {}
```

`Sample.supplemental_data` and `Mutation.supplemental_data` are both
`{component: {kind: {...}}}`. Core owns the `aledb_core` key and nothing else; write under
your own component name and core's writers will not touch it. `set_record` merges rather than
replaces, which matters because `update_fields` can name the column but cannot say which part
of it you meant.

Core's own groups, for reading:

| | |
|---|---|
| `sample.breseq` | `version`, `reads`, `average_read_length`, `mean_coverage`, `percentage_mapped` |
| `sample.sequencing` | `date`, `library_prep`, `reference_genome` |
| `sample.curation` | `medium_description` |
| `mutation.genome_diff` | the GenomeDiff record the import parsed, which `to_gd_line()` writes back |
| `call.vcf` | the VCF line this call arrived on, when it came from one |

Each answers `{}` when nothing was recorded, so a sample nobody has filled in renders rather
than raising. **Every one of these keys may be absent** — they were columns until recently and
several never had a writer at all.

**`MutationCall` carries `supplemental_data` as well as `evidence`, and they are different
things.** `evidence` is breseq's read counts in breseq's own shape, read by the mutation table
to render a cell; `supplemental_data` is the namespaced container, for the record a call
*arrived* with. The VCF import is what needed the second: `INFO`, `QUAL` and `FILTER` describe
a site while `FORMAT` and each sample column describe one sample's call, and a `Mutation` is
shared between samples. If your plugin has per-call material that came in with an import, it
goes here under your own component name.

The rest are not namespaced and are core's: `Mutation.annotation`, `MutationCall.evidence`
(one sample's read counts for one call), `ReferenceSequences.seq_ids`,
`InstallationCounts.data`, the editor's `snapshot` and `mutation_identity`,
`UploadSession.manifest` and `progress`, and `PhylogeneticTree`'s three.

## Getting the right samples and the right calls

Four helpers, and using them is not a style preference:

```python
from aledb_sample.util import (calls_for_samples, get_mutation_call_queryset,
                               get_ordered_reseq_queryset, get_reseq_ordered_dict)
```

- `get_mutation_call_queryset(experiment_id)` — every call in an experiment.
- `calls_for_samples(sample_ids, experiment_id)` — calls for a chosen set of samples.
- `get_ordered_reseq_queryset(experiment_id)` / `get_reseq_ordered_dict(...)` — the samples, in
  the order every page shows them.

**They subtract the experiment's designated ancestor, and hand-rolling the query does not.**
`Experiment.ancestor` names one sample whose mutations are the starting line rather than
evolution; they are removed from every other sample before anything is computed, and the
sample itself leaves every listing. This is not the reader's filter — it is shared, permanent,
and has no toggle.

Four repos each hand-wrote `MutationCall.objects.filter(sample_id__in=...)` before these
existed. The failure is quiet and severe: an ancestral mutation is in every ALE by
construction, so convergence reported all of them as convergent and fixation all of them as
fixed. If you need the cross-experiment form, it is
`aledb_experiment.ancestor.exclude_all_ancestry`.

Pass `include_ancestor=True` only on a page that curates rather than reads.

## Around the edges

**The reference.** `ReferenceSequences` is one row per experiment (`experiment.reference`)
holding `seq_ids`, the contig list with each contig's `sha256` and length. The files
themselves are in the managed store, not the database. `Mutation.seq_id` names a contig
within it.

`DatabaseSequenceLink` records that a contig's bases are byte-for-byte some database's record
— keyed on `(database, sha256)`, so the verdict cannot outlive the sequence it was about.
Today the only value is `"NCBI-nucleotide"`. Ask
`aledb_sample.ncbi.verified_contig_names(experiment)` before drawing anything in a public
database's coordinate space; a contig name is never the evidence.

**Uncalled regions.** `UncalledRegion` is where a sample had no coverage, per contig. A
mutation absent from a sample there was not observed to be absent — it was not looked at.
`aledb_phylogeny` encodes that as ambiguous rather than ancestral.

**Editing.** `MutationEditSet` and `MutationEdit` are the append-only log that makes every
edit reversible. You never write these; know they exist because every write to them requests a
rebuild of everything derived.

**Access.** `ProjectAccess` grants one of four ordered roles — read < write < admin < owner —
to a `User` or a `UserGroup`, on a **project**. Nothing below the project is owned. Ask
`can_view_project(user, experiment.project)` to read and
`can_edit_experiment(user, experiment)` to write — the second one, not
`can_edit_project`, because a lock lives on the experiment and a predicate handed the project
cannot see it.

**Derived state.** `DerivedDataState` tracks what is stale, `InstallationCounts` holds the
dashboard's site-wide totals. Both are described in
[Models and derived data](derived-data.md).

## What is not here

- **No table per plugin by default.** Four of the six shipped plugins define no models at all
  — compare, converge, fixation and the needle plot are computed by the request that renders
  them, and were faster for it. `aledb_phylogeny` stores one table, and what it stores is a
  cache it deletes rather than derived data it repairs.
- **No `TimePoint`, `Isolate` or `TechnicalReplicate`.** A sample's `time_point` is a float
  column on the sample, so `time_point__gte=500` is a filter rather than a join.
- **No per-sample media, instrument or freezer tables.** They were required foreign keys to
  singleton rows nothing read.
- **No `person` field anywhere.** Free text that looked like ownership and was not; ownership
  is `ProjectAccess`, and every write records the logged-in user.
