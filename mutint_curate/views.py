"""Editing a sample's mutations: three pages, three write endpoints.

The shape is the one `mutint_experiment/sample_views.py` established and which everything added
to this codebase since has followed -- a GET page that checks permission itself and renders
`403.html` with a status, plus a `@require_POST` JSON endpoint that checks again, because the
button being hidden is not a permission check.

Two things here differ from the mutation tables next door, and both are deliberate.

**The listings are unfiltered.** `breseq_table` runs its rows through
`mutint_filter.util.filter_mutation_calls`; these pages do not. Filtering is a display
concern, and a mutation excluded by a gene or frequency filter must still be visible to
whoever is curating -- otherwise it cannot be deleted, and it reappears the moment somebody
widens the filter.

**The write endpoints take a list, not one id.** `/experiment/`'s bulk delete fires one
request per row, which is fine for the dozen experiments on that page and wrong for the several
hundred mutations on this one. One POST is also what makes a batch a single edit set, which is
the whole point: "I removed these eleven calls" is one decision and reads as one line of
history.
"""

import json
import logging

from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

import mutint_sample.views.common as seq_common
from mutint_common.logger import user_extra
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.ordering import sample_sort_key
from mutint_experiment.permissions import (
    can_add_experiment_filter, experiment_lock_refusal,
)
from mutint_import import annotation
from mutint_curate import history, record_builder, validation
from mutint_curate.models import (
    KIND_ADD, KIND_COPY, KIND_DELETE, KIND_EDIT, MutationEditSet,
)
from mutint_sample.breseq_report import build_rows, is_mixed
from mutint_sample.models import Mutation, MutationCall
# `include_ancestor=True` on every call below: the editor curates rather than reads, so
# it must show the designated ancestor, which every reading page hides. It is also why
# `history.calls_for` uses the raw call queryset -- this app shows what is
# stored, and ancestral rows are stored.
from mutint_sample.util import get_reseq_ordered_dict
from mutint_experiment import paths

logger = logging.getLogger(__name__)

REQUEST_SAMPLE_ID = "sample_id"
REQUEST_SOURCE_SAMPLE_ID = "source_sample_id"

#: `?sample_id=all` -- the whole experiment at once rather than one sample. A sentinel in the
#: same parameter, not a parameter of its own, so the sample picker stays one control with one
#: link per entry and there is no state in which both are set and disagree.
ALL_SAMPLES = "all"

#: The two tabs over one listing. Editing and deleting are different decisions about the same
#: rows, and they were one page with a checkbox column *and* a per-row link -- so the next
#: thing you clicked might have meant either. The mode decides which column the listing grows
#: and which button sits above it; everything else about the page is identical, which is why
#: this is a parameter rather than a second template.
MODE_EDIT = "edit"
MODE_DELETE = "delete"

_REFUSED = "You do not have permission to edit this experiment's mutations."


class EditorError(Exception):
    """A refusal the user is meant to read, mirroring `samples.SampleEditError`.

    `errors` maps a field name to the complaint about that field, so the add form can put each
    message beside the input it belongs to. The delete and copy endpoints have one thing to
    say and leave it empty; `message` is always self-sufficient, so a caller with only one
    place to put text loses nothing by ignoring the map.
    """

    def __init__(self, message, status=400, errors=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or {}


# --- shared plumbing ------------------------------------------------------------------------


def _selected_reseq(request, reseq_dict, param=REQUEST_SAMPLE_ID):
    """The requested sample, or the experiment's first one."""
    requested = request.GET.get(param)
    if requested:
        try:
            sample_id = int(requested)
        except (TypeError, ValueError):
            sample_id = None
        if sample_id in reseq_dict:
            return reseq_dict[sample_id]
    for reseq in reseq_dict.values():
        return reseq
    return None


def _rows_for(reseq):
    """One sample's mutations as breseq-style rows, carrying the call id.

    `build_rows` is mutint_sample's, shared with the Samples page and the genome browser, so the
    editor's table reads identically to the one people already know. It is rendered from this
    app's own template rather than by including `breseq_table/_mutation_table.html`, which is
    shared between those two and must not grow a third caller's checkbox column.
    """
    calls = list(MutationCall.objects
                    .filter(sample=reseq)
                    .select_related("mutation"))
    calls.sort(key=lambda o: (o.mutation.seq_id or "", o.mutation.start_position))
    return build_rows(calls)


def _cell_for(call):
    """One call as a grid cell.

    `label` is the frequency where there is one, because that is what the read-only tables put
    in the same place, and a tick where there is not -- a call with no frequency is
    still an assertion that the mutation is there.
    """
    if call.present is False:
        state, label = "absent", "\u2013"
    elif call.present:
        state = "present"
        label = ("%.2f" % float(call.frequency)
                 if call.frequency is not None else "\u2713")
    else:
        state, label = "unknown", "?"
    return {"id": call.id, "label": label, "state": state}


#: How many mutations the grid lays out at once. Not a display preference -- a real
#: experiment here is 5,076 mutations across 51 samples, and rendering all of it produced a
#: **32.8 MB** page (measured; the server built it in 1.6s, so the cost is entirely what the
#: browser is then handed). The per-sample mode has no such ceiling because it is one column.
GRID_ROW_LIMIT = 250


def _grid_mutations(experiment, reseq_dict, query):
    """The mutations a grid should lay out, narrowed by the search box.

    Narrowing happens here rather than in DataTables because the point is to not *render* the
    rest: a client-side search still ships every row. Position is matched exactly when the
    query is a number, since a substring match on a coordinate is never what anybody means.

    **Only mutations something in `reseq_dict` observes.** A `Mutation` is never deleted here
    -- its id is stored as a bare integer in mutint-converge, in mutint-phylogeny's JSON and in
    every exported CSV -- so removing a mutation's last call leaves the row behind, and
    a grid keyed on `Mutation.objects.filter(experiment=...)` went on rendering it with
    every cell empty. That is what "the page does not update when I delete" was: the page
    reloads, and the row is genuinely still there.

    Restricted to the *shown* samples rather than to the experiment, because
    `get_reseq_ordered_dict` applies the experiment's sample tag filters -- a mutation observed
    only in a hidden sample is an all-empty row for the same reason.

    This is not the filtering the editor forbids. A mutation no sample observes is stored in no
    sample: there is no cell on its row to select and nothing on it to delete. It is not being
    hidden, it is not there.
    """
    calls_here = (history.calls_for(experiment)
                     .filter(sample_id__in=list(reseq_dict))
                     .values("mutation_id"))
    mutations = Mutation.objects.filter(experiment=experiment,
                                        id__in=calls_here)
    query = (query or "").strip()
    if query:
        terms = (Q(gene__icontains=query) | Q(seq_id__icontains=query)
                 | Q(mutation_type__iexact=query) | Q(sequence_change__icontains=query))
        if query.isdigit():
            terms = terms | Q(start_position=int(query))
        mutations = mutations.filter(terms)
    # nulls_first, because seq_id is nullable and the two backends disagree about
    # where a NULL goes -- SQLite first, PostgreSQL last. This listing reaches the page, so
    # inheriting the backend's opinion means the editor's rows reorder on deployment with
    # nothing to say why. Same rule as mutint_experiment.ordering.sample_order().
    return mutations.order_by(F("seq_id").asc(nulls_first=True), "start_position", "pk")


def _grid_for(experiment, reseq_dict, query=None):
    """Every call of the matching mutations, as mutations down and samples across.

    Returns `(rows, by_mutation, by_sample, total, shown)`. The two maps are what the page's
    row and column selectors read: `deferRender` means a cell on an undrawn page has no DOM,
    so "select this whole sample" cannot be done by walking `<td>`s without silently missing
    everything not currently on screen.

    **The maps cover only the rendered rows**, deliberately. They could just as easily cover
    the whole experiment, and then a column selector would put calls into the selection
    that the person cannot see and did not know about -- on a page whose next button deletes
    them.

    Built here rather than through the mutation matrix, for the reasons `_rows_for` gives
    about `breseq_table/_mutation_table.html` and two more of its own. That builder renders a
    cell as an `<a>` into the genome browser, which would fight a click that means "select";
    and `get_table_body` filters through `filter_mutation_calls`, while this page must show
    what is stored -- a mutation hidden by a gene or frequency filter has to stay deletable.
    """
    matching = _grid_mutations(experiment, reseq_dict, query)
    total = matching.count()
    page = list(matching[:GRID_ROW_LIMIT])

    column_of = {sample_id: position for position, sample_id in enumerate(reseq_dict)}
    rows = {mutation.id: {"mutation": mutation, "cells": [None] * len(column_of)}
            for mutation in page}

    by_mutation = {}
    by_sample = {}
    calls = (history.calls_for(experiment)
                .filter(mutation_id__in=list(rows))
                .order_by("pk"))
    for entry in calls:
        position = column_of.get(entry.sample_id)
        if position is None:
            # A sample the picker is not showing -- `get_reseq_ordered_dict` applies the
            # experiment's sample tag filters. Its calls are not selectable here
            # because they are not on the page; they are untouched, not hidden.
            continue
        rows[entry.mutation_id]["cells"][position] = _cell_for(entry)
        by_mutation.setdefault(entry.mutation_id, []).append(entry.id)
        by_sample.setdefault(entry.sample_id, []).append(entry.id)

    ordered = [rows[mutation.id] for mutation in page]
    # The column headers double as selectors, so each carries how much it would select. A
    # sample with nothing among the rendered rows renders as plain text instead of a link:
    # measured in a browser, an experiment's mutations are often concentrated in a subset of
    # its samples, and a control that looks live and silently does nothing reads as broken.
    columns = [{"reseq": reseq, "count": len(by_sample.get(sample_id, ()))}
               for sample_id, reseq in reseq_dict.items()]
    return ordered, columns, by_mutation, by_sample, total, len(ordered)


def _page_context(request, experiment):
    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        "can_edit": can_add_experiment_filter(request.user, experiment),
    })
    return context


class _NotForYou(Exception):
    """The caller may not see this experiment. Carries the response to render instead."""

    def __init__(self, response):
        super().__init__("refused")
        self.response = response


def _experiment_for_page(request, context):
    """The experiment a page is scoped to, or a refusal to render instead.

    `seq_common.get_experiment` signals two different things and neither is an exception
    type of its own: `Experiment.DoesNotExist` for "no experiment selected", which is how
    these pages open and is not an error, and a bare `ValueError` for "you may not view this".
    The pages next door catch the second with a blanket `except Exception` and render
    `500.html`, which tells a reader the site broke when in fact they were refused. Here it is
    `403.html` with a 403, matching every other page in this codebase that checks its own
    permission.
    """
    try:
        return seq_common.get_experiment(request)
    except Experiment.DoesNotExist:
        raise _NotForYou(seq_common.no_experiment_selected(
            request, context, logger, "mutation editor"))
    except ValueError:
        raise _NotForYou(render(request, "403.html", context, status=403))


def _experiment_for_write(request):
    """The experiment a POST names, refusing if the caller may not edit it.

    Reads `experiment_id` from the POST body rather than the query string: these endpoints are
    reached by `mutintPost`, which sends FormData, and a write that took its scope from the URL
    would be one redirect away from acting on the wrong experiment.
    """
    raw = request.POST.get("experiment_id")
    try:
        experiment = Experiment.objects.get(pk=raw)
    except (Experiment.DoesNotExist, ValueError, TypeError):
        raise EditorError("No such experiment.", status=404)
    if not can_add_experiment_filter(request.user, experiment):
        # "you may not edit this" and "nobody may edit this at the moment" are different
        # answers, and the second one is actionable -- it names the reason and says who can
        # lift it. Falling back to the generic refusal when the lock is not why.
        raise EditorError(experiment_lock_refusal(experiment) or _REFUSED, status=403)
    return experiment


def _int_list(request, field):
    """A JSON array of ids from the POST body.

    `mutintPost` stringifies every FormData value, so a list has to arrive as one JSON string --
    the same convention `experiment_samples_update` uses and for the same reason: flat keys
    need a hand-written parser in which a typo drops a value silently instead of erroring.
    """
    raw = request.POST.get(field) or "[]"
    try:
        values = json.loads(raw)
    except ValueError:
        raise EditorError("Malformed request: %s was not valid JSON." % field)
    if not isinstance(values, list):
        raise EditorError("Malformed request: %s was not a list." % field)
    try:
        return [int(value) for value in values]
    except (TypeError, ValueError):
        raise EditorError("Malformed request: %s held something that is not an id." % field)


def _error_response(error):
    # `errors` rides alongside the summary, which is what `mutintPost` hands the client as
    # `err.body.errors` -- the shape `experiment_samples.html` already reads to outline the
    # rows it was refused on.
    return JsonResponse({"error": error.message, "errors": error.errors},
                        status=error.status)


# --- pages ----------------------------------------------------------------------------------


@ensure_csrf_cookie
def curate(request):
    """The Edit tab: this experiment's mutations, each with a link to change it.

    `^$` rather than `^edit$` because this is where the sidebar's "Curate" link lands.
    """
    return _listing(request, MODE_EDIT)


@ensure_csrf_cookie
def mutation_delete(request):
    """The Delete tab: the same listing, selectable, with Delete selected."""
    return _listing(request, MODE_DELETE)


def _listing(request, mode):
    """Mutations, one sample or the whole experiment, in whichever mode the tab asked for.

    The two modes exist because the questions are different. One sample reads like breseq's
    own report and is where somebody checks a single run; all samples is where the same bad
    call is removed from the twelve samples that carry it, which was twelve page loads before
    even though `mutation_delete` has always taken a list spanning any of them.

    Per-sample stays the default. The grid is the more useful view of a large experiment and
    also the more expensive one, and arriving at a page that has to lay out every mutation
    against every sample is not what somebody following a link from a sample expects.

    Both modes render the same template and the same rows. What differs is one column -- an
    `edit` link or a selection checkbox -- and the button above the table. Building the rows
    twice, in two views over two templates, would be two places for the "listings here are
    unfiltered" rule to drift apart.
    """
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.id, include_ancestor=True)
        all_samples = request.GET.get(REQUEST_SAMPLE_ID) == ALL_SAMPLES
        reseq = None if all_samples else _selected_reseq(request, reseq_dict)

        context = _page_context(request, experiment)
        context.update({
            "reseq_list": list(reseq_dict.values()),
            "all_samples": all_samples,
            "all_samples_value": ALL_SAMPLES,
            "selected_reseq": reseq,
            "selected_sample_id": reseq.id if reseq is not None else None,
            "is_mixed": is_mixed(reseq),
            "rows": _rows_for(reseq) if reseq is not None else [],
            "recent_edits": _recent_edits(experiment),
            "mode": mode,
            "is_delete_mode": mode == MODE_DELETE,
            "title": ("Delete %s mutations" if mode == MODE_DELETE
                      else "Curate %s mutations") % experiment.name,
            "template_header": ("Delete Mutations" if mode == MODE_DELETE
                                else "Curate"),
        })
        if all_samples:
            query = request.GET.get("q", "")
            grid_rows, grid_columns, by_mutation, by_sample, total, shown = _grid_for(
                experiment, reseq_dict, query)
            context.update({
                "grid_rows": grid_rows,
                "grid_columns": grid_columns,
                "by_mutation": by_mutation,
                "by_sample": by_sample,
                "grid_query": query,
                "grid_total": total,
                "grid_shown": shown,
                "grid_truncated": total > shown,
                "grid_limit": GRID_ROW_LIMIT,
            })
        return render(request, "curate/mutations.html", context)
    except _NotForYou as refusal:
        return refusal.response


@ensure_csrf_cookie
def mutation_add(request):
    """Add a mutation nothing in this experiment carries yet."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.id, include_ancestor=True)
        reference_row = _reference_row(experiment)

        context = _page_context(request, experiment)
        context.update({
            "targets": list(reseq_dict.values()),
            "schema": validation.form_schema(),
            # The contigs a position can be on. Offered as a dropdown when they are known,
            # because a mistyped contig name is refused by an exact match with no near-miss
            # handling -- `LoadedReferenceSequences.add` matches names exactly, on purpose.
            "seq_ids": sorted(validation.contig_lengths(reference_row)),
            "has_reference": reference_row is not None,
            "title": "Add a mutation to %s" % experiment.name,
            "template_header": "Add Mutation",
        })
        return render(request, "curate/add.html", context)
    except _NotForYou as refusal:
        return refusal.response


@ensure_csrf_cookie
def mutation_edit(request):
    """Edit one mutation, in every sample that carries it or in a chosen few.

    The samples offered are exactly the ones carrying it -- there is nothing to change in a
    sample that does not. Which of them start highlighted is `_initial_selection`.
    """
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        mutation = _mutation_for_page(request, experiment)

        context = _page_context(request, experiment)
        reference_row = _reference_row(experiment)
        carrying = _carrying_samples(experiment, mutation)
        context.update({
            "mutation": mutation,
            "schema": validation.form_schema(),
            "initial": _initial_fields(mutation),
            "targets": carrying,
            "selected_ids": _initial_selection(request, carrying),
            "sample_count": len(carrying),
            "seq_ids": sorted(validation.contig_lengths(reference_row)),
            "has_reference": reference_row is not None,
            "title": "Edit a mutation",
            "template_header": "Edit Mutation",
        })
        return render(request, "curate/edit.html", context)
    except _NotForYou as refusal:
        return refusal.response


def _initial_selection(request, carrying):
    """Which of the carrying samples start highlighted: the one linked from, or all of them.

    A `change` link on a sample's own mutation table carries `?sample_id=`, and the page opens
    on that sample alone -- correcting a call you are looking at, in the sample you are
    looking at it in, is what somebody following that link means. The grid's link carries
    none, because its row spans every sample and there is no one sample that was clicked; the
    whole set stays the default there, and `?sample_id=all` lands on it too rather than on
    nothing, since `int("all")` is not a sample.
    """
    ids = [reseq.id for reseq in carrying]
    try:
        chosen = int(request.GET.get(REQUEST_SAMPLE_ID))
    except (TypeError, ValueError):
        return ids
    return [chosen] if chosen in ids else ids


def _carrying_samples(experiment, mutation):
    """The experiment's samples that observe `mutation`, in the usual sample order.

    Ordered through `get_reseq_ordered_dict` rather than by whatever the calls come
    back in, so the list reads the same way as every other list of samples on the site.
    """
    observing = set(history.calls_for(experiment)
                    .filter(mutation=mutation)
                    .values_list("sample_id", flat=True))
    return [reseq for reseq in get_reseq_ordered_dict(experiment.id, include_ancestor=True).values()
            if reseq.id in observing]


def _mutation_for_page(request, experiment):
    """The mutation named by `?mutation_id=`, scoped through the experiment.

    Scoped rather than fetched by pk alone, for the reason `mutation_delete` scopes its ids:
    a mutation belongs to one experiment's reference genome, and a hand-typed pk from another
    must not resolve here.
    """
    try:
        return Mutation.objects.get(experiment=experiment,
                                    pk=request.GET.get("mutation_id"))
    except (Mutation.DoesNotExist, ValueError, TypeError):
        raise _NotForYou(render(request, "404.html", get_user_context(request.user),
                                status=404))


def _initial_fields(mutation):
    """What the form opens with: the mutation's stored record, minus its bookkeeping.

    Straight from the stored GenomeDiff record -- so the form is
    populated by the same keys `validation.validate_record` will read back, and a field the
    add form knows nothing about cannot appear.
    """
    data = dict(mutation.genome_diff)
    for key in ("type", "id", "parent_ids", "frequency"):
        data.pop(key, None)
    return {name: ("" if value is None else value) for name, value in data.items()}


def _reference_row(experiment):
    """The experiment's `ReferenceSequences`, or None. Reads no files."""
    from mutint_sample.models import ReferenceSequences

    return ReferenceSequences.objects.filter(experiment=experiment).first()


@ensure_csrf_cookie
def mutation_copy(request):
    """Copy mutations from one sample of this experiment onto others."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.id, include_ancestor=True)
        source = _selected_reseq(request, reseq_dict, REQUEST_SOURCE_SAMPLE_ID)

        context = _page_context(request, experiment)
        context.update({
            "reseq_list": list(reseq_dict.values()),
            "source_reseq": source,
            "source_sample_id": source.id if source is not None else None,
            "targets": [reseq for reseq in reseq_dict.values()
                        if source is None or reseq.id != source.id],
            "rows": _rows_for(source) if source is not None else [],
            "title": "Copy mutations in %s" % experiment.name,
            "template_header": "Copy Mutations",
        })
        return render(request, "curate/copy.html", context)
    except _NotForYou as refusal:
        return refusal.response


def mutation_history(request):
    """Every recorded change to this experiment's mutations, newest first."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        context = _page_context(request, experiment)
        context.update({
            "edit_sets": _edit_sets(experiment),
            "title": "Mutation history for %s" % experiment.name,
            "template_header": "Mutation History",
        })
        return render(request, "curate/history.html", context)
    except _NotForYou as refusal:
        return refusal.response


def _edit_sets(experiment, limit=None):
    queryset = (MutationEditSet.objects
                .filter(experiment=experiment)
                .select_related("created_by")
                .prefetch_related(paths.to_population("edits__" + paths.FROM_EDIT)))
    if limit is not None:
        queryset = queryset[:limit]
    return [_edit_set_context(edit_set) for edit_set in queryset]


def _recent_edits(experiment):
    return _edit_sets(experiment, limit=5)


def _edit_set_context(edit_set):
    """One history row: who, when, and a per-sample tally rather than every row.

    A batch copy across twenty samples is hundreds of MutationEdit rows, and a page that
    listed each of them would bury the one thing a person is looking for -- which samples moved
    and by how much. The rows themselves are still there to expand into.
    """
    added = removed = 0
    samples = {}
    for edit in edit_set.edits.select_related(
            paths.to_population(paths.FROM_EDIT)).all():
        # A deleted sample sorts last: it has no coordinate, and keeping the rows nobody can
        # act on together at the end beats interleaving them.
        order = (0, sample_sort_key(edit.sample)) if edit.sample_id else (1, ())
        label = edit.sample.label if edit.sample_id else "(deleted sample)"
        tally = samples.setdefault(
            label, {"label": label, "added": 0, "removed": 0, "order": order})
        if edit.operation == "add":
            added += 1
            tally["added"] += 1
        else:
            removed += 1
            tally["removed"] += 1
    return {
        "edit_set": edit_set,
        "added": added,
        "removed": removed,
        # By coordinate, not by label. Sorting on `label` is a lexicographic
        # sort over text, which puts `A1 F10 I1` above `A1 F2 I1` -- the exact thing
        # `mutint_experiment.ordering` exists to prevent, and it read as correct here because
        # single-digit flasks are the common case. It also sorted by the *isolate description*
        # wherever one is set, since that is what `label` returns.
        "samples": sorted(samples.values(), key=lambda entry: entry["order"]),
    }


# --- writes ---------------------------------------------------------------------------------


@require_POST
def mutation_delete_apply(request):
    """Remove selected calls from one sample."""
    try:
        experiment = _experiment_for_write(request)
        call_ids = _int_list(request, "call_ids")
        if not call_ids:
            raise EditorError("Select at least one mutation to delete.")

        # Scoped through the experiment, not taken on trust: an id from another experiment
        # simply does not match, so a hand-built POST cannot reach across projects.
        removals = list(history.calls_for(experiment)
                        .filter(pk__in=call_ids))
        if not removals:
            raise EditorError("Those mutations are not in this experiment.", status=404)

        edit_set = history.apply_edits(
            experiment, request.user, KIND_DELETE, removals=removals,
            note="Deleted %d mutation(s)." % len(removals))
    except EditorError as error:
        return _error_response(error)

    history.rebuild_after_edit(experiment)
    logger.info("mutations deleted", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.id,
                         "removed": len(removals),
                         "edit_set_id": edit_set.pk if edit_set else None})


@require_POST
def mutation_copy_apply(request):
    """Copy chosen mutations from one sample onto one or more others.

    A target that already carries the mutation is skipped rather than given a second
    call of it: "make sure this call is on these samples too" is what the button means,
    and duplicating a row would quietly double that sample's count of it.
    """
    try:
        experiment = _experiment_for_write(request)
        mutation_ids = _int_list(request, "mutation_ids")
        target_ids = _int_list(request, "target_sample_ids")
        if not mutation_ids:
            raise EditorError("Select at least one mutation to copy.")
        if not target_ids:
            raise EditorError("Select at least one sample to copy to.")

        source_id = request.POST.get(REQUEST_SOURCE_SAMPLE_ID)
        sources = list(history.calls_for(experiment)
                       .filter(sample_id=source_id,
                               mutation_id__in=mutation_ids))
        if not sources:
            raise EditorError("Those mutations are not in the source sample.", status=404)

        targets = {reseq.id: reseq for reseq in
                   get_reseq_ordered_dict(experiment.id, include_ancestor=True).values()
                   if reseq.id in set(target_ids)}
        if not targets:
            raise EditorError("Those samples are not in this experiment.", status=404)

        additions, already = _plan_copy(experiment, sources, targets)
        edit_set = history.apply_edits(
            experiment, request.user, KIND_COPY, additions=additions,
            note="Copied %d mutation(s) to %d sample(s)." % (len(sources), len(targets)))
    except EditorError as error:
        return _error_response(error)

    if edit_set is not None:
        history.rebuild_after_edit(experiment)
    logger.info("mutations copied", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.id,
                         "added": len(additions),
                         "already": already,
                         "edit_set_id": edit_set.pk if edit_set else None})


def _plan_copy(experiment, sources, targets):
    """Additions for copying each source call onto each target that lacks it.

    Returns `(additions, already)` -- the targets that were skipped at least once, by name. A
    count would say how many calls were not copied, which is not a number anybody can
    act on; the samples are, and they are what the page reports.
    """
    existing = history.live_state(experiment, sample_ids=list(targets))
    additions = []
    skipped = set()
    for call in sources:
        identity = history.mutation_identity(call.mutation)
        snapshot = history.call_snapshot(call)
        for target_id in targets:
            key = (target_id, history.key_from_identity(identity), snapshot.get("source"))
            if existing.get(key):
                skipped.add(target_id)
                continue
            additions.append({
                "sample_id": target_id,
                "identity": identity,
                "snapshot": snapshot,
                "mutation": call.mutation,
                "call": None,
                "source_sample_id": call.sample_id,
            })
            # Keep the map current so copying two source rows that collapse to the same key
            # onto one target adds it once rather than twice.
            existing.setdefault(key, []).append(None)
    return additions, [reseq.label for target_id, reseq in targets.items()
                       if target_id in skipped]


@require_POST
def mutation_add_apply(request):
    """Create one mutation and observe it in every selected sample.

    The mutation itself is minted by `history.apply_edits` through `_resolve_mutation`, which
    `get_or_create`s on the same seven fields the importer keys on -- so adding a call another
    sample already carries links the existing row instead of forking it.
    """
    try:
        experiment = _experiment_for_write(request)
        target_ids = _int_list(request, "target_sample_ids")
        if not target_ids:
            raise EditorError("Select at least one sample to add it to.")

        mutation_type = (request.POST.get("mutation_type") or "").strip().upper()
        frequency = _frequency(request)

        reference_row = _reference_row(experiment)
        attributes, errors = validation.validate_record(
            request.POST, mutation_type,
            reference_row=reference_row,
            # A callable, not a value: loading parses the whole genome, and most types are
            # decided without ever reading a base.
            load_references=lambda: annotation.reference_sequences_for(experiment))
        if errors:
            raise EditorError("That mutation cannot be added as entered.", errors=errors)

        targets = {reseq.id: reseq for reseq in
                   get_reseq_ordered_dict(experiment.id, include_ancestor=True).values()
                   if reseq.id in set(target_ids)}
        if not targets:
            raise EditorError("Those samples are not in this experiment.", status=404)

        genome_diff = record_builder.build_genome_diff(mutation_type, attributes)
        annotated_record, _ = record_builder.annotate(genome_diff, experiment)
        identity = record_builder.build_identity(
            mutation_type, genome_diff, annotated_record)
        call = record_builder.build_call(frequency)

        additions, already = _plan_add(experiment, identity, call, targets)
        edit_set = history.apply_edits(
            experiment, request.user, KIND_ADD, additions=additions,
            note="Added %s at %s:%s to %d sample(s)." % (
                mutation_type, attributes.get("seq_id"), attributes.get("position"),
                len(additions)))
    except EditorError as error:
        return _error_response(error)

    if edit_set is not None:
        # The promoted annotation columns are not part of the identity, so the row `apply_edits`
        # minted has them null until this runs -- and would render through the unannotated
        # fallback. Same call `gd_import` makes after its own get_or_create.
        record_builder.apply_annotation(edit_set.edits.first().mutation, annotated_record)
        history.rebuild_after_edit(experiment)

    logger.info("mutation added", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.id,
                         "added": len(additions),
                         "already": already,
                         "edit_set_id": edit_set.pk if edit_set else None})


def _frequency(request):
    """The one frequency every selected sample's call gets."""
    raw = (request.POST.get("frequency") or "").strip()
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise EditorError("That mutation cannot be added as entered.",
                          errors={"frequency": "Must be a number between 0 and 1."})
    if not 0 < value <= 1:
        raise EditorError("That mutation cannot be added as entered.",
                          errors={"frequency": "Frequencies run from just above 0 to 1."})
    return value


def _plan_add(experiment, identity, call, targets):
    """One addition per target that does not already carry this mutation.

    Returns `(additions, already)`, the second being the skipped samples by name -- which is
    what somebody can act on, where a count of them is not.
    """
    existing = history.live_state(experiment, sample_ids=list(targets))
    key_part = history.key_from_identity(identity)

    additions = []
    already = []
    for target_id, reseq in targets.items():
        if existing.get((target_id, key_part, call.get("source"))):
            already.append(reseq.label)
            continue
        additions.append({
            "sample_id": target_id,
            "identity": identity,
            "snapshot": call,
            "mutation": None,          # -- minted by _resolve_mutation from the identity
            "call": None,
            "source_sample_id": None,  # -- nothing was copied; this is a new assertion
        })
    return additions, already


@require_POST
def mutation_edit_apply(request):
    """Edit a mutation, in every sample that carries it or in the ones chosen.

    Which of two paths runs is decided by two independent questions: does the whole set move,
    and do the new values already name a mutation this experiment has?

    - **The whole set, onto values nothing else holds.** `apply_mutation_edit` moves the row
      itself and its primary key never changes. Worth keeping wherever it can be kept:
      mutation ids are stored as bare integers, with no foreign key, in mutint-phylogeny's
      `branch_mutations` and in every exported CSV, and nothing refreshes them.
    - **Anything else.** The chosen calls move *off* their mutation and onto a
      different one -- the row already holding the new values, or a new row -- and the mutation
      they came off keeps whichever samples were not chosen. Correcting a call in three of
      eleven samples leaves two mutations behind, which is the point of being able to.

    A whole-set change onto values another mutation already has takes the second path too, and
    leaves the row it emptied in place rather than deleting it. That is the posture delete
    takes with a `Mutation` as well, and it is what lets a restore hand the calls back
    to the same primary key.
    """
    try:
        experiment = _experiment_for_write(request)
        mutation = _mutation_for_write(request, experiment)

        mutation_type = (request.POST.get("mutation_type") or "").strip().upper()
        reference_row = _reference_row(experiment)
        attributes, errors = validation.validate_record(
            request.POST, mutation_type,
            reference_row=reference_row,
            load_references=lambda: annotation.reference_sequences_for(experiment))
        if errors:
            raise EditorError("That mutation cannot be changed as entered.", errors=errors)

        genome_diff = record_builder.build_genome_diff(mutation_type, attributes)
        annotated_record, _ = record_builder.annotate(genome_diff, experiment)
        identity = record_builder.build_identity(
            mutation_type, genome_diff, annotated_record)

        _refuse_unchanged(mutation, identity)

        carrying = list(history.calls_for(experiment).filter(mutation=mutation))
        if not carrying:
            raise EditorError("No sample carries that mutation, so there is nothing to "
                              "change.", status=404)
        chosen = _chosen_calls(request, carrying)
        note = "Changed %s at %s:%s to %s at %s:%s in %d of %d sample(s)." % (
            mutation.mutation_type, mutation.seq_id, mutation.start_position,
            mutation_type, attributes.get("seq_id"), attributes.get("position"),
            len(chosen), len(carrying))

        if len(chosen) == len(carrying) and _existing_mutation(
                experiment, mutation, identity) is None:
            edit_set = history.apply_mutation_edit(
                experiment, request.user, mutation, identity, note=note)
            # The promoted annotation columns are not part of the identity, so
            # `apply_mutation_edit` left them describing the mutation as it was. Same call the
            # add path makes.
            record_builder.apply_annotation(mutation, annotated_record)
            landed_on, already = mutation, []
        else:
            landed_on, created = history.mutation_for_identity(experiment, identity)
            edit_set, already = _move_calls(
                experiment, request.user, landed_on, identity, chosen, note)
            if created:
                record_builder.apply_annotation(landed_on, annotated_record)
            # `mutation` itself is deliberately not re-annotated on this path: it has not
            # changed. Its remaining samples still observe the call it always was.
    except EditorError as error:
        return _error_response(error)

    if edit_set is not None:
        history.rebuild_after_edit(experiment)
    logger.info("mutation changed", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.id,
                         "mutation_id": landed_on.pk,
                         "samples": len(chosen),
                         "already": already,
                         "edit_set_id": edit_set.pk if edit_set else None})


def _chosen_calls(request, carrying):
    """The calls to change, named by `target_sample_ids` out of the ones carrying it.

    An absent or empty list means every carrying sample. That is what the page posted before
    it could pick a subset, so the endpoint's old contract still holds and a caller that does
    not care about samples need not learn about them.
    """
    wanted = _int_list(request, "target_sample_ids")
    if not wanted:
        return list(carrying)

    wanted = set(wanted)
    if wanted - {call.sample_id for call in carrying}:
        # Scoped to the samples carrying it for the reason `_mutation_for_page` scopes the
        # mutation to the experiment: a hand-typed id must not reach past what the page
        # offered. There is also nothing to change in a sample that does not carry it.
        raise EditorError("Those samples do not carry this mutation.", status=404)
    return [call for call in carrying
            if call.sample_id in wanted]


def _move_calls(experiment, user, target, identity, chosen, note):
    """Move `chosen` off the mutation they observe and onto `target`, as one edit set.

    Returns `(edit_set, already)` -- the samples that already carried `target`, by name.

    Such a sample gets the removal and no addition: afterwards it observes the mutation once
    rather than twice, keeping the frequency and read counts it already had rather than the
    ones it was carrying under the old call. That is the same choice `_plan_add` and
    `_plan_copy` make about a target that already has what is being put on it, and it is the
    one part of the result a person cannot read off the page, so it is named back to them.
    """
    sample_ids = [call.sample_id for call in chosen]
    present = history.live_state(experiment, sample_ids=sample_ids)
    names = {reseq.id: reseq.label
             for reseq in get_reseq_ordered_dict(experiment.id, include_ancestor=True).values()}

    additions = []
    already = []
    for call in chosen:
        snapshot = history.call_snapshot(call)
        key = (call.sample_id, history.key_from_identity(identity),
               snapshot.get("source"))
        if present.get(key):
            already.append(call.sample_id)
            continue
        additions.append({
            "sample_id": call.sample_id,
            "identity": identity,
            "snapshot": snapshot,
            "mutation": target,
            "call": None,
            # Nothing was copied from a sibling: this is the sample's own call,
            # carried across to what it is now a call of.
            "source_sample_id": None,
        })

    edit_set = history.apply_edits(experiment, user, KIND_EDIT,
                                       removals=chosen, additions=additions, note=note)
    # Named in sample order, which is the order `names` is in, rather than in whatever order
    # the calls came back in.
    already = set(already)
    return edit_set, [name for sample_id, name in names.items() if sample_id in already]


def _mutation_for_write(request, experiment):
    try:
        return Mutation.objects.get(experiment=experiment,
                                    pk=request.POST.get("mutation_id"))
    except (Mutation.DoesNotExist, ValueError, TypeError):
        raise EditorError("That mutation is not in this experiment.", status=404)


def _refuse_unchanged(mutation, identity):
    """A change that changes nothing is refused rather than logged.

    Both halves matter: the six key fields decide what the mutation *is*, and the stored
    record can move without them -- a MOB's `strand`, say -- which is a real change to what
    `to_gd_line()` writes even though the identity is the same.

    It compares the **record**, not the whole `supplemental_data` container. The question is
    whether this mutation changed; another component's key moving is not that, and reading
    the container would make an unrelated write look like an edit.
    """
    if (history.mutation_key(mutation) == history.key_from_identity(identity)
            and (mutation.genome_diff or None)
                == (history.genome_diff_from(identity) or None)):
        raise EditorError("Those are the values it already has.")


def _existing_mutation(experiment, mutation, identity):
    """The row this experiment already has holding `identity`, or None.

    Only the branch above needs this, and only to answer "is there anything to join". The six-
    field key is what `gd_import` dedups on, so at most one row can match. `mutation` is
    excluded because a row is not something to join *itself* -- though `_refuse_unchanged` has
    already ruled that out by the time this runs.
    """
    key = {field: identity.get(field) for field in history.MUTATION_KEY_FIELDS}
    return (Mutation.objects.filter(experiment=experiment, **key)
            .exclude(pk=mutation.pk).first())


@require_POST
def mutation_restore(request):
    """Put the experiment, or chosen samples of it, back to an earlier point."""
    try:
        experiment = _experiment_for_write(request)

        raw = (request.POST.get("edit_set_id") or "").strip()
        edit_set = None
        if raw:
            edit_set = MutationEditSet.objects.filter(
                pk=raw, experiment=experiment).first()
            if edit_set is None:
                raise EditorError("That point is not in this experiment's history.",
                                  status=404)

        sample_ids = _int_list(request, "sample_ids") or None
        edit = history.restore(experiment, request.user, edit_set, sample_ids)
    except EditorError as error:
        return _error_response(error)

    logger.info("mutations restored", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.id,
                         "edit_set_id": edit.pk if edit else None,
                         "changed": edit is not None})
