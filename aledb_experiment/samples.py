"""Editing a sample's identity.

A "sample" is an `aledb_seq.ResequencingExperiment`. Its identity -- the A/F/I/R
coordinate everything in the product labels it by -- is not stored on it. It lives in a
chain of four rows above it: AleId (A), Flask (F), Isolate (I), TechnicalReplicate (R).

**Those four rows are shared between samples, so editing a number on one is never the
right move.** `gd_import._get_or_create_chain` reuses one AleId and one Flask across every
sample under them, so `flask.flask_number = 30; flask.save()` renumbers every isolate in
that flask, not the one sample the user was looking at.

Everything here follows from doing the opposite: resolve (or create) the row for the
*target* coordinate and re-point `ResequencingExperiment.tech_rep` at it. That buys, in
order of how much each one matters:

- siblings are untouched, which is the whole point;
- `reseq.pk` never changes, so the store paths (`aledb_store/samples/<pk>/aligned.bam`)
  need no file moves -- see `aledb_common.store`;
- no transient unique_together violation is possible, because a number is only ever looked
  up, never written;
- swaps need no ordering logic. Both samples move to freshly resolved targets and the rows
  they vacated are pruned at the end, so there is no moment where two samples want one row.

The module is deliberately free of request objects: views hand it model instances and
dicts, which is what makes the collision and orphan rules testable on their own.
"""

import logging

from django.db import transaction

from aledb_experiment.models import AleId, Flask, Isolate, TechnicalReplicate

logger = logging.getLogger(__name__)

# Fields a user may change that do not affect identity. Editing one of these must not
# create a row, delete a row, or trigger a rebuild -- see `rows_are_structural`.
DESCRIPTIVE_FIELDS = ("sample_name", "person", "isolate_description",
                      "rep_description", "rep_tags")
STRUCTURAL_FIELDS = ("ale", "flask", "isolate", "rep", "is_population")

MAX_ROWS = 2000


class SampleEditError(Exception):
    """A refusal the user is meant to read.

    `errors` maps a sample id to the message about that row, so the bulk page can outline
    the offending inputs; `message` is a self-sufficient summary for pages (and for a
    future caller) that only have one place to put text.
    """

    def __init__(self, message, errors=None, status=400):
        super().__init__(message)
        self.message = message
        self.errors = errors or {}
        self.status = status


def sample_project(reseq):
    """The project a sample belongs to, or None.

    A sample knows its project only by traversal, so this is also the permission lookup:
    `can_edit_project(user, sample_project(reseq))`. Returns None for a sample whose
    `tech_rep` is null -- those are unreachable everywhere else too
    (`get_ordered_reseq_queryset` filters them out), and the views 404 rather than treat a
    sample with no project as one nobody needs permission for.
    """
    if reseq.tech_rep_id is None:
        return None
    return reseq.tech_rep.isolate.flask.ale_id.ale_experiment.project


def sample_experiment(reseq):
    if reseq.tech_rep_id is None:
        return None
    return reseq.tech_rep.isolate.flask.ale_id.ale_experiment


def sample_coordinate(reseq):
    """`(ale, flask, isolate, rep)`, or None for an unrooted sample.

    The ALE and the isolate are strings and the other two are integers -- see `AleId`.
    """
    if reseq.tech_rep_id is None:
        return None
    tech_rep = reseq.tech_rep
    isolate = tech_rep.isolate
    flask = isolate.flask
    return (flask.ale_id.ale_id, flask.flask_number,
            isolate.isolate_number, tech_rep.tech_rep_number)


def coordinate_str(coordinate):
    return "A%s F%s I%s R%s" % coordinate


def resolve_tech_rep(experiment, coordinate, *, media, freezer_box,
                     is_population, species="", strain="", description=""):
    """The TechnicalReplicate at `coordinate` within `experiment`, creating what is missing.

    Three of these four lookups differ from `gd_import._get_or_create_chain`, and each
    difference is a correction rather than a preference:

    - **Flask keys on (ale_id, flask_number) with media in `defaults`.** gd_import passes
      `media=` as a *lookup* kwarg, but Flask has `unique_together (ale_id, flask_number)`
      -- so against an existing flask carrying different media that call raises
      IntegrityError instead of returning the row. Keying on the unique tuple is the only
      form that can't.
    - **Isolate is filter().first(), not get_or_create.** Isolate has no unique_together
      and gd_import get_or_creates it on six fields including `reseq_date`, so real
      databases already hold two Isolate rows at one (flask, isolate_number).
      `get_or_create` raises MultipleObjectsReturned on that data. Lowest pk wins, which
      at least makes the choice deterministic.
    - **AleId copies species/strain but not description.** The first two are facts about
      the experiment's organism and hold across ALEs; a description is what makes *this*
      ALE different from the others, so copying it onto a new one would be a lie.
    """
    ale_number, flask_number, isolate_number, rep_number = coordinate

    ale_row, _ = AleId.objects.get_or_create(
        ale_experiment=experiment, ale_id=ale_number,
        defaults={"species": species, "strain": strain})

    flask_row, _ = Flask.objects.get_or_create(
        ale_id=ale_row, flask_number=flask_number,
        defaults={"media": media})

    isolate_row = (Isolate.objects.filter(flask=flask_row, isolate_number=isolate_number)
                   .order_by("pk").first())
    if isolate_row is None:
        isolate_row = Isolate.objects.create(
            flask=flask_row, isolate_number=isolate_number,
            is_population=is_population, freezer_box=freezer_box,
            description=description)

    tech_rep, created = TechnicalReplicate.objects.get_or_create(
        isolate=isolate_row, tech_rep_number=rep_number)
    return tech_rep, created


def prune_orphans(tech_reps):
    """Delete the rows a move emptied, bottom-up.

    Leaving them is not neutral. `aledb_dashboard.util.rebuild_sample_counts` counts
    AleId/Flask/Isolate *rows* rather than samples, so an emptied row inflates the
    dashboard's ALE and flask counts permanently; and the ALE picker in
    `aledb_seq.views.common` is built from AleId rows, so an emptied ALE would sit in the
    menu selecting nothing.

    Emptiness is re-queried here rather than taken from a snapshot made before the move:
    a swap vacates and refills the same rows, and a stale snapshot would delete a row that
    had just been filled again -- taking its samples with it, since every downward FK
    cascades.

    `Isolate` gets one extra guard. `Isolate.parent_isolate` and `AleId.starting_strain`
    are both `on_delete=DO_NOTHING`, which means Django issues the DELETE and lets the
    database reject it. Nothing in the suite ever writes either column, so an error about
    one would be unexplainable to whoever hit it: keep the row and log instead.
    """
    for tech_rep in tech_reps:
        isolate = tech_rep.isolate
        flask = isolate.flask
        ale_row = flask.ale_id

        if tech_rep.resequencingexperiment_set.exists():
            continue
        tech_rep.delete()

        if isolate.technicalreplicate_set.exists():
            continue
        if (Isolate.objects.filter(parent_isolate=isolate).exists()
                or AleId.objects.filter(starting_strain=isolate).exists()):
            logger.info("keeping empty isolate %s: still referenced as a parent or "
                        "starting strain", isolate.pk)
            continue
        isolate.delete()

        if flask.isolate_set.exists():
            continue
        flask.delete()

        if ale_row.flask_set.exists():
            continue
        ale_row.delete()


# --- parsing and validation ---------------------------------------------------------------


def _truthy(raw):
    """Whether a checkbox came back checked.

    `bool()` alone is wrong here and was: a form posts the string "0" for an unchecked
    box, and every non-empty string is truthy, so the bulk table would have flipped every
    sample it saved to population.
    """
    if isinstance(raw, str):
        return raw.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(raw)


def _positive_int(raw, label, row_label):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise SampleEditError(
            "%s: %s must be a whole number." % (row_label, label))
    if value < 0:
        # 0 is legal: aledb_experiment.common.STARTING_STRAIN_ALE_ID is "0", and the
        # starting strain is a real sample.
        raise SampleEditError("%s: %s cannot be negative." % (row_label, label))
    return value


#: An ALE or an isolate is a label, not a number (`aledb_experiment.0008`), so the only
#: things to check are that it is there and that it fits the column.
_LABEL_LIMIT = 100


def _label(raw, label, row_label):
    """One text half of a coordinate: `Ara-1`, `763A`, or plain `2`.

    Stripped, because a trailing space is invisible in the input and would make two
    coordinates that read identically point at different rows. Refused when empty for the
    same reason `_positive_int` refuses a blank: a sample has to sit somewhere.
    """
    value = ("" if raw is None else str(raw)).strip()
    if not value:
        raise SampleEditError("%s: %s is required." % (row_label, label))
    if len(value) > _LABEL_LIMIT:
        raise SampleEditError(
            "%s: %s is too long (%d characters; the limit is %d)."
            % (row_label, label, len(value), _LABEL_LIMIT))
    return value


# Column widths, so an over-long value is refused here rather than truncated silently on
# write (which is what gd_import does with its [:200] slices) or rejected by MySQL with a
# message nobody can act on. SQLite would accept any of these, so the check has to be ours.
_MAX_LENGTHS = {"sample_name": 200, "person": 200, "isolate_description": 300,
                "rep_description": 500, "rep_tags": 500}
_FIELD_LABELS = {"sample_name": "sample name", "person": "person",
                 "isolate_description": "isolate description",
                 "rep_description": "replicate description", "rep_tags": "tags"}


def _check_lengths(descriptive, row_label):
    for field, limit in _MAX_LENGTHS.items():
        if field not in descriptive:
            continue
        if len(descriptive[field]) > limit:
            raise SampleEditError(
                "%s: %s is too long (%d characters; the limit is %d)."
                % (row_label, _FIELD_LABELS[field], len(descriptive[field]), limit))


def parse_rows(rows, samples_by_id):
    """Validate every row and return [(reseq, coordinate, descriptive_dict), ...].

    Phase one of three. Nothing is written here and nothing downstream is consulted, so a
    bad row costs one dict lookup rather than a transaction.
    """
    if not isinstance(rows, list):
        raise SampleEditError("Malformed request: expected a list of rows.")
    if len(rows) > MAX_ROWS:
        raise SampleEditError(
            "Too many rows in one save (%d); the limit is %d." % (len(rows), MAX_ROWS))

    parsed = []
    errors = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SampleEditError("Malformed request: row %d is not an object." % (index + 1))
        raw_id = str(row.get("id", "")).strip()
        reseq = samples_by_id.get(raw_id)
        if reseq is None:
            # Includes an id from another experiment: samples_by_id is built from this
            # experiment's queryset, so a foreign id simply is not in it.
            raise SampleEditError(
                "Row %d refers to a sample that is not in this experiment." % (index + 1))

        row_label = "Row %d" % (index + 1)
        try:
            coordinate = (
                _label(row.get("ale"), "ALE", row_label),
                # "time point", not "flask number": the column is flask_number and stays
                # so, but that is not what anyone calls it, and a refusal is the one place
                # the internal name would surface to a user. Still the one member of the
                # coordinate that must be a number -- fixation orders ALEs by it.
                _positive_int(row.get("flask"), "time point", row_label),
                _label(row.get("isolate"), "isolate", row_label),
                _positive_int(row.get("rep"), "replicate number", row_label),
            )
        except SampleEditError as error:
            errors[raw_id] = error.message
            continue

        # Only fields the form actually sent. The bulk table has no column for the
        # replicate description or tags, and a missing key must leave the stored value
        # alone -- treating absent as empty would have the table silently blank a field
        # it does not even show.
        descriptive = {field: (row.get(field) or "").strip()
                       for field in DESCRIPTIVE_FIELDS if field in row}
        descriptive["is_population"] = _truthy(row.get("is_population"))
        try:
            _check_lengths(descriptive, row_label)
        except SampleEditError as error:
            errors[raw_id] = error.message
            continue
        parsed.append((reseq, coordinate, descriptive))

    if errors:
        raise SampleEditError(_summarise(errors, samples_by_id), errors)
    _check_names(parsed, samples_by_id)
    return parsed


def _check_names(parsed, samples_by_id):
    """Refuse a sample_name that would *newly* collide within the experiment.

    Not cosmetic. `gd_import._get_or_create_autonumbered_chain` finds an existing sample by
    `sample_name` within the experiment, so two samples sharing a name means a later
    re-import of one can silently attach to the other.

    Only a name that changed is checked. Nothing has ever stopped an import writing two
    samples with one name, so a plain "is this name taken" rule would refuse every save on
    an experiment that already had a pair -- including the save that was fixing it.
    """
    moving = {str(reseq.pk) for reseq, _, descriptive in parsed
              if descriptive.get("sample_name", reseq.sample_name) != reseq.sample_name}
    taken = {}
    for key, reseq in samples_by_id.items():
        if key in moving or not reseq.sample_name:
            continue
        taken[reseq.sample_name] = key

    errors = {}
    for reseq, _, descriptive in parsed:
        name = descriptive.get("sample_name")
        if not name or name == (reseq.sample_name or ""):
            continue
        if name in taken:
            errors[str(reseq.pk)] = (
                'A sample named "%s" already exists in this experiment.' % name)
        taken[name] = str(reseq.pk)
    if errors:
        raise SampleEditError(_summarise(errors, samples_by_id), errors)


def plan_moves(parsed, samples_by_id):
    """Phase two: work out which rows actually move, and refuse collisions.

    A target held by a sample that is *itself* moving away in this same batch is fine --
    that is exactly what a swap looks like, and refusing it would make the common bulk
    operation impossible. Only a target held by a sample staying put, or one not in this
    batch at all, is a collision.

    Two samples cannot share a coordinate. There is no database constraint saying so, but
    aledb-fixation builds `flask_isolate_mutation_dict[(flask, isolate)] = queryset` by
    plain assignment, so the second sample at a coordinate overwrites the first and its
    mutations vanish from fixation with no error anywhere.
    """
    occupants = {}
    for reseq in samples_by_id.values():
        coordinate = sample_coordinate(reseq)
        if coordinate is not None:
            occupants.setdefault(coordinate, []).append(reseq)

    vacating = set()
    for reseq, coordinate, _ in parsed:
        current = sample_coordinate(reseq)
        if current is not None and current != coordinate:
            vacating.add(reseq.pk)

    errors = {}
    claimed = {}
    for reseq, coordinate, _ in parsed:
        label = coordinate_str(coordinate)
        if coordinate in claimed and claimed[coordinate] != reseq.pk:
            errors[str(reseq.pk)] = (
                "Two rows were both given the identity %s." % label)
            continue
        claimed[coordinate] = reseq.pk

        for occupant in occupants.get(coordinate, []):
            if occupant.pk == reseq.pk or occupant.pk in vacating:
                continue
            errors[str(reseq.pk)] = (
                '%s is already "%s". Two samples cannot share one identity -- move that '
                'one first, or pick a different replicate number.'
                % (label, occupant.sample_name or occupant.pk))
    if errors:
        raise SampleEditError(_summarise(errors, samples_by_id), errors)

    return [(reseq, coordinate, descriptive)
            for reseq, coordinate, descriptive in parsed]


def _summarise(errors, samples_by_id):
    """One string that stands on its own.

    The page also gets `errors` keyed by sample id so it can outline the inputs, but the
    save is all-or-nothing, so a single readable block is genuinely enough on its own --
    and it is what shows if the shared `aledbPost` helper ever loses the response body
    again.
    """
    lines = ["Nothing was saved."]
    for key, message in errors.items():
        reseq = samples_by_id.get(key)
        name = (reseq.sample_name if reseq and reseq.sample_name else "sample %s" % key)
        lines.append("%s: %s" % (name, message))
    return "\n".join(lines)


# --- applying ------------------------------------------------------------------------------


def rows_are_structural(parsed):
    """Did anything identity-bearing actually change?

    Drives whether the rebuild hooks run. A rename must not pay for a fixation rebuild.
    """
    for reseq, coordinate, descriptive in parsed:
        if sample_coordinate(reseq) != coordinate:
            return True
        if reseq.tech_rep_id and \
                bool(reseq.tech_rep.isolate.is_population) != descriptive["is_population"]:
            return True
    return False


def _write(instance, mapping, descriptive, always):
    """Save only the columns this form sent, plus `always`.

    `update_fields` is built from what arrived rather than from the full column list, so a
    form with no input for a field cannot overwrite it -- see the note in `parse_rows`.
    """
    fields = list(always)
    for column, key in mapping.items():
        if key in descriptive:
            setattr(instance, column, descriptive[key])
            fields.append(column)
    if fields:
        instance.save(update_fields=fields)


@transaction.atomic
def apply_rows(experiment, parsed, *, media, freezer_box):
    """Phase three: write everything, or nothing.

    All-or-nothing because a bulk renumber is usually a permutation, and half a swap is a
    state the user can neither reason about nor undo from the page. It also means the
    rebuild hooks never run over a half-applied experiment.

    Returns the sample ids that were touched.
    """
    vacated = []
    touched = []

    for reseq, coordinate, descriptive in parsed:
        current = sample_coordinate(reseq)
        source_tech_rep = reseq.tech_rep

        if current != coordinate:
            source_isolate = source_tech_rep.isolate if source_tech_rep else None
            source_flask = source_isolate.flask if source_isolate else None
            source_ale = source_flask.ale_id if source_flask else None

            # A renumber re-labels a sample; it does not move it to different growth
            # conditions or a different freezer. Inheriting these is the only answer that
            # does not silently reset real data -- the placeholders are the fallback for a
            # sample with nothing to inherit from.
            tech_rep, created = resolve_tech_rep(
                experiment, coordinate,
                media=source_flask.media if source_flask else media,
                freezer_box=source_isolate.freezer_box if source_isolate else freezer_box,
                is_population=descriptive["is_population"],
                species=source_ale.species if source_ale else "",
                strain=source_ale.strain if source_ale else "",
                description=source_isolate.description if source_isolate else "")
            if created and source_tech_rep is not None:
                # Same rule as media and the freezer box: a renumber re-labels a sample,
                # so what was written about this run travels with it onto a row that did
                # not exist a moment ago. An existing target keeps its own text -- it
                # describes other samples too.
                tech_rep.description = source_tech_rep.description
                tech_rep.tags = source_tech_rep.tags
                tech_rep.save(update_fields=["description", "tags"])
            reseq.tech_rep = tech_rep
            if source_tech_rep is not None:
                vacated.append(source_tech_rep)
        else:
            tech_rep = source_tech_rep

        _write(reseq, {"sample_name": "sample_name", "person": "person"},
               descriptive, ["tech_rep"])

        isolate = tech_rep.isolate
        isolate.is_population = descriptive["is_population"]
        _write(isolate, {"description": "isolate_description"},
               descriptive, ["is_population"])

        _write(tech_rep, {"description": "rep_description", "tags": "rep_tags"},
               descriptive, [])

        touched.append(reseq.pk)

    prune_orphans(vacated)
    return touched


def rebuild_after_structural_change(experiment):
    """Recompute only what a renumber can actually have changed.

    Fixation reads the numbers directly -- it sorts flask numbers and takes the last two to
    decide what counts as fixed -- so it has to rebuild. Sample counts are counts of
    AleId/Flask/Isolate *rows*, which this module creates and prunes.

    Deliberately not `gd_import.run_post_processing`: it lives in aledb_import, so calling
    it would point aledb_experiment at the import app, and it asks for every registered
    rebuild rather than the two a renumber can change.

    Deliberately not `mutation_counts`: it pulls every ObservedMutation in the database into
    Python. Nothing about a renumber changes a mutation count, and paying for the whole
    database on every rename is the one thing here that could make the feature feel broken in
    production. That refusal is what `only=` says -- it names what a renumber can change, and
    everything it does not name is left alone.

    **This list has emptied out from the other end.** `overview`, `aledb_converge` and
    `aledb_fixation` were all named here, and none of them stores anything now -- the
    Overview's counts, the convergent set and the fixated set are each computed by the request
    that renders them, so a renumber has nothing of theirs to mark. What is left is
    `sample_counts`, which counts the AleId/Flask/Isolate rows this module creates and prunes.

    Naming a plugin from here was always safe in itself: `get_rebuilders` skips a name nothing
    registered, so a deployment without the plugin simply had less to do. That property still
    holds and is still tested; it just no longer has a caller in core relying on it.
    """
    from aledb_common.rebuild_registry import request_rebuild, run_rebuilds

    changed = ('sample_counts',)
    # Marked but not run: `aledb_phylogeny` stores a rendered "A1 F1500 I1 R1" per tip, so a
    # renumber leaves its tree drawing labels that are now wrong -- but nobody asked for
    # anything to happen to a tree by renaming a sample, so this marks and stops.
    #
    # What the mark now costs that plugin is a DELETE rather than an inference: it registers a
    # discard, and its page calls `ensure_fresh` on the way in, so the first reader after a
    # renumber finds the tree gone and is offered a new one. Leaving it out of `run_rebuilds`
    # below is therefore about *where* that happens rather than about expense -- this call site
    # is renaming samples and has no business inferring anything.
    request_rebuild(experiment.ale_id, only=changed + ('aledb_phylogeny',),
                    reason='samples renumbered')
    run_rebuilds(experiment.ale_id, only=changed)
