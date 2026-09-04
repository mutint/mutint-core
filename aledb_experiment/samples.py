"""Editing a sample's identity.

A "sample" is an `aledb_seq.Sample`. Its identity is the population / time point / label
coordinate
everything in the product labels it by, and **half of it is stored on the sample and half
is not**. The label is the sample's own column; the population and the time point live in
two rows above it that it shares with its siblings.

That split is what this module is about, and it used to be a four-row chain -- Population,
TimePoint, Isolate, TechnicalReplicate -- of which the last two are now the sample itself.

**The shared rows must never be edited in place.** `gd_import._get_or_create_chain` reuses
one Population and one TimePoint across every sample under them, so `time_point.value = 30;
time_point.save()` renumbers every sample at that time point, not the one the user was
looking at.

Everything here follows from doing the opposite: resolve (or create) the row for the
*target* coordinate and re-point `Sample.time_point` at it. That buys, in
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

from aledb_experiment import coordinates
from aledb_experiment.models import Population, TimePoint

logger = logging.getLogger(__name__)

# Fields a user may change that do not affect identity. Editing one of these must not
# create a row, delete a row, or trigger a rebuild -- see `rows_are_structural`.
DESCRIPTIVE_FIELDS = ("sample_name", "person", "isolate_description",
                      "medium_description", "rep_tags")
# The form's field names, which are the query-string vocabulary and change with it rather
# than with the columns behind them.
STRUCTURAL_FIELDS = ("ale", "flask", "isolate", "is_mixed")

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
    `time_point` is null -- those are unreachable everywhere else too
    (`get_ordered_reseq_queryset` filters them out), and the views 404 rather than treat a
    sample with no project as one nobody needs permission for.
    """
    if reseq.time_point_id is None:
        return None
    return reseq.time_point.population.experiment.project


def sample_experiment(reseq):
    if reseq.time_point_id is None:
        return None
    return reseq.time_point.population.experiment


def sample_coordinate(reseq):
    """`(population, time_point, label)`, or None for an unrooted sample.

    The population and the label are strings and the time point is an integer -- see
    `Population`.

    **It was a 4-tuple** ending in the replicate number. The replicate is part of the label
    now (`1-2` rather than `1` with an `R2` beside it), so anything unpacking this into four
    names is a compile error rather than a silently short coordinate.
    """
    if reseq.time_point_id is None:
        return None
    time_point = reseq.time_point
    return (time_point.population.name, time_point.value, reseq.name)


def coordinate_str(coordinate):
    """The tuple `sample_coordinate` answers, written the one way it is written.

    This was the second copy of the format string; `aledb_experiment.coordinates` is the
    first and only one now.
    """
    return coordinates.format_coordinate(*coordinate)


def resolve_time_point(experiment, coordinate, *, media, species="", strain=""):
    """The TimePoint at `coordinate` within `experiment`, creating what is missing.

    It used to resolve four rows and return the last one. Two of those rows are the sample
    itself now, so it resolves two and returns the time point -- the caller re-points
    `reseq.time_point` at it and writes the label straight onto the sample.

    **That deleted the subtlest paragraph in this module.** Isolate had no unique_together
    and gd_import get_or_created it on six fields including `sequencing_date`, so real databases
    held two Isolate rows at one (time point, label) and `get_or_create` raised
    MultipleObjectsReturned on them. This resolved it with `filter().order_by("pk").first()`
    -- lowest pk wins, deterministic if arbitrary. There is no such row to be ambiguous
    about any more: the label is a column on the sample, and two samples sharing a
    coordinate is what `plan_moves` refuses.

    Two differences from `gd_import._get_or_create_chain` remain, and each is a correction
    rather than a preference:

    - **TimePoint keys on (population, value) with media in `defaults`.** gd_import passes
      `media=` as a *lookup* kwarg, but TimePoint is unique on (population, value) -- so
      against an existing time point carrying different media that call raises
      IntegrityError instead of returning the row. Keying on the unique tuple is the only
      form that can't.
    - **Population copies species/strain but not description.** The first two are facts about
      the experiment's organism and hold across ALEs; a description is what makes *this*
      ALE different from the others, so copying it onto a new one would be a lie.
    """
    population_name, time_point_value, _label = coordinate

    population, _ = Population.objects.get_or_create(
        experiment=experiment, name=population_name,
        defaults={"species": species, "strain": strain})

    time_point, _ = TimePoint.objects.get_or_create(
        population=population, value=time_point_value,
        defaults={"media": media})
    return time_point


def prune_orphans(time_points):
    """Delete the rows a move emptied, bottom-up.

    Leaving them is not neutral. `aledb_dashboard.util.rebuild_sample_counts` counts
    Population/TimePoint *rows* rather than samples, so an emptied row inflates the
    dashboard's counts permanently; and the population picker in `aledb_seq.views.common`
    is built from Population rows, so an emptied one would sit in the menu selecting
    nothing.

    Emptiness is re-queried here rather than taken from a snapshot made before the move:
    a swap vacates and refills the same rows, and a stale snapshot would delete a row that
    had just been filled again -- taking its samples with it, since every downward FK
    cascades.

    **Two of the four levels have gone**, so this walks time point then population rather
    than replicate, isolate, flask, ALE. The two guards it dropped were the ones that could not
    be got wrong; the two left are the ones that share rows between samples.

    `Isolate.parent_isolate` was guarded here, and both are gone: nothing in the suite ever
    wrote that column, so the guard protected a state no import or edit could produce.

    `Population.starting_strain` was guarded here too. It is gone -- it was a second, never
    written spelling of the ancestor, which is now one designation on the experiment; see
    `aledb_experiment/ancestor.py`.
    """
    for time_point in time_points:
        population = time_point.population

        if time_point.sample_set.exists():
            continue
        time_point.delete()

        if population.timepoint_set.exists():
            continue
        population.delete()


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


#: An ALE or a sample label is a label, not a number (`aledb_experiment.0008`), so the only
#: things to check are that it is there and that it fits the column.
_LABEL_LIMIT = 100


def _label(raw, label, row_label):
    """One text part of a coordinate: `Ara-1`, `763A`, `1-2`, or plain `2`.

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
                "medium_description": 500, "rep_tags": 500}
_FIELD_LABELS = {"sample_name": "sample name", "person": "person",
                 "isolate_description": "description",
                 "medium_description": "medium description", "rep_tags": "tags"}


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
                # "time point" is what the form field is called; the column is
                # so, but that is not what anyone calls it, and a refusal is the one place
                # the internal name would surface to a user. Still the one member of the
                # coordinate that must be a number -- fixation orders ALEs by it.
                _positive_int(row.get("flask"), "time point", row_label),
                # One label where there were two. A replicate was never a level of
                # anything -- `1-2` is what the sample is called, and the form has one box
                # for it.
                _label(row.get("isolate"), "sample label", row_label),
            )
        except SampleEditError as error:
            errors[raw_id] = error.message
            continue

        # Only fields the form actually sent. The bulk table has no column for the medium
        # description or the tags, and a missing key must leave the stored value alone --
        # treating absent as empty would have the table silently blank a field it does not
        # even show.
        descriptive = {field: (row.get(field) or "").strip()
                       for field in DESCRIPTIVE_FIELDS if field in row}
        # The form asks the mixed question, because that is the box a person ticks. The
        # column stores the clonal one. The negation happens once, in `apply_rows`.
        descriptive["is_mixed"] = _truthy(row.get("is_mixed"))
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
              if descriptive.get("sample_name", reseq.source_name) != reseq.source_name}
    taken = {}
    for key, reseq in samples_by_id.items():
        if key in moving or not reseq.source_name:
            continue
        taken[reseq.source_name] = key

    errors = {}
    for reseq, _, descriptive in parsed:
        name = descriptive.get("sample_name")
        if not name or name == (reseq.source_name or ""):
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
    aledb-fixation builds `flask_isolate_mutation_dict[(time_point, label)] = queryset` by
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
                'one first, or give this one a different label.'
                % (label, occupant.source_name or occupant.pk))
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
        name = (reseq.source_name if reseq and reseq.source_name else "sample %s" % key)
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
        if bool(reseq.is_mixed) != descriptive["is_mixed"]:
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
def apply_rows(experiment, parsed, *, media):
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
        source_time_point = reseq.time_point
        always = ["is_clonal"]

        if current != coordinate:
            source_population = (source_time_point.population
                                 if source_time_point else None)

            # A renumber re-labels a sample; it does not move it to different growth
            # conditions. Inheriting these is the only answer that does not silently reset
            # real data -- the placeholder is the fallback for a sample with nothing to
            # inherit from. (There was a freezer box here too; it was a required FK to a
            # singleton row nothing displayed, and it is gone.)
            reseq.time_point = resolve_time_point(
                experiment, coordinate,
                media=source_time_point.media if source_time_point else media,
                species=source_population.species if source_population else "",
                strain=source_population.strain if source_population else "")
            reseq.name = coordinate[2]
            always += ["time_point", "name"]
            if source_time_point is not None:
                vacated.append(source_time_point)

        # One row written where there were three. The description and the tags used to
        # belong to the isolate and the replicate, which were *shared* -- so a move had to
        # decide whether they travelled with the sample or stayed with the row, and the
        # answer ("travel, but only onto a row that did not exist a moment ago") was three
        # paragraphs of comment. They are the sample's own columns now, so they simply move
        # with it and there is nothing left to decide.
        reseq.is_clonal = not descriptive["is_mixed"]
        _write(reseq, {"source_name": "sample_name", "person": "person",
                       "description": "isolate_description",
                       "medium_description": "medium_description", "tags": "rep_tags"},
               descriptive, always)

        touched.append(reseq.pk)

    prune_orphans(vacated)
    return touched


def rebuild_after_structural_change(experiment):
    """Recompute only what a renumber can actually have changed.

    Fixation reads the numbers directly -- it sorts time points and takes the last two to
    decide what counts as fixed -- so it has to rebuild. Sample counts are counts of
    Population/TimePoint *rows*, which this module creates and prunes.

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
    `sample_counts`, which counts the Population/TimePoint rows this module creates and prunes.

    Naming a plugin from here was always safe in itself: `get_rebuilders` skips a name nothing
    registered, so a deployment without the plugin simply had less to do. That property still
    holds and is still tested; it just no longer has a caller in core relying on it.
    """
    from aledb_common.rebuild_registry import request_rebuild, run_rebuilds

    changed = ('sample_counts',)
    # Marked but not run: `aledb_phylogeny` stores a rendered "A1 F1500 I1-1" per tip, so a
    # renumber leaves its tree drawing labels that are now wrong -- but nobody asked for
    # anything to happen to a tree by renaming a sample, so this marks and stops.
    #
    # What the mark now costs that plugin is a DELETE rather than an inference: it registers a
    # discard, and its page calls `ensure_fresh` on the way in, so the first reader after a
    # renumber finds the tree gone and is offered a new one. Leaving it out of `run_rebuilds`
    # below is therefore about *where* that happens rather than about expense -- this call site
    # is renaming samples and has no business inferring anything.
    request_rebuild(experiment.id, only=changed + ('aledb_phylogeny',),
                    reason='samples renumbered')
    run_rebuilds(experiment.id, only=changed)
