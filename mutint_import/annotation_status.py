"""Whether an annotation job is in flight for one experiment, and what to say about it.

One function, because three surfaces ask the same question and must not be able to disagree
about the answer: the Import data page's view, the `{% import_tabs %}` tag that draws the
panel on every tab -- core's and plugins' alike -- and the endpoint that panel polls.

**Why this exists at all.** A reference annotator that takes minutes enqueues a job and returns
a sentence, and until this the sentence was the whole of the feedback: the person was told to
go and find `/jobs/`, from a page that was about to reload onto a different tab. Worse, nothing
stopped them importing sequencing results in the meantime -- and those results are wrong in a
way nothing repairs. Re-annotation fixes gene names; it cannot turn the two junction calls a
breseq run made against the un-annotated reference into the one MOB that running ISEScan was
for.

**It lives in `mutint_import` and is not a registry.** The question it answers is the Import
page's -- *may I drop something now* -- rather than anything a component contributes to. Nobody
registers anything here and nobody enumerates components, which is the same reason `staging.py`
is not a registry either.

**The import lock is global; this panel is per-experiment.** An annotation job on experiment A
can still 409 a finalize on experiment B, and that is not a gap to be closed here: a banner on
B naming A's job would be noise on every import page in the deployment, and `/jobs/` is already
the installation-wide view. The block this feeds is about *this* experiment's annotation.
"""

from mutint_jobs import jobs


def status_for(experiment, user):
    """`{"busy", "stalled", "jobs"}` for `experiment`, as `user` is allowed to see it.

    `jobs` is one row per annotation job still in flight, newest first, through
    `mutint_jobs.jobs.row` -- so a viewer who did not start the job sees that it exists, its
    label, its status and whose it is, and gets neither a log link nor a Cancel button. That
    is a deliberate widening of `may_view`'s rule and is argued where the rule is: standing to
    read a job's *log* comes from having asked for the job, but a control switched off in
    front of somebody owes them a reason, and a disabled button with an empty panel beside it
    is the dead end this repo refuses.

    `busy` is simply whether there are any, and is therefore the queue's answer rather than
    any component's claim -- see `jobs.annotation_jobs`.

    `stalled` is `jobs.stalled_since()`, the same hedge `/jobs/` shows, and it earns its place
    here more than it does there: this panel switches a button off, so when nothing is going to
    finish it owes the reader that fact and the Cancel button that is the way out.
    """
    rows = [jobs.row(job, user=user) for job in jobs.annotation_jobs(experiment)]
    return {
        "busy": bool(rows),
        "stalled": jobs.stalled_since() if rows else None,
        "jobs": rows,
    }
