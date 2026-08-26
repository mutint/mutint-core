from django.contrib import admin
from django import forms
from aledb_filter.models import AleExperimentFilter


@admin.register(AleExperimentFilter)
class FilterAdmin(admin.ModelAdmin):
    list_display = ('id', 'ale_experiment', 'min_cutoff', 'max_cutoff')
    search_fields = ('id', 'ale_experiment')
