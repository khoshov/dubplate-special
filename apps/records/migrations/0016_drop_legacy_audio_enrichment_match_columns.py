from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0015_reconcile_audio_enrichment_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE records_audioenrichmenttrackresult
            DROP COLUMN IF EXISTS matched_title,
            DROP COLUMN IF EXISTS matched_artist;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
