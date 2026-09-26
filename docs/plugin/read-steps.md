# Steps on a sample's reads

Some components turn FASTQ reads into a sample: mutint-breseq runs breseq on them. Before
the mutations are predicted, other components can do something to those reads. They can
check their quality, trim them, or screen them for contamination. `read_step_registry` is
the seam between the two sides:

- a **step** is what a consumer registers;
- a **producer** runs whichever steps somebody ticked.

Neither side imports the other, and core runs nothing itself, because core has no reads.

## Registering a step

```python
from django.apps import AppConfig


class FastqcConfig(AppConfig):
    name = 'mutint_fastqc'

    def ready(self):
        from mutint_common.read_step_registry import STAGE_INSPECT, register_read_step
        from mutint_fastqc import step

        register_read_step(self, 'fastqc', 'FastQC report', step.run,
                           stage=STAGE_INSPECT, default=True,
                           available=step.available,
                           attach=step.attach, discard=step.discard,
                           description='Runs FastQC on each read file as it was uploaded.')
```

**Choose the stage by what the step does to the reads.**

| stage | the step | example |
|---|---|---|
| `STAGE_INSPECT` | looks at the reads and must not change them. What `run` returns is ignored. | FastQC |
| `STAGE_TRANSFORM` | may return a new list of read paths, which the next step and the producer's tool use instead | fastp trimming |

Every inspect step runs before every transform step. That is why a QC report describes the
reads as they were uploaded, whichever app happens to load first. Within a stage, steps run in
`INSTALLED_APPS` order.

**`available()`** returns `(True, '')` or `(False, why)`. If it answers `False`, the launch
page draws the step's checkbox disabled with the reason beside it. A launch that asks for the
step anyway is refused before anything is uploaded. Use it to check that your tool is
installed.

## What `run(ctx)` gets

`ctx` is a `ReadStepContext`:

| field | what it is |
|---|---|
| `experiment` | the experiment the sample will land in |
| `sample_name` | the name the producer will give the sample |
| `reads` | absolute paths to the reads, as the previous step left them |
| `paired` | whether files that pair by name are treated as mates |
| `producer` | a string naming the producer's run, e.g. `"mutint_breseq:42"`. Key your rows by it. |
| `display_name(path)` | the name the person gave the file. The producer may have renamed it on disk. |
| `scratch_dir(name)` | a directory under the run, deleted when the run ends |
| `run_tool(argv, what, env=None)` | runs a command into the job's log, under the run's deadline and cancel flag |
| `write(text)` | writes a line of your own into the job's log |
| `note(text)` | records a sentence about a run that worked but should be looked at |

Run your tool through `ctx.run_tool` rather than calling `subprocess` yourself. That way a
Cancel on `/jobs/` stops it and its output appears in the job's log while it runs. Let the
exceptions `run_tool` raises (`processes.Cancelled`, `subprocess.TimeoutExpired`, `OSError`)
propagate: the producer maps them the same way it maps its own tool's.

## Failing, or not

**Raise `ReadStepFailed(message)` when the run should not go on without you.** Trimming does
this: breseq on reads that fastp could not read is not a run anybody wants.

**Otherwise, catch your own trouble and say so with `ctx.note`.** Do this when your step is
advisory. A QC report that could not be made is worth a sentence in the run's notes, and not
worth throwing away the run.

## The sample does not exist yet

While the steps run, the producer has not imported anything, so there is no `Sample` for your
rows to point at. Create them keyed by `ctx.producer` with a nullable sample. The producer then
calls exactly one of your two callbacks:

- **`attach(producer, sample)`**, once the import has landed. Fill in the sample here.
- **`discard(producer)`**, if the run failed or was cancelled. Delete your rows here.

Both are isolated. If one of them raises, the error is logged and the other steps' callbacks
still run. By then the run has already succeeded or already failed, and your bookkeeping must
not change which.

Keep whatever outlives the run under `store.component_dir(<your app>, <row pk>)`, with a
`post_delete` receiver on the row. `scratch_dir` is removed along with the producer's run
directory.

## Writing a producer

A producer does the following:

1. Stores the step names that were ticked on its own run row.
2. At launch, checks them with `clean_selection(names, check_available=True)`, which raises
   `ValueError` naming an unknown or unavailable step.
3. In its task, builds a `ReadStepContext` and calls `run_read_steps(clean_selection(names), ctx)`.
   It uses the list that call returns as its reads.
4. After a successful import, calls `attach_read_steps(names, producer, sample)`. On failure or
   cancellation, calls `discard_read_steps(names, producer)` instead.

mutint-breseq's `tasks.py` is the worked example.

## Showing what you kept

To link a sample to something your step kept, register with
[`sample_link_registry`](../reference/sample_link_registry.md):

```python
register_sample_link(self, 'fastqc', step.sample_links)
```

`sample_links(sample, request)` returns `[(label, url, title), ...]`. The links are drawn in
the box that heads the sample's Mutations page. If you serve HTML that a tool wrote, serve it
with a `Content-Security-Policy: sandbox` header. That is the rule for markup this codebase
did not write.
