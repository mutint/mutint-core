from django import forms


class GenomeDiffUploadForm(forms.Form):
    """Validates a drag-and-drop batch of GenomeDiff (.gd) files plus the target
    experiment they should be imported into."""

    project = forms.CharField(max_length=50,
                              help_text="Project to file the experiment under (created if new).")
    experiment = forms.CharField(max_length=200,
                                 help_text="AleExperiment name (defaults to the .gd #=TITLE).")
    person = forms.CharField(max_length=200, required=False)

    def clean_project(self):
        return self.cleaned_data["project"].strip()

    def clean_experiment(self):
        return self.cleaned_data["experiment"].strip()
