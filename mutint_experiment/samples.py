"""Editing a sample's identity.

A "sample" is an `mutint_sample.Sample`. Its identity is the population / time point / label
coordinate
everything in the product labels it by, and **half of it is stored on the sample and half
is not**. The label is the sample's own column; the population and the time point live in
two rows above it that it shares with its siblings.

That split is what this module is about, and it used to be a four-row chain -- Population,
Isolate, TechnicalReplicate -- of which the last two are now the sample itself.

**The shared row must never be edited in place.** `gd_import._get_or_create_chain` reuses
one Population across every sample under it, so `population.name = "Ara-2";
population.save()` renumbers every sample in that lineage, not the one the user was looking
at. There used to be a second such row -- `TimePoint`, shared by every sample at one point
-- and the same hazard: `time_point.value = 30` moved all of them. That one is a column on
the sample now, so it is simply assigned, and only the population is still shared.

Everything here follows from doing the opposite for the row that remains: resolve (or
create) the Population for the *target* coordinate and re-point `Sample.population` at it.
That buys, in order of how much each one matters:

- siblings are untouched, which is the whole point;
- `sample.pk` never changes, so the store paths (`mutint_store/samples/<pk>/aligned.bam`)
  need no file moves -- see `mutint_common.store`;
- no transient unique_together violation is possible, because a number is only ever looked
  up, never written;
- swaps need no ordering logic. Both samples move to freshly resolved targets and the rows
  they vacated are pruned at the end, so there is no moment where two samples want one row.

The module is deliberately free of request objects: views hand it model instances and
dicts, which is what makes the collision and orphan rules testable on their own.
"""

import logging

from django.db import transaction

from mutint_experiment import coordinates
from mutint_experiment.models import Population
from mutint_sample.flags import FLAG_FIELDS

logger = logging.getLogger(__name__)

# Fields a user may change that do not affect identity. Editing one of these must not
# create a row, delete a row, or trigger a rebuild -- see `rows_are_structural`.
DESCRIPTIVE_FIELDS = ("source_name", "description", "medium_description")
# The form's field names, which are the query-string vocabulary and change with it rather
# than with the columns behind them.
STRUCTURAL_FIELDS = ("population", "time_point", "name", "is_mixed")

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


def sample_project(sample):
    """The project a sample belongs to, or None.

    A sample knows its project only by traversal, so this is also the permission lookup:
    `can_edit_project(user, sample_project(sample))`. Returns None for a sample whose
    `population` is null -- those are unreachable everywhere else too
    (`get_ordered_sample_queryset` filters them out), and the views 404 rather than treat a
    sample with no project as one nobody needs permission for.
    """
    if sample.population_id is None:
        return None
    return sample.population.experiment.project


def sample_experiment(sample):
    if sample.population_id is None:
        return None
    return sample.population.experiment


def sample_coordinate(sample):
    """`(population, time_point, label)`, or None for an unrooted sample.

    The population and the label are strings and the time point is a float -- see
    `Sample.time_point`.

    **It was a 4-tuple** ending in the replicate number. The replicate is part of the label
    now (`1-2` rather than `1` with an `R2` beside it), so anything unpacking this into four
    names is a compile error rather than a silently short coordinate.
    """
    if sample.population_id is None:
        return None
    return (sample.population.name, sample.time_point, sample.name)


def coordinate_str(coordinate):
    """The tuple `sample_coordinate` answers, written the one way it is written.

    This was the second copy of the format string; `mutint_experiment.coordinates` is the
    first and only one now.
    """
    return coordinates.format_coordinate(*coordinate)


def resolve_population(experiment, coordinate, *, species="", strain=""):
    """The Population at `coordinate` within `experiment`, creating it if it is missing.

    It used to resolve four rows and return the last one, then two and return the time
    point. Three of those four are the sample itself now -- the isolate and the replicate
    were merged into it, and the time point became a column on it -- so one row is left and
    the caller writes the rest straight onto the sample.

    **Two paragraphs of hazard went with those rows**, and neither can come back:

    - `Isolate` had no `unique_together` while `gd_import` get_or_created it on six fields
      including `sequencing_date`, so real databases held two rows at one (time point,
      label) and `get_or_create` raised `MultipleObjectsReturned`. This resolved it with
      `filter().order_by("pk").first()` -- lowest pk wins, deterministic if arbitrary.
    - `TimePoint` was unique on (population, value) while `gd_import` passed `media=` as a
      *lookup* kwarg, so against an existing time point carrying different media that call
      raised `IntegrityError` rather than returning the row. `Media` is gone and so is the
      row it hung on.

    One difference from `gd_import._get_or_create_chain` remains, and it is a correction
    rather than a preference: **Population copies species/strain but not description.** The
    first two are facts about the experiment's organizm and hold across ALEs; a description
    is what makes *this* ALE different from the others, so copying it onto a new one would
    be a lie.
    """
    population_name, _time_point, _label = coordinate

    population, _ = Population.objects.get_or_create(
        experiment=experiment, name=population_name,
        defaults={"species": species, "strain": strain})
    return population


def prune_orphans(populations):
    """Delete the rows a move emptied.

    Leaving them is not neutral. `mutint_dashboard.util.rebuild_sample_counts` counts
    Population *rows* rather than samples, so an emptied one inflates the dashboard's count
    permanently; and the population picker in `mutint_sample.views.common` is built from
    Population rows, so an emptied one would sit in the menu selecting nothing.

    Emptiness is re-queried here rather than taken from a snapshot made before the move:
    a swap vacates and refills the same rows, and a stale snapshot would delete a row that
    had just been filled again -- taking its samples with it, since the FK cascades.

    **Three of the four levels have gone**, so this walks one row where it once walked
    replicate, isolate, flask, ALE. An emptied *time point* needs no pruning at all now and
    is the clearest thing the removal bought: it was never a row, only a value some sample
    held, so vacating it leaves nothing behind to count or to offer in a menu.

    `Isolate.parent_isolate` was guarded here, and both are gone: nothing in the suite ever
    wrote that column, so the guard protected a state no import or edit could produce.

    `Population.starting_strain` was guarded here too. It is gone -- it was a second, never
    written spelling of the ancestor, which is now one designation on the experiment; see
    `mutint_experiment/ancestor.py`.
    """
    for population in populations:
        if population.sample_set.exists():
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


def _optional_time_point(raw, label, row_label):
    """A whole number, also when it arrives as `500.0` -- or **None** for a blank.

    `Sample.time_point` is a float, so a coordinate read back from a row and posted again
    comes back as `500.0` -- and `int("500.0")` raises. That is how a bulk save of a page
    that had merely been *opened* failed with "must be a whole number", and how a test that
    refreshed its sample between two saves found it.

    **A blank is a time point nobody has set**, which the column has always allowed and which
    `gd_import` now writes for a sample whose name carries no coordinate. This used to refuse
    one, on the reasoning that a sample has to sit somewhere -- true of the population, and
    not of the ordinal: `ordering.sample_order` sorts a null time point first precisely
    because such a sample has not been placed yet. Refusing it here meant an auto-numbered
    sample could be opened in the editor and not saved back unchanged.
    """
    if raw is None or not str(raw).strip():
        return None
    try:
        number = float(str(raw).strip())
        if not number.is_integer():
            raise ValueError(raw)
        value = int(number)
    except (TypeError, ValueError, OverflowError):
        raise SampleEditError(
            "%s: %s must be a whole number." % (row_label, label))
    if value < 0:
        # 0 is legal: a time point of 0 is where an ancestor sits.
        raise SampleEditError("%s: %s cannot be negative." % (row_label, label))
    return value


#: An ALE or a sample label is a label, not a number (`mutint_experiment.0008`), so the only
#: things to check are that it is there and that it fits the column.
_LABEL_LIMIT = 100


def _label(raw, label, row_label):
    """One text part of a coordinate: `Ara-1`, `763A`, `1-2`, or plain `2`.

    Stripped, because a trailing space is invisible in the input and would make two
    coordinates that read identically point at different rows. Refused when empty because a
    sample has to sit *somewhere*: the population and the label are what say which sample
    this is, where the time point is an ordinal that may genuinely not be known --
    `_optional_time_point` takes a blank for that reason and this does not.
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
# write or rejected by the database with a message nobody can act on.
#
# **One of these no longer has a column behind it.** `medium_description` moved into
# `supplemental_data`, where JSON imposes no width at all, so for that one this check is not
# a mirror of the schema -- it is the only limit there is. The number is kept because a note
# somebody types into a form still wants a bound, and because the sample edit page's
# `maxlength` attribute mirrors it client-side.
_MAX_LENGTHS = {"source_name": 200, "description": 300,
                "medium_description": 500}
_FIELD_LABELS = {"source_name": "sample name",
                 "description": "description",
                 "medium_description": "medium description"}


def _check_lengths(descriptive, row_label):
    for field, limit in _MAX_LENGTHS.items():
        if field not in descriptive:
            continue
        if len(descriptive[field]) > limit:
            raise SampleEditError(
                "%s: %s is too long (%d characters; the limit is %d)."
                % (row_label, _FIELD_LABELS[field], len(descriptive[field]), limit))


def parse_rows(rows, samples_by_id):
    """Validate every row and return [(sample, coordinate, descriptive_dict), ...].

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
        sample = samples_by_id.get(raw_id)
        if sample is None:
            # Includes an id from another experiment: samples_by_id is built from this
            # experiment's queryset, so a foreign id simply is not in it.
            raise SampleEditError(
                "Row %d refers to a sample that is not in this experiment." % (index + 1))

        row_label = "Row %d" % (index + 1)
        try:
            coordinate = (
                _label(row.get("population"), "Population", row_label),
                # "time point" is what the form field is called; the column is
                # so, but that is not what anyone calls it, and a refusal is the one place
                # the internal name would surface to a user. Still the one member of the
                # coordinate that must be a number -- fixation orders ALEs by it.
                _optional_time_point(row.get("time_point"), "time point", row_label),
                # One label where there were two. A replicate was never a level of
                # anything -- `1-2` is what the sample is called, and the form has one box
                # for it.
                _label(row.get("name"), "sample label", row_label),
            )
        except SampleEditError as error:
            errors[raw_id] = error.message
            continue

        # Only fields the form actually sent. The bulk table has no column for the medium
        # description, and a missing key must leave the stored value alone -- treating
        # absent as empty would have the table silently blank a field it does not even show.
        descriptive = {field: (row.get(field) or "").strip()
                       for field in DESCRIPTIVE_FIELDS if field in row}
        # The form asks the mixed question, because that is the box a person ticks. The
        # column stores the clonal one. The negation happens once, in `apply_rows`.
        descriptive["is_mixed"] = _truthy(row.get("is_mixed"))
        # The sample flags -- see mutint_sample.flags. Present means "set it to this"; absent
        # means untouched, for the same reason as the text fields above.
        for field in FLAG_FIELDS:
            if field in row:
                descriptive[field] = _truthy(row.get(field))
        try:
            _check_lengths(descriptive, row_label)
        except SampleEditError as error:
            errors[raw_id] = error.message
            continue
        parsed.append((sample, coordinate, descriptive))

    if errors:
        raise SampleEditError(_summarize(errors, samples_by_id), errors)
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
    moving = {str(sample.pk) for sample, _, descriptive in parsed
              if descriptive.get("source_name", sample.source_name) != sample.source_name}
    taken = {}
    for key, sample in samples_by_id.items():
        if key in moving or not sample.source_name:
            continue
        taken[sample.source_name] = key

    errors = {}
    for sample, _, descriptive in parsed:
        name = descriptive.get("source_name")
        if not name or name == (sample.source_name or ""):
            continue
        if name in taken:
            errors[str(sample.pk)] = (
                'A sample named "%s" already exists in this experiment.' % name)
        taken[name] = str(sample.pk)
    if errors:
        raise SampleEditError(_summarize(errors, samples_by_id), errors)


def plan_moves(parsed, samples_by_id):
    """Phase two: work out which rows actually move, and refuse collisions.

    A target held by a sample that is *itself* moving away in this same batch is fine --
    that is exactly what a swap looks like, and refusing it would make the common bulk
    operation impossible. Only a target held by a sample staying put, or one not in this
    batch at all, is a collision.

    Two samples cannot share a coordinate. There is no database constraint saying so, but
    mutint-fixation builds `flask_isolate_mutation_dict[(time_point, label)] = queryset` by
    plain assignment, so the second sample at a coordinate overwrites the first and its
    mutations vanish from fixation with no error anywhere.
    """
    occupants = {}
    for sample in samples_by_id.values():
        coordinate = sample_coordinate(sample)
        if coordinate is not None:
            occupants.setdefault(coordinate, []).append(sample)

    vacating = set()
    for sample, coordinate, _ in parsed:
        current = sample_coordinate(sample)
        if current is not None and current != coordinate:
            vacating.add(sample.pk)

    errors = {}
    claimed = {}
    for sample, coordinate, _ in parsed:
        label = coordinate_str(coordinate)
        if coordinate in claimed and claimed[coordinate] != sample.pk:
            errors[str(sample.pk)] = (
                "Two rows were both given the identity %s." % label)
            continue
        claimed[coordinate] = sample.pk

        for occupant in occupants.get(coordinate, []):
            if occupant.pk == sample.pk or occupant.pk in vacating:
                continue
            errors[str(sample.pk)] = (
                '%s is already "%s". Two samples cannot share one identity -- move that '
                'one first, or give this one a different label.'
                % (label, occupant.source_name or occupant.pk))
    if errors:
        raise SampleEditError(_summarize(errors, samples_by_id), errors)

    return [(sample, coordinate, descriptive)
            for sample, coordinate, descriptive in parsed]


def _summarize(errors, samples_by_id):
    """One string that stands on its own.

    The page also gets `errors` keyed by sample id so it can outline the inputs, but the
    save is all-or-nothing, so a single readable block is genuinely enough on its own --
    and it is what shows if the shared `mutintPost` helper ever loses the response body
    again.
    """
    lines = ["Nothing was saved."]
    for key, message in errors.items():
        sample = samples_by_id.get(key)
        name = (sample.source_name if sample and sample.source_name else "sample %s" % key)
        lines.append("%s: %s" % (name, message))
    return "\n".join(lines)


# --- applying ------------------------------------------------------------------------------


def rows_are_structural(parsed):
    """Did anything identity-bearing actually change?

    Drives whether the rebuild hooks run. A rename must not pay for a fixation rebuild.
    """
    for sample, coordinate, descriptive in parsed:
        if sample_coordinate(sample) != coordinate:
            return True
        if bool(sample.is_mixed) != descriptive["is_mixed"]:
            return True
    return False


def _write(instance, mapping, descriptive, always, curation=None):
    """Save only the columns this form sent, plus `always`.

    `update_fields` is built from what arrived rather than from the full column list, so a
    form with no input for a field cannot overwrite it -- see the note in `parse_rows`.

    `curation` is the same idea for the fields that moved into `supplemental_data`, and it
    needs its own argument rather than another entry in `mapping` because the destination is
    not a column: `update_fields` can name `supplemental_data`, but it cannot say *which part
    of it*. `set_record` is what draws that line, merging into the stored value so a curation
    write leaves the `breseq` and `sequencing` groups beside it alone -- which is the whole
    hazard of a shared JSON column, and what
    `test_a_bulk_save_leaves_the_other_groups_alone` is the test for.
    """
    from mutint_sample.models import Sample

    fields = list(always)
    for column, key in mapping.items():
        if key in descriptive:
            setattr(instance, column, descriptive[key])
            fields.append(column)

    sent = {name: descriptive[key] for name, key in (curation or {}).items()
            if key in descriptive}
    if sent:
        instance.set_record(Sample.COMPONENT, Sample.CURATION,
                            dict(instance.curation, **sent), save=False)
        fields.append("supplemental_data")

    if fields:
        instance.save(update_fields=fields)


@transaction.atomic
def apply_rows(experiment, parsed):
    """Phase three: write everything, or nothing.

    All-or-nothing because a bulk renumber is usually a permutation, and half a swap is a
    state the user can neither reason about nor undo from the page. It also means the
    rebuild hooks never run over a half-applied experiment.

    Returns the sample ids that were touched.
    """
    vacated = []
    touched = []

    for sample, coordinate, descriptive in parsed:
        current = sample_coordinate(sample)
        source_population = sample.population
        always = ["is_clonal"]

        if current != coordinate:
            # A renumber re-labels a sample; it does not move it to a different organizm.
            # Inheriting species/strain is the only answer that does not silently reset real
            # data. (There was a medium and a freezer box here too, both required FKs to
            # rows nothing displayed, and both are gone.)
            sample.population = resolve_population(
                experiment, coordinate,
                species=source_population.species if source_population else "",
                strain=source_population.strain if source_population else "")
            sample.time_point = coordinate[1]
            sample.name = coordinate[2]
            always += ["population", "time_point", "name"]
            if source_population is not None:
                vacated.append(source_population)

        # One row written where there were three. The description used to belong to the
        # isolate, which was *shared* -- so a move had to decide whether it travelled with
        # the sample or stayed with the row, and the answer ("travel, but only onto a row
        # that did not exist a moment ago") was three paragraphs of comment. It is the
        # sample's own column now, so it simply moves with it and there is nothing to decide.
        sample.is_clonal = not descriptive["is_mixed"]
        for field in FLAG_FIELDS:
            if field in descriptive:
                setattr(sample, field, descriptive[field])
                always.append(field)
        _write(sample, {"source_name": "source_name",
                       "description": "description"},
               descriptive, always,
               curation={"medium_description": "medium_description"})

        touched.append(sample.pk)

    prune_orphans(vacated)
    return touched


def rebuild_after_structural_change(experiment):
    """Recompute only what a renumber can actually have changed.

    Fixation reads the numbers directly -- it sorts time points and takes the last two to
    decide what counts as fixed -- so it has to rebuild. Sample counts are counts of
    Population *rows*, which this module creates and prunes.

    Deliberately not `gd_import.run_post_processing`: it lives in mutint_import, so calling
    it would point mutint_experiment at the import app, and it asks for every registered
    rebuild rather than the two a renumber can change.

    Deliberately not `mutation_counts`: it pulls every MutationCall in the database into
    Python. Nothing about a renumber changes a mutation count, and paying for the whole
    database on every rename is the one thing here that could make the feature feel broken in
    production. That refusal is what `only=` says -- it names what a renumber can change, and
    everything it does not name is left alone.

    **This list has emptied out from the other end.** `overview`, `mutint_converge` and
    `mutint_fixation` were all named here, and none of them stores anything now -- the
    Overview's counts, the convergent set and the fixated set are each computed by the request
    that renders them, so a renumber has nothing of theirs to mark. What is left is
    `sample_counts`, which counts the Population rows this module creates and prunes.

    Naming a plugin from here was always safe in itself: `get_rebuilders` skips a name nothing
    registered, so a deployment without the plugin simply had less to do. That property still
    holds and is still tested; it just no longer has a caller in core relying on it.
    """
    from mutint_common.rebuild_registry import request_rebuild, run_rebuilds

    changed = ('sample_counts',)
    # Marked but not run: `mutint_phylogeny` stores a rendered "A1 F1500 I1-1" per tip, so a
    # renumber leaves its tree drawing labels that are now wrong -- but nobody asked for
    # anything to happen to a tree by renaming a sample, so this marks and stops.
    #
    # What the mark now costs that plugin is a DELETE rather than an inference: it registers a
    # discard, and its page calls `ensure_fresh` on the way in, so the first reader after a
    # renumber finds the tree gone and is offered a new one. Leaving it out of `run_rebuilds`
    # below is therefore about *where* that happens rather than about expense -- this call site
    # is renaming samples and has no business inferring anything.
    request_rebuild(experiment.id, only=changed + ('mutint_phylogeny',),
                    reason='samples renumbered')
    run_rebuilds(experiment.id, only=changed)
