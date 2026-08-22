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


class ReferenceUploadForm(forms.Form):
    """Step one of the two-step import: the experiment and its reference genome.

    Accepts GenBank, GFF3, or FASTA; all three are normalized to one canonical pair before
    being stored, so the format a user happens to have does not affect the shared-reference
    check later.
    """

    project = forms.CharField(max_length=50,
                              help_text="Project to file the experiment under (created if new).")
    experiment = forms.CharField(max_length=200, help_text="AleExperiment name.")
    person = forms.CharField(max_length=200, required=False)
    reference = forms.FileField(
        help_text="Reference genome: .gbk/.gb, .gff/.gff3, or .fa/.fasta/.fna.")
    is_public = forms.BooleanField(required=False)
    replace = forms.BooleanField(
        required=False,
        help_text="Overwrite an existing reference on this experiment.")

    def clean_project(self):
        return self.cleaned_data["project"].strip()

    def clean_experiment(self):
        return self.cleaned_data["experiment"].strip()
