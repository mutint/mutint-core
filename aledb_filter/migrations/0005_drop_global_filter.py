"""Fold the site-wide ignored genes into each experiment's list, then drop `GlobalFilter`.

`GlobalFilter` was a second ignored-gene list, one row for the whole installation
(`get_or_create(id=1)`), editable only by a superuser, and reachable only by typing its URL --
the sole reference to its page anywhere was a commented-out sidebar entry. `AleExperimentFilter`
already carries `ignored_genes` with identical semantics, so what goes is the ability to hide a
gene everywhere at once rather than per experiment.

**The fold happens first, and is the reason this is a data migration rather than a
RemoveField.** Dropping the table on a deployment that had used it would silently un-hide those
genes in every experiment, with nothing to say why a table suddenly grew rows. Each global gene
is appended to every experiment filter that does not already list it, so what was hidden stays
hidden and becomes editable where it now lives. This is the posture
`aledb_mutation_editor.0002` took with the ignored *mutation* lists: convert, then drop.

It is a no-op on a deployment that never used one, which is every deployment we know of.
"""

from django.db import migrations


def _genes(raw):
    """The column's comma-joined list, as an ordered list of non-empty names."""
    return [name.strip() for name in (raw or "").split(",") if name.strip()]


def fold_into_experiments(apps, schema_editor):
    GlobalFilter = apps.get_model("aledb_filter", "GlobalFilter")
    AleExperimentFilter = apps.get_model("aledb_filter", "AleExperimentFilter")

    global_genes = []
    for row in GlobalFilter.objects.all():
        for gene in _genes(row.ignored_genes):
            if gene not in global_genes:
                global_genes.append(gene)
    if not global_genes:
        return

    for exp_filter in AleExperimentFilter.objects.all():
        existing = _genes(exp_filter.ignored_genes)
        added = [gene for gene in global_genes if gene not in existing]
        if not added:
            continue
        exp_filter.ignored_genes = ", ".join(existing + added)
        exp_filter.save(update_fields=["ignored_genes"])


def unfold(apps, schema_editor):
    """Deliberately a no-op rather than an error.

    Reversing re-creates an empty `GlobalFilter`; it cannot know which of an experiment's genes
    arrived from it, and guessing would remove genes somebody had set locally. Refusing to
    reverse at all would make the whole migration irreversible for the sake of a list that can
    be retyped.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_filter", "0004_drop_gatk_cutoffs"),
    ]

    operations = [
        migrations.RunPython(fold_into_experiments, unfold),
        migrations.DeleteModel(name="GlobalFilter"),
    ]
