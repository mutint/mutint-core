# Taking a file upload

If what your plugin accepts is a *mutation file*, you want
[an import handler](registries.md) — register one, and the Import data page's drag-and-drop, its
type dropdown, its per-file progress table and its summary all reach you with no page of your
own.

This page is about the other case: a drop that is **not** something to import. `mutint-breseq`
takes FASTQ reads, which are the input to a job whose *output* is imported hours later, and
which needs a sample name and a command line beside them. An import handler cannot carry
either — `handle()` is given the experiment, the staged root, the paths and the user, and the
Add page has no box to put anything else in.

So core splits the two halves. It keeps the part that is about bytes arriving safely, and you
keep the part that is about what they are for.

## What core does for you

`mutint_import.staging`, plus the chunk endpoint you already have:

```python
from mutint_import import staging

session = staging.open_session(request.user, experiment, "my_plugin", files)
```

- **Permission** — `create_staging_session` asks `can_edit_experiment`, so a locked experiment
  refuses before a byte is transferred. Call `open_session` directly and the check is yours.
- **The manifest** — the client's declared `[{path, size}, ...]`, sanitized. It is a record of
  what was promised, not a trusted map.
- **Path containment** — every relative path is rejected if it is absolute, carries a drive
  letter or contains `..`, and every write is additionally confirmed to resolve inside the
  session's own directory. The directory is named by a server-generated UUID, so a client can
  neither guess another session's area nor influence where its own bytes land.
- **The chunk endpoint** — `POST /import/uploads/<id>/chunk`, unchanged. It does not care
  which kind of session it is appending to, so 8 MB slices, resume from a 409 and the retry
  budget are all already written.
- **Reaping** — a session left open past `MUTINT_UPLOAD_SESSION_TTL_HOURS` is cleared by
  `./mutint reap_uploads` like any other.

In the browser, `mutintUpload` is loaded on every page from `base.html`:

```js
mutintUpload(entries, {
    experimentId: EXPERIMENT_ID,
    consumer: "my_plugin",
    onProgress: function (done, total, label) { /* … */ }
}).then(function (uploadId) {
    return mutintPostJson("/my-plugin/launch", {upload_id: uploadId, /* … */});
});
```

`mutintCollectDropped(e.dataTransfer)` builds `entries` from a drop, descending into
directories; `mutintFromFileList(input.files)` does it from an `<input type=file>`.

## The one rule

**`claim` is the handover.**

```python
root = staging.claim(session)          # the directory is yours now
```

After it, nothing in core will delete that directory. Before it, an abandoned session is
reaped on the TTL. That asymmetry is deliberate and is the whole reason `claimed` is a state:
your work may legitimately outlive the TTL — a breseq run is hours — and a reaper that deleted
the files out from under it would be a failure nobody could reproduce.

The corollary is that **a claim you do not finish leaks a directory**. So claim, move the
files somewhere you reap yourself, and close:

```python
root = staging.claim(session)
shutil.move(os.path.join(root, name), my_own_directory)
staging.close(session)                 # removes what is left of the staging area
```

`staging.abandon(session)` is the same thing for the path where you decide not to proceed.

For "somewhere you reap yourself", core offers a path and nothing else:

```python
from mutint_common import store
store.component_dir("my_plugin", row.pk)     # <store>/components/my_plugin/<pk>/
```

Unlike every other function in `store`, that one does not name an artifact core owns, because
core cannot know what you keep there. What it still owns is the shape — the component must be
an app label and the key a primary key — so the rule that no client-supplied path component
reaches the filesystem still holds. **Deleting it is yours**, which for a row-keyed directory
means a `post_delete` receiver on the row:

```python
@receiver(post_delete, sender=MyRow)
def _remove_files(sender, instance, **kwargs):
    shutil.rmtree(store.component_dir("my_plugin", instance.pk), ignore_errors=True)
```

Hang the row off `Experiment` with `on_delete=CASCADE` and deleting the experiment then
reaches the files with nothing further to write.

## Two things that will bite

**Your session is not finalizable.** `POST /import/uploads/<id>/finalize` refuses a session
that has a `consumer`, with a 409 naming it. That is not tidiness: such a session carries no
`import_type`, so `run_import` would fall through to auto-detect and hand your files to
whichever registered handler claimed the suffix — FASTQ reads becoming an attempted reference
import. There is deliberately no `staging/<id>/finalize`, because what happens next is exactly
what core does not know.

**Resolve the session through `staging.session_for`**, not by fetching it yourself:

```python
session, error = staging.session_for(request, upload_id, "my_plugin")
if error:
    return error
```

It answers 404 for an unknown id, 409 for a session belonging to another component or to an
ordinary import, and 403 for one belonging to another person. Written by hand, the middle one
is the check that gets left out.
