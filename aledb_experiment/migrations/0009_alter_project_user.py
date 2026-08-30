"""`Project.user` becomes PROTECT, so an owner cannot be deleted out from under a project.

Schema-only, and on SQLite it changes no behaviour the database was not already enforcing: the
column is NOT NULL with a real FK, so the delete failed either way. What changes is where the
refusal comes from -- Django's `ProtectedError`, raised before anything is written and naming
the projects, rather than an `IntegrityError` surfacing from the database at commit with the
admin's confirmation page having promised the delete would succeed.

No data to repair: ownership is transferred, and a project whose `user` names a missing row
could not exist under the constraint that was already there.
"""


from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('aledb_experiment', '0008_alter_aleid_ale_id_alter_isolate_isolate_number'),
    ]

    operations = [
        migrations.AlterField(
            model_name='project',
            name='user',
            field=models.ForeignKey(default=None, help_text='project owner', on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL),
        ),
    ]
