"""Drop `AleExperimentFilter`. Filtering belongs to the reader now.

The row held one experiment's `min_cutoff`, `max_cutoff` and `ignored_genes`, shared by every
user of the deployment. A reader's filter lives in their session instead -- see
`aledb_filter/view_filter.py` -- so there is no stored value to migrate to, and pages open
unfiltered rather than inheriting somebody else's setting.

**Nothing is folded forward, and that is a real loss rather than a technicality**, which is why
this logs what it is about to discard. `aledb_filter.0005` could fold the global ignored-gene
list into each experiment because there was somewhere to put it; there is nowhere now. A
deployment may have had a genuine curatorial decision in these rows -- "calls below 20% in this
dataset are noise" is a fact about the data, not a preference -- and dropping it without a trace
in the `migrate` output would lose it silently. The log line is the trace.

What is gained is that the same fact stops being enforced on everyone by whoever last edited the
page, and starts being something each reader chooses and can see stated under the table.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def log_what_is_lost(apps, schema_editor):
    """Say what these rows held, once, before they go."""
    AleExperimentFilter = apps.get_model("aledb_filter", "AleExperimentFilter")

    described = []
    for row in AleExperimentFilter.objects.all():
        low, high = row.min_cutoff or 0, 100 if row.max_cutoff is None else row.max_cutoff
        genes = (row.ignored_genes or "").strip()
        if low == 0 and high == 100 and not genes:
            continue        # configured and hiding nothing; there is nothing to say about it
        described.append("experiment %s: %d-%d%%%s"
                         % (row.ale_experiment_id, low, high,
                            ", ignoring %s" % genes if genes else ""))
    if described:
        logger.warning(
            "dropping %d experiment filter(s); readers now set their own. Previously: %s",
            len(described), "; ".join(described))


def nothing_to_restore(apps, schema_editor):
    """A no-op. Reversing the schema cannot bring back values this migration did not keep."""


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_filter", "0005_drop_global_filter"),
    ]

    operations = [
        migrations.RunPython(log_what_is_lost, nothing_to_restore),
        migrations.DeleteModel(name="AleExperimentFilter"),
    ]
