from django.shortcuts import get_object_or_404, render
from aledb_experiment.models import live
from django.shortcuts import redirect
from .models import Project, AleExperiment
from .utils import get_user_projects, get_all_user_exps
from .permissions import (
    accessible_projects, can_admin_project, can_edit_experiment, can_edit_project,
    can_lock_experiment, can_view_project, experiment_lock_refusal,
)
from .roles import ROLE_WRITE
from aledb_common.logger import user_extra
from aledb_common.util import get_user_context
import logging

logger = logging.getLogger(__name__)


def projects(request):
    project_list = get_user_projects(request.user)
    template_name = "ale/projects.html"
    project_dic = {}
    for project in project_list:
        project_experiments = live(project.aleexperiment_set.all())
        dois = []
        for project_experiment in project_experiments:
            if project_experiment.doi is not None:
                experiment_dois = project_experiment.doi.split()
            else:
                experiment_dois = []
            dois = dois + experiment_dois
        project_dic[project] = list(set(dois))

    return render(request, template_name, {'project_dic': project_dic.items()})


def _editable_projects(user):
    """Projects the user may create an experiment under.

    Viewable is not enough: `experiment_create` gates on `can_edit_project`, so offering a
    project here that the POST would 403 on is just a slower error. One query -- this was a
    Python filter over every readable project, which meant a permission check per row.
    """
    return accessible_projects(user, ROLE_WRITE)


def experiments(request):
    experiment_list = get_all_user_exps(request.user)
    template_name = "ale/experiments.html"
    return render(request, template_name, {
        'experiments': experiment_list,
        'editable_projects': _editable_projects(request.user),
    })


def project_detail(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if can_view_project(request.user, project):
        experiments = live(project.aleexperiment_set.all())
        return render(request, "ale/project_detail.html", {
            "project": project,
            "experiments": experiments,
            "can_edit": can_edit_project(request.user, project),
            "can_admin": can_admin_project(request.user, project),
        })
    # `status=403`, as every other refusal on this page does. Without it a refused project
    # rendered the 403 body under an HTTP 200, so anything reading the status -- a test, a
    # link checker, a client -- was told the request had succeeded.
    return render(request, "403.html", get_user_context(request.user), status=403)


def experiment_detail(request, pk):
    # experiment = get_object_or_404(AleExperiment, pk=pk)
    # context = {
    #     "ale_experiment_id": experiment.ale_id,
    #     "ale_experiment_name": experiment.name,
    # }
    url = "/stats?ale_experiment_id="+pk
    return redirect(url)





# --- create / delete ---------------------------------------------------------------------
#
# Creation used to happen only as a side effect of uploading, and deletion only from the CLI
# or /admin/. These give both a home under the objects they act on.
#
# Deletion is soft: the row is flagged with a timestamp and the acting user, and
# `purge_deleted` removes it for real once the retention window passes.

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .permissions import can_delete_experiment, can_delete_project, set_primary_owner



def project_new(request):
    """The create-a-project form, on a page of its own.

    Pages rather than dialogs: a create form wants a heading, room to explain its
    fields and a URL you can link someone to, and none of that survives in a modal
    over a table. The POST still goes to project_create, which is where the
    permission check lives.
    """
    if not request.user.is_authenticated:
        return render(request, "403.html", get_user_context(request.user), status=403)
    return render(request, "ale/project_new.html", get_user_context(request.user))


def experiment_new(request):
    """The create-an-experiment form. `?project=<pk>` fixes the project."""
    if not request.user.is_authenticated:
        return render(request, "403.html", get_user_context(request.user), status=403)

    context = get_user_context(request.user)
    editable = _editable_projects(request.user)

    project = None
    requested = request.GET.get("project")
    if requested:
        project = get_object_or_404(Project, pk=requested)
        if not can_edit_project(request.user, project):
            return render(request, "403.html", context, status=403)

    if project is None and not editable:
        # Nothing to create under. The project page is where that starts, and it
        # offers the button once you get there.
        return redirect("/ale/projects/new/")

    context.update({"project": project, "editable_projects": editable})
    return render(request, "ale/experiment_new.html", context)


@require_POST
def project_create(request):
    """Create a project, optionally with its first experiment in the same step."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "A project name is required."}, status=400)

    project = Project.objects.create(
        name=name,
        user=request.user,
        is_public=bool(request.POST.get("is_public")),
        status="in progress",
        description=(request.POST.get("description") or "").strip())
    set_primary_owner(project, request.user)

    payload = {"project_id": project.id, "project": project.name, "experiment_id": None}

    experiment_name = (request.POST.get("experiment") or "").strip()
    if experiment_name:
        experiment = _create_experiment(project, experiment_name, request.user)
        payload["experiment_id"] = experiment.ale_id
        payload["experiment"] = experiment.name
    return JsonResponse(payload)


@require_POST
def experiment_create(request):
    """Create an experiment under an existing project."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "An experiment name is required."}, status=400)

    project = get_object_or_404(Project, pk=request.POST.get("project"))
    if not can_edit_project(request.user, project):
        return JsonResponse({"error": "You cannot add to this project."}, status=403)

    experiment = _create_experiment(project, name, request.user)
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "experiment": experiment.name,
                         "project_id": project.id})


def _create_experiment(project, name, user):
    """Experiments are identified by primary key, so a duplicate name is allowed."""
    import aledb_metadata.parser as metadata_defaults
    from aledb_experiment.models import Instrument

    instrument, _ = Instrument.objects.get_or_create(
        name=metadata_defaults.DEFAULT_INSTRUMENT_NAME)
    return AleExperiment.objects.create(
        name=name, project=project, instrument=instrument, person=user.get_username())


#: The dashboard's installation-wide totals, which a soft delete changes and nothing else
#: here does. Named rather than requesting everything: removing one experiment cannot make
#: another's needle plot or fixation table wrong, and `request_rebuild()` with no experiment
#: would mark every one of them.
_AGGREGATE_REBUILDS = ("sample_counts", "mutation_counts")


@require_POST
def project_delete(request, pk):
    # Admin, not `can_edit_project`. Editing widened to `write` when roles arrived, and
    # letting everyone who may add data also delete the whole project is not what anyone
    # means by "may add data". Admin rather than owner because the delete is soft and
    # `purge_deleted` gives a retention window.
    project = get_object_or_404(Project, pk=pk)
    if not can_delete_project(request.user, project):
        return JsonResponse({"error": "You cannot delete this project."}, status=403)

    # A locked experiment cannot be deleted, and deleting the project that holds it would
    # take it away just the same -- so the lock has to reach one button sideways or it is
    # sidestepped by the most obvious route there is. Named rather than counted: the person
    # has to know which one to go and unlock.
    locked = list(live(project.aleexperiment_set.all())
                  .filter(locked_at__isnull=False).values_list("name", flat=True))
    if locked:
        return JsonResponse(
            {"error": "This project holds locked experiments (%s). Unlock them first."
                      % ", ".join(locked)}, status=409)

    if project.deleted_at is None:
        project.soft_delete(request.user)
        _mark_totals_stale('project deleted')
    return JsonResponse({"project_id": project.id, "deleted_at": project.deleted_at})


def experiment_ancestor(request, pk):
    """The page that designates one sample as this experiment's ancestor.

    A page of its own rather than a field on the edit form, for the reason
    `experiment_lock` gives: a details form rewrites every field it carries on every save,
    and this one needs to say what it is about to do before it does it.

    Rendered for anyone who can view the experiment; the list is read-only without write
    access, and `experiment_ancestor_apply` refuses the write regardless -- the button being
    hidden is not a permission check.
    """
    experiment = get_object_or_404(AleExperiment, pk=pk)
    context = get_user_context(request.user)
    if not can_view_project(request.user, experiment.project):
        return render(request, "403.html", context, status=403)

    from aledb_seq.util import get_ordered_reseq_queryset

    # `include_ancestor=True`: the current ancestor has to appear in the list that changes it.
    samples = list(get_ordered_reseq_queryset(experiment.ale_id, include_ancestor=True))
    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "samples": samples,
        "selected_ids": [experiment.ancestor_id] if experiment.ancestor_id else [],
        "can_edit": can_edit_experiment(request.user, experiment),
        "lock_refusal": experiment_lock_refusal(experiment),
        "title": "%s ancestor" % experiment.name,
        "template_header": "Designate ancestor",
    })
    return render(request, "experiment/ancestor.html", context)


@require_POST
def experiment_ancestor_apply(request, pk):
    """Set or clear the designated ancestor. Write access, and not while locked.

    Posting no `reseq_id` clears the designation, which is what the "No ancestor" row does.

    **`can_edit_experiment`, not `can_edit_project`.** A predicate handed the project cannot
    see the lock on the experiment, and this is exactly the kind of write a lock exists to
    stop: it changes what every reader of a finished dataset sees. It also means a locked
    experiment cannot have its ancestor *cleared* either, which is correct -- an admin
    unlocks, changes it, and locks it again.
    """
    experiment = get_object_or_404(AleExperiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse(
            {"error": (experiment_lock_refusal(experiment)
                       or "You do not have permission to change this experiment.")},
            status=403)

    raw = (request.POST.get("reseq_id") or "").strip()
    if not raw:
        experiment.clear_ancestor()
    else:
        from aledb_seq.util import get_ordered_reseq_queryset
        try:
            reseq_id = int(raw)
        except (TypeError, ValueError):
            return JsonResponse({"error": "That is not a sample id."}, status=400)

        # Resolved out of *this experiment's* samples rather than by bare pk. Nothing in the
        # schema stops the column pointing at another experiment's sample, and a mutation
        # belongs to one experiment's reference genome -- subtracting a foreign sample's
        # mutations would be meaningless where it was not simply a no-op.
        reseq = get_ordered_reseq_queryset(
            experiment.ale_id, include_ancestor=True).filter(pk=reseq_id).first()
        if reseq is None:
            return JsonResponse(
                {"error": "That sample is not in this experiment."}, status=404)
        experiment.set_ancestor(reseq, request.user)

    # Everything derived changes: the ancestor's mutations leave or rejoin every other sample.
    # Marked *and* run, the shape `aledb_mutation_editor.history.rebuild_after_edit` uses for
    # "the mutations effectively changed" -- experiment scope now, so phylogeny's cached trees
    # are discarded before anyone reads one; site scope left marked for the dashboard's own
    # `ensure_fresh`.
    from aledb_common.rebuild_registry import EXPERIMENT_SCOPE, request_rebuild, run_rebuilds
    request_rebuild(experiment.ale_id, reason="ancestor designated")
    run_rebuilds(experiment.ale_id, scope=EXPERIMENT_SCOPE)

    logger.info("ancestor designated", extra=user_extra(request))
    return JsonResponse({
        "experiment_id": experiment.ale_id,
        "ancestor_id": experiment.ancestor_id,
        "ancestor_set_by": (experiment.ancestor_set_by.get_username()
                            if experiment.ancestor_set_by_id else None),
    })


@require_POST
def experiment_lock(request, pk):
    """Lock or unlock an experiment. Admin on its project.

    Its own endpoint rather than a field on the edit form, for the reason `experiment_update`
    gives about `person`: a details form rewrites every field it carries on every save. Here
    that argument is doubled, because a locked experiment refuses `experiment_update`
    outright -- a lock checkbox on that form could only ever be used to lock, never to unlock.

    Idempotent, like `project_delete`: locking a locked experiment is not an error, it just
    does not move the timestamp.
    """
    experiment = get_object_or_404(AleExperiment, pk=pk)
    if not can_lock_experiment(request.user, experiment):
        return JsonResponse(
            {"error": "Only an administrator of this project can lock or unlock it."},
            status=403)

    wants_locked = bool(request.POST.get("locked"))
    if wants_locked and not experiment.is_locked:
        experiment.lock(request.user)
    elif not wants_locked and experiment.is_locked:
        experiment.unlock()

    return JsonResponse({"experiment_id": experiment.ale_id,
                         "locked": experiment.is_locked,
                         "locked_at": experiment.locked_at,
                         "locked_by": (experiment.locked_by.get_username()
                                       if experiment.locked_by_id else None)})


@require_POST
def experiment_delete(request, pk):
    experiment = get_object_or_404(AleExperiment, pk=pk)
    if not can_delete_experiment(request.user, experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(experiment)
                      or "You cannot delete this experiment."}, status=403)
    if experiment.deleted_at is None:
        experiment.soft_delete(request.user)
        _mark_totals_stale('experiment deleted')
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "deleted_at": experiment.deleted_at})


def _mark_totals_stale(reason):
    """Marked, not rebuilt. The dashboard recounts on its next view.

    Deleting is a fast operation and recounting the installation is not -- and the person who
    just deleted something is on a list page, not the dashboard.
    """
    from aledb_common.rebuild_registry import request_rebuild

    request_rebuild(only=_AGGREGATE_REBUILDS, reason=reason)


# --- edit --------------------------------------------------------------------------------
#
# Creating and deleting were the first two thirds of this; until now a project or experiment
# could be brought into existence and taken out of it, but not corrected. The summary on
# ale/project_detail.html carries the comment about what the previous attempt looked like:
# editable inputs in a form that posted nowhere, silently discarding anything typed. That
# summary stays read-only. Editing is a page of its own, like creating, for the same reasons
# -- a heading, room to explain a field, and a URL you can send someone.


def project_edit(request, pk):
    """The edit-a-project form."""
    project = get_object_or_404(Project, pk=pk)
    context = get_user_context(request.user)
    if not can_edit_project(request.user, project):
        return render(request, "403.html", context, status=403)

    context.update({"project": project, "statuses": Project.PROJECT_STATUS})
    return render(request, "ale/project_edit.html", context)


def experiment_edit(request, pk):
    """The edit-an-experiment form.

    The project picker lists only what `can_edit_project` allows, so it cannot offer a
    destination the POST would refuse -- the same rule `experiment_new` follows.

    `can_edit_experiment` rather than `can_edit_project`: a locked experiment refuses this
    page as well as the endpoint behind it, so there is no form to fill in and be refused at
    the end of.
    """
    experiment = get_object_or_404(AleExperiment, pk=pk)
    context = get_user_context(request.user)
    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", context, status=403)

    context.update({
        "experiment": experiment,
        "editable_projects": _editable_projects(request.user),
    })
    return render(request, "ale/experiment_edit.html", context)


@require_POST
def project_update(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit_project(request.user, project):
        return JsonResponse({"error": "You cannot edit this project."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "A project name is required."}, status=400)
    if len(name) > 50:
        return JsonResponse(
            {"error": "A project name is at most 50 characters."}, status=400)

    description = (request.POST.get("description") or "").strip()
    if len(description) > 300:
        return JsonResponse(
            {"error": "A project description is at most 300 characters."}, status=400)

    status = (request.POST.get("status") or "").strip()
    valid = [value for value, _label in Project.PROJECT_STATUS]
    if status and status not in valid:
        return JsonResponse({"error": "Unknown project status."}, status=400)

    project.name = name
    project.description = description
    project.status = status or project.status
    project.is_public = bool(request.POST.get("is_public"))
    project.save(update_fields=["name", "description", "status", "is_public"])
    return JsonResponse({"project_id": project.id, "project": project.name})


@require_POST
def experiment_update(request, pk):
    """Save an experiment, possibly moving it to another project.

    The move is checked against **both** ends. Checking only the destination would let you
    take an experiment out of a project you have no say over; checking only the source
    would let you push your experiment into someone else's.
    """
    experiment = get_object_or_404(AleExperiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        # Refuses a locked experiment too, which stops the *move* as well as the rename --
        # the destination check below is about the other end and would not catch it.
        return JsonResponse(
            {"error": experiment_lock_refusal(experiment)
                      or "You cannot edit this experiment."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "An experiment name is required."}, status=400)
    if len(name) > 200:
        return JsonResponse(
            {"error": "An experiment name is at most 200 characters."}, status=400)

    project = experiment.project
    requested = (request.POST.get("project") or "").strip()
    if requested and str(requested) != str(experiment.project_id):
        project = get_object_or_404(Project, pk=requested)
        if not can_edit_project(request.user, project):
            return JsonResponse({"error": "You cannot move it into that project."},
                                status=403)

    # `person` is deliberately absent, here and from the form. Who owns or created a
    # thing is its own workflow; folding it into a details form means every save rewrites
    # it, and a form that omitted the field would silently blank it.
    experiment.name = name
    experiment.notes = (request.POST.get("notes") or "").strip()
    experiment.doi = (request.POST.get("doi") or "").strip()
    experiment.project = project
    experiment.save(update_fields=["name", "notes", "doi", "project"])
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "experiment": experiment.name,
                         "project_id": project.id if project else None})
