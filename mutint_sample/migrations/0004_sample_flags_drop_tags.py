"""Three sample flags in, two tag columns out.

`Sample.tags` and `Mutation.tags` were comma-joined text with a three-word vocabulary --
`hypermutated`, `contaminated`, `fixating` -- written by the cross-sample table's tag menus and
the sample edit page, and read by nothing. The two words that mean something about a sample
become booleans; anything else somebody typed is kept, verbatim, in `supplemental_data` rather
than thrown away, because a deployment's database may hold curation nobody here can see.

**The first `RunPython` in the repository.** There was none before because the migration
history was regenerated from scratch; that is an accident of history, not a rule, and a column
that may hold real data is exactly what a data step exists for. Kept small enough to read: one
pass over each table, no bulk update, and a no-op reverse -- the columns come back empty.
"""

from django.db import migrations, models

COMPONENT = "mutint_core"
CURATION = "curation"


def split_tags(text):
    """(hypermutator, contaminated, leftover) from a comma-joined tag string.

    `fixating` is dropped without a trace: it duplicated what the Fixed Mutations plugin
    computes. Everything else that is not one of the two flag words is the leftover, joined
    back with commas in its original order.
    """
    words = [word.strip() for word in (text or "").split(",") if word.strip()]
    hypermutator = any(word.lower() == "hypermutated" for word in words)
    contaminated = any(word.lower() == "contaminated" for word in words)
    leftover = [word for word in words
                if word.lower() not in ("hypermutated", "contaminated", "fixating")]
    return hypermutator, contaminated, ",".join(leftover)


def _keep(supplemental, path, value):
    node = supplemental
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def forward(apps, schema_editor):
    Sample = apps.get_model("mutint_sample", "Sample")
    for sample in Sample.objects.exclude(tags__isnull=True).exclude(tags="").iterator():
        hypermutator, contaminated, leftover = split_tags(sample.tags)
        sample.is_hypermutator = hypermutator
        sample.is_contaminated = contaminated
        fields = ["is_hypermutator", "is_contaminated"]
        if leftover:
            data = dict(sample.supplemental_data or {})
            _keep(data, (COMPONENT, CURATION, "legacy_tags"), leftover)
            sample.supplemental_data = data
            fields.append("supplemental_data")
        sample.save(update_fields=fields)

    Mutation = apps.get_model("mutint_sample", "Mutation")
    for mutation in Mutation.objects.exclude(tags__isnull=True).exclude(tags="").iterator():
        _, _, leftover = split_tags(mutation.tags)
        if not leftover:
            continue
        data = dict(mutation.supplemental_data or {})
        _keep(data, (COMPONENT, "legacy_tags"), leftover)
        mutation.supplemental_data = data
        mutation.save(update_fields=["supplemental_data"])


class Migration(migrations.Migration):

    dependencies = [
        ('mutint_sample', '0003_sample_report_stored'),
    ]

    operations = [
        migrations.AddField(model_name='sample', name='is_hypermutator',
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name='sample', name='is_contaminated',
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name='sample', name='is_low_coverage',
                            field=models.BooleanField(default=False)),
        migrations.RunPython(forward, migrations.RunPython.noop),
        migrations.RemoveField(model_name='sample', name='tags'),
        migrations.RemoveField(model_name='mutation', name='tags'),
    ]
