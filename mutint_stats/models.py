"""No models.

Two stood here, and they were the same idea a generation apart. `StaticData` held the needle
plot's `{coord, category, value}` list as a JSON blob, precomputed at import since long before
there was a rebuild registry to keep it current. `ExperimentSummary` held the Overview's four
count dictionaries, and was added when the page's cost was traced to materialising every
call in the experiment to arrive at about sixteen integers.

Both are gone, and for the same reason: the cost was never the counting, it was fetching rows
as model instances. Reading the three or four columns each answer actually needs, as tuples,
brings the needle plot to 0.05s and the counts to 0.07s on the largest experiment in the dev
database -- 52 139 calls, against 3.38s for the model-instance path.

What went with them is what a cache costs beyond disk. Two registered rebuilders, marked stale
by every mutation edit and every filter change. Two `ensure_fresh` calls on the read path. A
`StaticData` row tied to its experiment only by the convention `id == ale_id`, with no foreign
key, which both `delete_experiments` and `purge_deleted` had to remember to sweep. And a
failure mode particular to caching two halves of one page: `/stats` renders both of these from
the same mutations, and while both were stored they could disagree in the same viewport.
Neither is stored now and both read `get_mutation_call_queryset`, so they cannot.

The app has no migrations left at all -- only an empty `migrations/__init__.py`. The one that
dropped both tables went with the migration collapse, along with every other file that named a
state no current database is in. A deployment that predates the collapse cannot migrate across
it in any case; see `docs/contributing/releasing.md` for why that stops being true at the first
release tag.
"""
