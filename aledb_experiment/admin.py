from django.contrib import admin, messages
from django import forms
from aledb_experiment.models import (
    AleExperiment, AleGroup, AleGroupMembership, Media, Project, ProjectAccess,
)
from aledb_experiment.permissions import (
    AccessError, grant_project_access, revoke_project_access, set_primary_owner,
)


class ExperimentInline(admin.TabularInline):
    model = AleExperiment
    extra = 0
    fields = ('name', 'person')


@admin.register(AleExperiment)
class AleExperimentAdmin(admin.ModelAdmin):
    # fetch project
    list_select_related = ('project',)
    list_display = ('id', 'name', 'project', 'person', 'date')
    search_fields = ('name', 'person', 'project')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Ownership changes here go through `set_primary_owner`, like everywhere else.

    `Project.user` and an owner `ProjectAccess` row are two statements of one fact, and
    `effective_role` grants owner from either alone. A plain ModelAdmin writes the field and
    not the row, which leaves the new owner holding power the sharing page cannot show and the
    old one still an owner through their untouched row. The admin is a superuser tool, but it
    should not be the one place able to express a state the application forbids.
    """

    list_display = ('id', 'name', 'user', 'date', 'status', 'is_public')
    search_fields = ('name', 'user')
    # inlines = [ExperimentInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.user_id is not None:
            set_primary_owner(obj, obj.user, granted_by=request.user)


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ('id', 'description', 'experiments')
    search_fields = ('name', 'user')
    # inlines = [ExperimentInline]


class MembershipInline(admin.TabularInline):
    model = AleGroupMembership
    extra = 0
    fields = ('user', 'is_manager')


@admin.register(AleGroup)
class AleGroupAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'owner', 'created')
    search_fields = ('name',)
    inlines = [MembershipInline]


@admin.register(ProjectAccess)
class ProjectAccessAdmin(admin.ModelAdmin):
    """Grants written here take the same route the sharing page and the CLI take.

    Editing this table directly would otherwise bypass the last-owner invariant and the
    re-point of `Project.user` -- deleting the primary owner's row leaves a project whose only
    owner is a field nothing can reach, which is the desync in its most durable form.

    A refusal is reported as a message rather than raised: `AccessError` is a rule about
    access, and a 500 is the wrong way to say "a project must have an owner".
    """

    list_select_related = ('project', 'user', 'group')
    list_display = ('id', 'project', 'user', 'group', 'role', 'granted_at')
    list_filter = ('role',)
    search_fields = ('project__name', 'user__username', 'group__name')

    def save_model(self, request, obj, form, change):
        try:
            grant_project_access(obj.project, obj.user or obj.group, obj.role,
                                 granted_by=request.user)
        except AccessError as error:
            self.message_user(request, str(error), level=messages.ERROR)

    def delete_model(self, request, obj):
        try:
            revoke_project_access(obj.project, obj)
        except AccessError as error:
            self.message_user(request, str(error), level=messages.ERROR)

    def delete_queryset(self, request, queryset):
        # One at a time, and deliberately: the last-owner rule is a cross-row dependency, so a
        # batch deleted in bulk would step past it. Same reasoning as `project_access_bulk`.
        for entry in queryset:
            self.delete_model(request, entry)
