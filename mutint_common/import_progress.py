"""Per-unit progress out of a running import.

``import_registry.run_import`` calls each handler **once** with every file it claimed, and the
per-sample loop lives inside the handler. So the only code that knows a sample has finished is
the handler's own loop, and the only thing that wants to know is a view three layers above it.
This module is the side channel between them.

Nothing is installed by default, so every function here is a no-op unless somebody is
listening. That is what lets ``./mutint import``, ``./mutint load_example`` and every existing
test call ``run_import`` unchanged -- a reporting call in the import path costs them an
attribute lookup and a ``None`` check.

    with reporting(sink):
        run_import(...)

The sink is a callable taking one JSON-safe event dict. `run_import` announces the whole unit
list before any handler runs; each handler then brackets its own work::

    announce(["sample-a", "sample-b"])
    begin("sample-a"); ...; report({"file": "sample-a", "mutations": 523, ...})
    begin("sample-b"); ...; report({"file": "sample-b", "mutations": 0, "error": "..."})
    stage("Recomputing derived data...")

**A unit's announced name must equal the ``file`` key its eventual result carries**, or a
reader pairing the two has nothing to pair on. The handlers do not agree on what that name is
-- ``breseq_folder`` reports a directory basename, ``genomediff`` a filename, the reference
handlers a path relative to the drop root -- so each handler's ``list_units`` mirrors its own
reporting rather than there being one rule. ``mutint_import.tests.test_import_progress`` pins
each of them.

**``begin`` and ``report`` carry an index this module assigns**, counting from zero in call
order, and a reader is meant to key on it rather than on the name. Two sample directories with
the same basename at different depths are a thing ``breseq_folder.find_sample_dirs`` can
return, since it walks; an index cannot collide.

A handler that reports nothing is not broken. Its units stay unstarted and its rows arrive in
the final summary the way they always have -- which is what keeps this optional for a plugin's
import type, the same way ``accepts_options`` keeps the fifth handler argument optional.
"""

import contextvars
import logging
from contextlib import contextmanager

logger = logging.getLogger("mutint_common.import_progress")

# Not threading.local(): a ContextVar is correct under threads and also under async, and the
# WSGI streaming response this exists for iterates its generator on the request's own thread.
_sink = contextvars.ContextVar("mutint_import_progress_sink", default=None)


class _Reporter:
    """Holds the callback and the index it stamps each unit event with."""

    def __init__(self, callback):
        self.callback = callback
        self.index = 0

    def emit(self, event):
        try:
            self.callback(event)
        except Exception:
            # A progress channel must never be able to fail an import. The mutations are the
            # point; the commentary is not, and by the time a sample is reported its
            # transaction has already committed.
            logger.exception("import progress sink raised; continuing the import")


@contextmanager
def reporting(callback):
    """Install `callback` as the progress sink for the duration of the block."""
    token = _sink.set(_Reporter(callback))
    try:
        yield
    finally:
        _sink.reset(token)


def announce(units):
    """Declare every unit this import will process, in the order it will process them."""
    reporter = _sink.get()
    if reporter is None:
        return
    reporter.emit({"event": "total", "units": list(units)})


def advance_to(index):
    """Put the cursor at `index`, which is where the next `report` will land.

    Called by `run_import` before it hands control to each handler, so a handler's rows go
    to that handler's own slice of the announced list. Without it the cursor would simply
    count `report` calls, and a handler that reported *nothing* -- which a plugin's is free
    to do -- would silently shift every row after it up by its own unit count.
    """
    reporter = _sink.get()
    if reporter is None:
        return
    reporter.index = index


def begin(name):
    """This unit is starting. Does not consume an index -- `report` does."""
    reporter = _sink.get()
    if reporter is None:
        return
    reporter.emit({"event": "begin", "index": reporter.index, "file": name})


def report(result):
    """This unit finished, carrying the {file, mutations, error, warnings} record."""
    reporter = _sink.get()
    if reporter is None:
        return
    event = dict(result)
    event["event"] = "file"
    event["index"] = reporter.index
    reporter.index += 1
    reporter.emit(event)


def stage(message):
    """A phase that is not a unit -- the derived-data rebuild after the last sample.

    Worth its own event because it is the longest silence in an import: every registered
    rebuild runs there, the dashboard's installation-wide totals included, after the last row
    has already been filled in.
    """
    reporter = _sink.get()
    if reporter is None:
        return
    reporter.emit({"event": "stage", "message": message})


def is_reporting():
    """Whether anything is listening. For tests; the report functions check for themselves."""
    return _sink.get() is not None
