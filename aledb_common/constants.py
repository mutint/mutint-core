__author__ = "Patrick Phaneuf"

REQUEST_ALL = "all"
REQUEST_MUTATION_ID = "mutation_id"
REQUEST_ALE_EXPERIMENT_ID = "ale_experiment_id"
REQUEST_ALE_ID = "ale_no"
REQUEST_SAMPLE_TYPE = "sample_type"

#: What `?sample_type=` accepts. Routing these through constants is what made the second one
#: changeable at all: it was `population`, which is now the *model* one level up and is about
#: to be a parameter name too, so for one commit the word meant two things. It means one now.
SAMPLE_TYPE_CLONAL = "clonal"
SAMPLE_TYPE_MIXED = "mixed"
SAMPLE_TYPES = (SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED)
# Was 3. The table's first column used to hold a close icon that removed the row from the
# client-side DataTable and nothing else -- it came back on the next reload, which is the same
# confusion `aledb_mutation_editor` exists to end. Dropping it shifts every column left by one.
# Everything in `table_template.js` is expressed relative to this constant; the two places that
# were not are `aledb_export.util`'s `mut_pos_index` and the row builder itself.
REFSEQ_COLUMN_IN_MUT_TABLE = 2
# Function, GO Process and GO Component were columns here and are gone with the three
# Mutation fields behind them: nothing had written those since a helper documented as
# "executed from Django ipython shell", so every modern row rendered three empty cells and
# exported three empty columns. They sit *after* REFSEQ_COLUMN_IN_MUT_TABLE, so the constant
# above is unchanged -- which is the only reason this was safe to do in one edit.
HTML_MUTATION_TABLE_HEADER = ["", "Tags", "Reference Seq", "Position", "Mutation Type",
                              "Sequence Change", "Gene (Scrollable)", "Product", "Mut ID",
                              "Details"]

TAGS = {
    "contaminated": '<i class="fa fa-random fa-fw" aria-hidden="true"></i>',
    "hypermutated": '<i class="fa fa-line-chart fa-fw" aria-hidden="true"></i>',
    "fixating":     '<i class="fa fa-signal fa-fw" aria-hidden="true"></i>',
}

COLUMN_TAGS = ["contaminated", "hypermutated"]

ROW_TAGS = ["contaminated", "hypermutated", "fixating"]
