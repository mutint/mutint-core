"""Drop `StaticData` and `ExperimentSummary`. Both are computed on demand now.

Nothing is folded forward. Every row in either table was a function of the experiment's
mutations and its filter, so the same answer is produced by asking -- verified identical to
what was stored, element for element, on the dev database's largest experiments before the
tables were removed.

`StaticData` is the older of the two and the one to check after upgrading: it had no foreign
key to `AleExperiment`, only the convention that its primary key *was* the experiment's, which
is why two separate delete paths had to sweep it by hand. Dropping the table is also the end of
that obligation.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_stats", "0003_experimentsummary"),
    ]

    operations = [
        migrations.DeleteModel(name="ExperimentSummary"),
        migrations.DeleteModel(name="StaticData"),
    ]
