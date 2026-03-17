from django.db import migrations, transaction


LEGACY_FORMAT_LIBRARY = (
    "Not specified",
    '12" Vinyl',
    '2x12" Vinyl',
    '10" Vinyl',
    '7" Vinyl',
    '3x12" Vinyl',
    '4x12" Vinyl',
    '5x12" Vinyl',
)
DEFAULT_LEGACY_FORMAT = "Not specified"


def normalize_legacy_format_library(apps, schema_editor) -> None:
    Format = apps.get_model("records", "Format")
    Record = apps.get_model("records", "Record")
    using = schema_editor.connection.alias

    allowed_lookup = {name.casefold(): name for name in LEGACY_FORMAT_LIBRARY}

    with transaction.atomic(using=using):
        canonical_formats = {}
        for name in LEGACY_FORMAT_LIBRARY:
            obj, _ = Format.objects.using(using).get_or_create(name=name)
            canonical_formats[name] = obj

        default_format = canonical_formats[DEFAULT_LEGACY_FORMAT]

        for record in Record.objects.using(using).all():
            current_formats = list(record.formats.using(using).all())
            normalized_names: list[str] = []
            seen: set[str] = set()
            has_invalid_format = not current_formats

            for format_obj in current_formats:
                raw_name = str(getattr(format_obj, "name", "") or "").strip()
                canonical_name = allowed_lookup.get(raw_name.casefold())
                if canonical_name is None:
                    has_invalid_format = True
                    break

                canonical_key = canonical_name.casefold()
                if canonical_key in seen:
                    continue
                seen.add(canonical_key)
                normalized_names.append(canonical_name)

            if has_invalid_format or not normalized_names:
                record.formats.set([default_format])
                continue

            record.formats.set([canonical_formats[name] for name in normalized_names])

        Format.objects.using(using).exclude(name__in=LEGACY_FORMAT_LIBRARY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0011_recordformatentry"),
    ]

    operations = [
        migrations.RunPython(
            normalize_legacy_format_library,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
