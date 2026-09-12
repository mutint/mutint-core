"""Registry of reference annotators: things a component runs *on* a reference genome.

Apps call `register_reference_annotator()` from their `AppConfig.ready()`. The Import data
page's two reference tabs -- Reference Sequence and Update Annotation -- draw a panel per
annotator with an enable box, collect its options when the form is submitted, and run it after
the reference lands if it was ticked; the Update Annotation tab also offers **Run annotators**
against the stored reference with nothing uploaded. This is the tenth registry, and the first
whose contribution is a *step*: something that happens to the reference after it is stored,
rather than a page, a link, a handler or a panel of content.

**Why this exists.** A reference is imported once and its annotation can be replaced by
dropping another file, and until this nothing could run on it afterwards. The case that asked
for it is ISEScan: breseq's own manual recommends predicting IS elements on the reference and
merging them in with `CONVERT-REFERENCE -s`, so an IS insertion is called as one MOB rather
than two junctions. That is an annotation *derived* from the sequence by a tool, and a tool
that takes minutes and belongs on the worker -- neither a file to drop nor an import type.

**An annotator writes through `mutint_import.annotation.install_annotation`**, the one
function that stores a new annotation for the same sequence and then re-annotates every
mutation and rebuilds what derives from them. The plain Update Annotation drop writes through
it too. What an annotator may not do is write the store itself: the reference is one artifact
with one digest, and two writers would race.

**`run` returns a message, and is expected to return quickly.** The contract is
`run(experiment, options, user) -> dict`, JSON-safe, with a `message` a person reads on the
import summary. A tool that takes minutes enqueues a job through `mutint_jobs` and returns
"queued as job N" -- the shape mutint-isescan takes -- rather than running inline in the
request that finalized an upload. A task that then calls `install_annotation` holds the
import lock through `import_lock.hold_waiting`; see `docs/plugin/integrating.md`.

**Panels are body-only templates and options travel by input name.** Core draws the fieldset
and the enable checkbox; the template supplies the controls, each with a `name=`, and the page
collects them as `{name: value}` -- a checkbox as a boolean, everything else as its string.
`enabled` is reserved for the box core draws. `clean(options)` is where a component says what
those strings mean and refuses what it cannot use, with a `ValueError` the page shows beside
the form before anything is uploaded.

**Failures are isolated, twice.** A panel whose context or template raises is dropped with a
logged warning and the page renders, the posture `panel_registry` takes. An annotator whose
`run` raises is recorded as an error on its own row of the summary and the rest still run --
the reference is already installed by then, and one component's fault must not read as a
failed import.

**Keyed by name alone.** The page's payload and the fieldset are keyed by it, so two apps
registering one name would be indistinguishable there; re-registering replaces, so a reload
cannot double a panel. Ordering is registration order, which is INSTALLED_APPS order.
"""

import logging

logger = logging.getLogger(__name__)

#: [{'app', 'name', 'label', 'description', 'template', 'context', 'clean', 'run'}], in
#: registration order.
_annotators = []

#: The one option key core owns. It is the fieldset's enable box, read by `clean_selections`
#: and stripped before a component's `clean` sees the rest.
ENABLED = "enabled"


class AnnotatorOptionsInvalid(ValueError):
    """The selection payload cannot be used: an unknown annotator, a shape that is not a
    mapping, or options a component's `clean` refused. The message is for a person, and the
    caller answers 400 -- before anything has been uploaded, when the refusal is cheap."""


class NoReference(Exception):
    """The experiment has no reference to run anything on. The caller answers 409."""


def register_reference_annotator(app_config, name, label, run, template, context=None,
                                 clean=None, description=""):
    """Register a reference annotator (from `AppConfig.ready()`).

    app_config  the AppConfig itself, i.e. `self` at the call site. Kept so a warning about a
                broken panel can name the app it came from.
    name        stable slug, unique across the installation: it keys the page's payload and
                the fieldset. Registering the same name twice replaces the first.
    label       what the enable box and the summary row call it, e.g. 'ISEScan IS elements'.
    run         callable(experiment, options, user) -> dict, JSON-safe. Its `message` is what
                the summary shows; any other keys are the component's own. Expected to return
                promptly -- enqueue a job for anything that takes minutes -- and to write the
                annotation through `mutint_import.annotation.install_annotation`.
    template    template name for the panel body, e.g. 'isescan/panel.html'. Body only: core
                draws the fieldset, the enable box and the description.
    context     optional callable(experiment, request) -> dict for the template.
    clean       optional callable(options) -> options. Receives the panel's inputs as
                `{name: value}` with `enabled` already stripped, returns what `run` will be
                handed. A `ValueError` is shown to the person as a refusal of the form.
    description a sentence under the enable box saying what the annotator does.

    Returns the name it registered under, so a test has something to unregister.
    """
    entry = {'app': app_config.name, 'name': name, 'label': label,
             'description': description, 'template': template, 'context': context,
             'clean': clean, 'run': run}
    for index, existing in enumerate(_annotators):
        if existing['name'] == name:
            _annotators[index] = entry
            return name
    _annotators.append(entry)
    return name


def unregister_reference_annotator(name):
    """Remove a registered annotator. For tests; nothing in the product unregisters."""
    global _annotators
    _annotators = [entry for entry in _annotators if entry['name'] != name]


def get_reference_annotators():
    """Every registered annotator, in registration order. Metadata and callables."""
    return list(_annotators)


def _entry(name):
    for entry in _annotators:
        if entry['name'] == name:
            return entry
    return None


def render_annotator_panels(experiment, request):
    """Render every annotator's panel body for `experiment`.

    Returns [{'name', 'label', 'description', 'html'}, ...] in registration order, omitting
    any panel that raised. `annotator` (the name) is in every panel's context, so a template
    can build its own ids and endpoints from it; `request` is passed so context processors
    run and the panel sees what the page around it does.
    """
    from django.template.loader import render_to_string

    rendered = []
    for entry in _annotators:
        try:
            context = {'annotator': entry['name']}
            if entry['context'] is not None:
                context.update(entry['context'](experiment, request) or {})
            html = render_to_string(entry['template'], context, request=request)
        except Exception:
            # The page will simply be missing a panel, which nobody can work backwards from;
            # the app and the name are what say whose.
            logger.exception("annotator panel %s.%s failed; skipping it",
                             entry['app'], entry['name'])
            continue
        rendered.append({'name': entry['name'], 'label': entry['label'],
                         'description': entry['description'], 'html': html})
    return rendered


def clean_selections(raw):
    """The page's `annotators` payload as `{name: options}`, ticked entries only.

    `raw` is `{name: {enabled: bool, <input name>: value, ...}}` as the page collects it.
    Entries whose `enabled` is false are dropped; the rest have `enabled` stripped and go
    through the component's `clean`. Returned in registry order rather than payload order, so
    what runs first is what registered first, whatever the client sent.

    Raises `AnnotatorOptionsInvalid` for a payload that is not a mapping, an unknown name, or
    options a `clean` refused with `ValueError` -- naming the annotator's label, since the
    person is looking at a form. Anything else `clean` raises propagates: a bug in a
    component's `clean` is a 500, and nothing has been uploaded yet.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise AnnotatorOptionsInvalid("The annotator selections could not be read.")
    for name, value in raw.items():
        if _entry(name) is None:
            raise AnnotatorOptionsInvalid("%s is not a registered annotator." % (name,))
        if not isinstance(value, dict):
            raise AnnotatorOptionsInvalid(
                "The options for %s could not be read." % (name,))

    selections = {}
    for entry in _annotators:
        value = raw.get(entry['name'])
        if not value or not value.get(ENABLED):
            continue
        options = {key: item for key, item in value.items() if key != ENABLED}
        if entry['clean'] is not None:
            try:
                options = entry['clean'](options)
            except ValueError as refusal:
                raise AnnotatorOptionsInvalid("%s: %s" % (entry['label'], refusal))
        selections[entry['name']] = options
    return selections


def run_annotators(experiment, selections, user):
    """Run each selected annotator against `experiment`'s stored reference.

    `selections` is `clean_selections`' answer. Returns one row per annotator, in registry
    order: its `run`'s dict plus `name` and `label`, with `message` defaulted to "" and
    `error` to None -- a `run` may set `error` itself to decline without it being a fault --
    and for one that raised, `{'name', 'label', 'message': '', 'error': str(exc)}`, logged,
    with the rest still run. Rows must be JSON-safe; they go into the
    import summary and are echoed back to the page.

    Raises `NoReference` before running anything when the experiment has no reference:
    both callers -- the finalize path and the Run annotators button -- pass through here, so
    the refusal lives here rather than in each.
    """
    from mutint_import.reference_store import has_reference

    if not has_reference(experiment):
        raise NoReference("This experiment has no reference genome to annotate.")

    rows = []
    for entry in _annotators:
        if entry['name'] not in selections:
            continue
        row = {'name': entry['name'], 'label': entry['label'], 'message': '', 'error': None}
        try:
            result = entry['run'](experiment, selections[entry['name']], user) or {}
            # A component may decline in an ordinary way -- the tool is not installed, a run
            # is already under way -- by returning `error` itself; that is a refusal rather
            # than a fault, so it is not logged as one.
            row.update(result)
            row.setdefault('message', '')
            row.setdefault('error', None)
        except Exception as exc:  # noqa: BLE001 -- one annotator must not fail the rest
            logger.exception("reference annotator %s.%s failed for experiment %s",
                             entry['app'], entry['name'], experiment.id)
            row['error'] = str(exc)
        rows.append(row)
    return rows
