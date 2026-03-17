from django.db import migrations, models


def set_active_structured_format_variant(apps, schema_editor) -> None:
    Record = apps.get_model("records", "Record")

    for record in Record.objects.all():
        variants = list(
            record.structured_formats.order_by("variant_of_format", "id").values_list(
                "variant_of_format",
                flat=True,
            )
        )
        record.active_structured_format_variant = variants[0] if variants else None
        record.save(update_fields=["active_structured_format_variant"])


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0012_normalize_legacy_format_library"),
    ]

    operations = [
        migrations.AddField(
            model_name="record",
            name="active_structured_format_variant",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                verbose_name="Активный вариант формата",
            ),
        ),
        migrations.RenameModel(
            old_name="RecordFormatEntry",
            new_name="StructuredFormat",
        ),
        migrations.RenameField(
            model_name="structuredformat",
            old_name="sort_order",
            new_name="variant_of_format",
        ),
        migrations.AlterField(
            model_name="structuredformat",
            name="variant_of_format",
            field=models.PositiveSmallIntegerField(verbose_name="Вариант формата"),
        ),
        migrations.AlterModelOptions(
            name="structuredformat",
            options={
                "verbose_name": "Структурированный формат релиза",
                "verbose_name_plural": "Структурированные форматы релиза",
                "ordering": ("record", "variant_of_format", "id"),
            },
        ),
        migrations.RemoveConstraint(
            model_name="structuredformat",
            name="uq_recordformatentry_record_sort_order",
        ),
        migrations.AddConstraint(
            model_name="structuredformat",
            constraint=models.UniqueConstraint(
                fields=("record", "variant_of_format"),
                name="uq_structuredformat_record_variant_of_format",
            ),
        ),
        migrations.RunPython(
            set_active_structured_format_variant,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
