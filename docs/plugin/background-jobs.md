# Work that takes minutes, or hours

Anything long belongs off the request. The API is Django's own — `@task` and `.enqueue()` —
and mutint-core adds one thing on top: a record of **who asked**, so the work appears on
`/jobs/` with a name and can be stopped.

## Declaring a task

An ordinary `django.tasks` task, in your app's `tasks.py`:

```python
from django.tasks import task

@task()
def analyze(row_id):
    ...
```

**The argument is a primary key, not a model.** Task arguments are serialized to JSON, so
passing an instance fails at enqueue time.

## Enqueueing it so a person can see it

```python
from mutint_jobs import jobs

jobs.enqueue(tasks.analyze, row.pk,
             user=request.user,
             label="analysis — %s" % row.name,
             component="my_plugin",
             experiment=experiment,
             cancellable=True)
```

`task.enqueue(...)` still works and still runs. The difference is that a plain enqueue has no
owner and no name, so it appears on `/jobs/` only for superusers, in the unattributed section.
Use `jobs.enqueue` for anything a person asked for.

`label` matters more than it looks: it is the only thing on the page that says what the job
*is*. `analyze(4)` means nothing to the person who pressed the button.

## Making it stoppable

`cancellable=True` is a **promise your task keeps**, not a property core can give it. The
queue offers no way to interrupt a running task — `django_tasks_db` has no cancel API, and its
worker calls your function and looks at nothing again until it returns. So cancellation is
cooperative: core records that somebody asked, and your task is the only thing that can act.

Check on the way in, and again wherever you can:

```python
from mutint_jobs import jobs

@task()
def analyze(row_id):
    row = MyRow.objects.filter(pk=row_id).first()
    if row is None or jobs.is_cancelled(row.task_result_id):
        return None
    for step in steps:
        jobs.check_cancelled(row.task_result_id)   # raises JobCancelled
        ...
```

`is_cancelled` is one indexed read, so polling it every couple of seconds costs nothing next
to work measured in minutes. Store the `Job`'s `task_result_id` on your own row when you
enqueue — that is the handle.

**Leave `cancellable` at its default of False if you do not poll.** The page renders a Cancel
button only for jobs that claim they can be stopped, and a button that silently does nothing
is worse than no button.

### If your task runs a subprocess

**Do not write the loop.** `mutint_jobs.processes.run_tool` runs a command, polls the
cancellation flag while it runs, kills the whole process group when somebody asks, and writes
everything the command prints into the job's log:

```python
from mutint_jobs import jobs, logs, processes

with logs.open_log(task_result_id) as log:
    logs.write(log, "trimming %s" % name)          # your own commentary
    code = processes.run_tool(argv, log, env=env, timeout=3600,
                              is_cancelled=lambda: jobs.is_cancelled(task_result_id),
                              what="my-tool")
```

It returns a returncode and nothing else; the output is in the log. It raises
`processes.Cancelled` when the job was stopped, and `subprocess.TimeoutExpired` on the budget.

Two things it is doing for you, both easy to get wrong in ways that look fine:

- **`subprocess.run` cannot be cancelled.** It blocks until the process exits, so there is no
  moment at which anything can ask whether the job is still wanted. `Popen` plus a poll loop
  is the whole difference.
- **Signal the process group, not the process.** Most tools spawn children of their own.
  `process.kill()` reaps the one you started and leaves its children running with no parent —
  the job reports itself stopped while the machine stays busy. `start_new_session=True` makes
  the whole run one group with one thing to signal.

`mutint_breseq/tasks.py` is the worked example.

### The log, and how a task reaches its own

`logs.open_log` takes the **queue's result id** — the same handle `jobs.is_cancelled` takes —
and the log is written at `<store>/components/mutint_jobs/<id>/job.log` while the job runs,
gzipped when it finishes. `/jobs/<pk>/log` renders the tail with a Refresh button and offers
the whole thing as a download; `/jobs/` links every job that has one.

**Get the id from the task context, not from your own row.** A row's copy is written after
`jobs.enqueue` returns, so a backend that runs the task inside `enqueue` — which is what the
test suite uses — executes the whole thing before that column is set:

```python
@task(takes_context=True)
def analyze(context, row_id):
    task_result_id = context.task_result.id
```

There is nothing to opt into: a task that runs no command writes no log, and `/jobs/` links
only the jobs that have one. Nothing here can fail your task — an unwritable log is logged and
skipped, the posture `is_cancelled` takes.

### Cancellation is not failure

Record it as its own state and **do not re-raise**. Letting `JobCancelled` out marks the job
FAILED on the queue and puts a traceback in front of somebody who got exactly what they asked
for. Tidy up, record it, return.

## What a person sees

`/jobs/`, from the sidebar under their username. Their own jobs; everything for a superuser,
plus the unattributed section. It polls while anything is unfinished and stops when nothing is.

## Running the work

**`./mutint start` runs a worker alongside the dev server**, so in development the queue
drains on its own. It is spawned under a deadman supervisor that stops it when the server
goes — including `kill -9` — and `--no-worker` turns it off.

**Everywhere else, nothing spawns one.** `./mutint db_worker` is what executes what has been
enqueued, and it must be started through the entry script — a bare `manage.py db_worker` has
neither the database connection nor `MUTINT_TOOLS_DIR`, so it finds none of the external tools.
A deployment runs one under whatever supervises its web server.

The worker `start` runs is deliberately **not** reloaded on code changes, so it executes the
code as of launch. Restart the server after editing a task, or your edit is not what runs.

`./mutint reap_jobs` clears queue rows that were never claimed — the library's own
`prune_db_task_results` only removes *finished* ones, so an unclaimed row is otherwise
immortal. It also sweeps job logs belonging to no job row, which is what a bare
`task.enqueue` leaves behind: a job's own log goes with its row, and work that never made one
has nothing to go with.

Decide, before you enqueue anything, **which kind of task yours is**:

- *Benign when skipped*, like coverage derivation: the product works without it and a command
  backfills later. An installation with no worker is merely missing something.
- *Broken when skipped*, like a breseq run: nothing else will ever do it. Then your page has
  to say so — ask the queue and tell the reader that nothing has picked the job up, rather
  than leaving "queued" to mean both "soon" and "never". `/jobs/` does this for you if you
  enqueue through `mutint_jobs`, and also says when work has been waiting with nothing taking
  it — which is as close to "is a worker running" as can honestly be asked, since the queue
  keeps no worker registry and no heartbeat.

## Testing it

The suite forces the immediate backend, where `.enqueue()` means "run it now". That is right
for tests that care what the work *produced* — one POST exercises everything — and useless for
tests about queueing, where there is no queued job to cancel and
`ImmediateBackend.supports_get_result` is False so every status reads as unknown.

For those, override it, as `mutint_import/tests/test_tasks.py` and `mutint_jobs/tests/test_jobs.py`
both do:

```python
DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}

@override_settings(TASKS=DATABASE_BACKEND)
class MyQueueTestCase(TestCase):
    ...
```

and drive the real worker with
`Worker(queue_names=["default"], interval=0, batch=True, ...).run_task(row)`.
