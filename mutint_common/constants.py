__author__ = "Patrick Phaneuf"

REQUEST_ALL = "all"
REQUEST_MUTATION_ID = "mutation_id"

#: The query string every experiment-scoped page carries. It was `experiment_id`, which
#: named a model that no longer exists.
REQUEST_EXPERIMENT_ID = "experiment_id"

#: Which population to narrow to. It was `population` -- a *number*, for a column that has been
#: text since `mutint_experiment.0008` and holds `Ara-1` as readily as `3`.
REQUEST_POPULATION = "population"

#: One sample, by primary key. It was `sample_id`, after `ResequencingExperiment`.
REQUEST_SAMPLE_ID = "sample_id"

REQUEST_SAMPLE_TYPE = "sample_type"

#: What `?sample_type=` accepts. Routing these through constants is what made the second one
#: changeable at all: it was `population`, which is now the *model* one level up and is about
#: to be a parameter name too, so for one commit the word meant two things. It means one now.
SAMPLE_TYPE_CLONAL = "clonal"
SAMPLE_TYPE_MIXED = "mixed"
SAMPLE_TYPES = (SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED)
# REFSEQ_COLUMN_IN_MUT_TABLE, HTML_MUTATION_TABLE_HEADER and FIRST_SAMPLE_COLUMN_IN_MUT_TABLE
# stood here: the shared cross-sample table was a DataTable of positional arrays, and every
# consumer located a column by arithmetic on these. The mutation matrix
# (`mutint_sample.mutation_matrix`) reads cells by name, so there is no index for anything to
# agree about; the CSV export carries its own header in `mutint_export.util`.

TAGS = {
    "contaminated": '<i class="fa fa-random fa-fw" aria-hidden="true"></i>',
    "hypermutated": '<i class="fa fa-line-chart fa-fw" aria-hidden="true"></i>',
    "fixating":     '<i class="fa fa-signal fa-fw" aria-hidden="true"></i>',
}

COLUMN_TAGS = ["contaminated", "hypermutated"]

ROW_TAGS = ["contaminated", "hypermutated", "fixating"]
