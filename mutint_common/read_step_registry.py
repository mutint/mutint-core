"""Registry of steps run on a sample's reads before its mutations are predicted.

Apps call `register_read_step()` from their `AppConfig.ready()`. A **producer** -- a component
that turns FASTQ reads into a sample, which today is mutint-breseq -- asks this registry which
steps somebody ticked, builds a `ReadStepContext` over the reads it holds, and hands both to
`run_read_steps`. It is the twelfth registry, and the first whose callers are plugins rather
than core: core has no reads to run anything on, so it owns the seam and nothing else.

**Why this exists.** fastp trimming was the only thing done to reads before breseq, and it was
written into mutint-breseq's task as a block of its own, with a column, a checkbox and two
cleanup paths to match. Nothing else could join it -- a FastQC step, a contamination screen,
a subsampler -- without an edit to that plugin, and a second producer would have had to copy
all of it. So trimming is a registered step now, owned by mutint-breseq like any consumer's,
and the task runs whatever is ticked.

**Two stages, and order is correctness.** An `inspect` step looks at the reads and must not
change them (a QC report); a `transform` step may hand back a replacement list (trimming).
Every inspect step runs before every transform step, so a QC step always sees the reads as they
arrived, whichever app loaded first. Within a stage the order is INSTALLED_APPS order. This is
the third registry that takes an order, after `import_registry` and `rebuild_registry`, and for
their reason: what a step sees depends on what ran before it.

**The Sample does not exist while the steps run.** A step that keeps something about the reads
creates its rows keyed by `ctx.producer` -- a string naming the producer's own run -- and is
told which sample they belong to by `attach(producer, sample)` once the import has landed, or
told to throw them away by `discard(producer)` if the run failed or was cancelled. Both are
isolated per step: one that raises is logged and skipped, because by then the producer's run
has either already succeeded or already failed, and a consumer's bookkeeping must not change
which.

**A step's failure is the producer's failure only when the step says so.** `run` raising
`ReadStepFailed` fails the run -- trimming does, because breseq on reads fastp could not read
is not a run anybody wants. A step whose work is advisory catches its own trouble, says so with
`ctx.note`, and returns. `processes.Cancelled`, `subprocess.TimeoutExpired` and `OSError`
propagate unchanged, so a producer maps them exactly as it maps its own tool's.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

STAGE_INSPECT = "inspect"
STAGE_TRANSFORM = "transform"
_STAGES = (STAGE_INSPECT, STAGE_TRANSFORM)

#: [ReadStep], in registration order.
_steps = []


class ReadStepFailed(RuntimeError):
    """A step could not do its work and the producer's run should fail with this message."""


class ReadStep:
    """One registered step. Read-only by convention; `register_read_step` builds them."""

    def __init__(self, app, name, label, run, stage, default, available, attach, discard,
                 description):
        self.app = app
        self.name = name
        self.label = label
        self.run = run
        self.stage = stage
        self.default = default
        self._available = available
        self.attach = attach
        self.discard = discard
        self.description = description

    def available(self):
        """`(True, '')` when the step can run here, else `(False, <why>)`. Never raises."""
        if self._available is None:
            return True, ""
        try:
            answer = self._available()
        except Exception as exc:
            logger.exception("read step %s: availability check failed", self.name)
            return False, str(exc)
        if isinstance(answer, tuple):
            return bool(answer[0]), answer[1] if len(answer) > 1 else ""
        return bool(answer), ""

    def __repr__(self):
        return "<ReadStep %s (%s)>" % (self.name, self.stage)


def register_read_step(app_config, name, label, run, *, stage, default=True, available=None,
                       attach=None, discard=None, description=""):
    """Register a step on a sample's reads (from `AppConfig.ready()`).

    app_config   the AppConfig itself, `self` at the call site, so a warning names the app.
    name         stable slug, unique across the installation: it is what a producer stores on
                 its run and what the launch page posts. Registering it again replaces the
                 first entry in place, so a reload cannot double a step.
    label        what the launch page's checkbox says.
    run          callable(ctx) -> None | [path, ...]. A transform step returns the reads the
                 next step and the producer's tool should use; an inspect step's return value
                 is ignored.
    stage        STAGE_INSPECT or STAGE_TRANSFORM.
    default      whether the launch page ticks it to begin with.
    available    optional callable() -> (bool, why). A step that cannot run here is drawn
                 disabled with the reason, and a launch that asks for it anyway is refused.
    attach       optional callable(producer, sample), after the producer's import landed.
    discard      optional callable(producer), after the producer's run failed or was
                 cancelled.
    description  a sentence the launch page shows beside the checkbox.
    """
    if stage not in _STAGES:
        raise ValueError("stage must be one of %s, not %r" % (", ".join(_STAGES), stage))
    step = ReadStep(app_config.name, name, label, run, stage, default, available, attach,
                    discard, description)
    for index, existing in enumerate(_steps):
        if existing.name == name:
            _steps[index] = step
            return name
    _steps.append(step)
    return name


def unregister_read_step(name):
    """Remove a registered step. For tests; nothing in the product unregisters."""
    global _steps
    _steps = [step for step in _steps if step.name != name]


def steps():
    """Every registered step, in the order they would run."""
    return sorted(_steps, key=lambda step: _STAGES.index(step.stage))


def get_step(name):
    for step in _steps:
        if step.name == name:
            return step
    return None


def clean_selection(names, check_available=False):
    """The steps `names` asks for, in run order.

    Raises ValueError naming an unknown step -- a name somebody posted, or one a run stored
    before the component that registered it was uninstalled. With `check_available`, also
    naming a step that cannot run on this machine; a launch asks that, a worker does not,
    since a worker's answer is the step's own to give.
    """
    wanted = list(names or [])
    known = {step.name for step in _steps}
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise ValueError("No such read step: %s." % ", ".join(sorted(set(unknown))))
    selected = [step for step in steps() if step.name in wanted]
    if check_available:
        for step in selected:
            ok, why = step.available()
            if not ok:
                raise ValueError("%s cannot run here: %s" % (step.label, why or "unavailable."))
    return selected


class ReadStepContext:
    """What a producer hands every step: the reads, and the run's own machinery.

    experiment     the experiment the sample will land in.
    sample_name    what the producer will call it.
    reads          absolute paths, as the previous step left them.
    paired         whether the producer treats files that pair by name as mates.
    producer       a string naming the producer's run, e.g. "mutint_breseq:42". What a step
                   keys its rows by until `attach` says which sample they belong to.
    work_dir       the producer's run directory; `scratch_dir` makes subdirectories of it, and
                   the producer removes the whole of it when the run ends.
    log            the job's log, as `mutint_jobs.logs.open_log` yields it.
    deadline       a `time.monotonic()` value the steps' tools must finish by, or None.
    is_cancelled   callable() -> bool, the job's cancellation flag.
    display_name   callable(path) -> the name the person gave the file, where the producer
                   renamed it on disk. Defaults to the basename.
    note           callable(text): a sentence about a run that worked but did something a
                   person should know about. Defaults to writing it to the log.
    """

    def __init__(self, *, experiment, sample_name, reads, paired, producer, work_dir,
                 log=None, deadline=None, is_cancelled=None, display_name=None, note=None):
        self.experiment = experiment
        self.sample_name = sample_name
        self.reads = list(reads)
        self.paired = paired
        self.producer = producer
        self.work_dir = work_dir
        self.log = log
        self.deadline = deadline
        self.is_cancelled = is_cancelled or (lambda: False)
        self._display_name = display_name
        self._note = note

    def display_name(self, path):
        if self._display_name is not None:
            return self._display_name(path)
        return os.path.basename(path)

    def scratch_dir(self, name):
        """`<work_dir>/steps/<name>`, created. Gone when the producer's run ends."""
        path = os.path.join(self.work_dir, "steps", name)
        os.makedirs(path, exist_ok=True)
        return path

    def write(self, text):
        """A line of the step's own in the job log."""
        if self.log is None:
            return
        from mutint_jobs import logs
        logs.write(self.log, text)

    def note(self, text):
        if self._note is not None:
            self._note(text)
        else:
            self.write(text)

    def remaining(self):
        """Seconds left before the deadline, at least one; None when there is no deadline."""
        if self.deadline is None:
            return None
        return max(1, self.deadline - time.monotonic())

    def run_tool(self, argv, what, env=None):
        """`mutint_jobs.processes.run_tool` against this run's log, deadline and flag.

        Returns the exit status. Raises `processes.Cancelled`, `subprocess.TimeoutExpired`
        and `OSError` as that does; a step lets them go, and the producer maps them.
        """
        from mutint_jobs import processes
        return processes.run_tool(argv, self.log, env=env, timeout=self.remaining(),
                                  is_cancelled=self.is_cancelled, what=what)


def run_read_steps(selected, ctx):
    """Run `selected` over `ctx.reads` in order, and return the reads the producer should use.

    The cancellation flag is asked between steps, and a cancel raises
    `mutint_jobs.processes.Cancelled` exactly as a cancel inside a tool does, so a producer
    has one place to catch it.
    """
    from mutint_jobs import processes

    for step in selected:
        if ctx.is_cancelled():
            raise processes.Cancelled()
        replacement = step.run(ctx)
        if step.stage == STAGE_TRANSFORM and replacement is not None:
            ctx.reads = list(replacement)
    if selected and ctx.is_cancelled():
        raise processes.Cancelled()
    return list(ctx.reads)


def attach_read_steps(names, producer, sample):
    """Tell each step in `names` that `producer`'s reads became `sample`. Never raises."""
    _each(names, "attach", producer, sample)


def discard_read_steps(names, producer):
    """Tell each step in `names` that `producer`'s run produced nothing. Never raises."""
    _each(names, "discard", producer)


def _each(names, hook, *args):
    for name in names or ():
        step = get_step(name)
        if step is None:
            continue
        callback = getattr(step, hook)
        if callback is None:
            continue
        try:
            callback(*args)
        except Exception:
            logger.exception("read step %s: %s failed for %s; skipping it",
                             name, hook, args[0])
