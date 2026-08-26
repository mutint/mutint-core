from django.contrib import admin
from django import forms
from aledb_experiment.models import (
    AleExperiment, AleGroup, AleGroupMembership, Media, Project, ProjectAccess,
)


class ExperimentInline(admin.TabularInline):
    model = AleExperiment
    extra = 0
    fields = ('name', 'person')


@admin.register(AleExperiment)
class AleExperimentAdmin(admin.ModelAdmin):
    # fetch project
    list_select_related = ('project',)
    list_display = ('ale_id', 'name', 'project', 'person', 'date')
    search_fields = ('name', 'person', 'project')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'user', 'date', 'status', 'is_public')
    search_fields = ('name', 'user')
    # inlines = [ExperimentInline]


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
    list_select_related = ('project', 'user', 'group')
    list_display = ('id', 'project', 'user', 'group', 'role', 'granted_at')
    list_filter = ('role',)
    search_fields = ('project__name', 'user__username', 'group__name')
