from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0027_alter_audioenrichmentjob_options_and_more"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="AudioEnrichmentJob",
            new_name="ReleaseJob",
        ),
        migrations.RenameModel(
            old_name="AudioEnrichmentJobRecord",
            new_name="ReleaseReport",
        ),
        migrations.RenameModel(
            old_name="VKPublicationJobRecord",
            new_name="VKPublicationReport",
        ),
    ]
