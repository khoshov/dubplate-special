# Squashed 0006–0020
from django.db import migrations, models
import django.db.models.deletion
import sorl.thumbnail.fields

# --- Локальный аналог PathByInstance, чтобы не тянуть apps.records.models в миграции ---
def path_by_instance(field):
    def _path(instance, filename):
        # если pk уже есть — складываем по pk, иначе без него
        if getattr(instance, "pk", None):
            return f"records/{field}/{instance.pk}/{filename}"
        return f"records/{field}/{filename}"
    return _path


GENRES = ["Not specified", "Electronic"]
STYLES = ["Not specified", "Bass Music", "Drum n Bass"]
CANON = "Not specified"


def _merge_ci(apps, model_name: str, through, fk_name: str):
    Model = apps.get_model("records", model_name)
    qs = Model.objects.filter(name__iexact="not specified").order_by("id")
    candidates = list(qs)
    if not candidates:
        return

    keeper = next((x for x in candidates if x.name == CANON), candidates[0])
    victims = [x for x in candidates if x.id != keeper.id]
    if not victims:
        if keeper.name != CANON:
            Model.objects.filter(id=keeper.id).update(name=CANON)
        return

    keeper_rec_ids = set(
        through.objects.filter(**{fk_name: keeper}).values_list("record_id", flat=True)
    )
    for v in victims:
        if keeper_rec_ids:
            through.objects.filter(
                **{fk_name + "_id": v.id, "record_id__in": keeper_rec_ids}
            ).delete()
        through.objects.filter(**{fk_name + "_id": v.id}).update(**{fk_name + "_id": keeper.id})

    Model.objects.filter(id__in=[v.id for v in victims]).delete()
    if keeper.name != CANON:
        Model.objects.filter(id=keeper.id).update(name=CANON)


def seed_and_normalize_forward(apps, schema_editor):
    Genre = apps.get_model("records", "Genre")
    Style = apps.get_model("records", "Style")
    Record = apps.get_model("records", "Record")

    _merge_ci(apps, "Genre", Record.genres.through, "genre")
    _merge_ci(apps, "Style", Record.styles.through, "style")

    Genre.objects.filter(name__iexact="not specified").update(name=CANON)
    Style.objects.filter(name__iexact="not specified").update(name=CANON)

    existing_genres = set(Genre.objects.values_list("name", flat=True))
    existing_styles = set(Style.objects.values_list("name", flat=True))

    for name in GENRES:
        if name not in existing_genres:
            Genre.objects.create(name=name)
    for name in STYLES:
        if name not in existing_styles:
            Style.objects.create(name=name)

    Genre.objects.exclude(name__in=GENRES).delete()
    Style.objects.exclude(name__in=STYLES).delete()


def seed_backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0005_alter_format_name_alter_genre_name_alter_style_name"),
    ]

    replaces = [
        ("records", "0006_seed_presets"),
        ("records", "0007_alter_record_cover_image_alter_track_position"),
        ("records", "0008_record_is_expected_record_release_day_and_more"),
        ("records", "0009_alter_genre_name_alter_record_cover_image_and_more"),
        ("records", "0010_prune_genres_styles"),
        ("records", "0011_alter_genre_name_alter_style_name"),
        ("records", "0012_seed_genres_styles"),
        ("records", "0013_fix_not_specified"),
        ("records", "0014_alter_format_name"),
        ("records", "0015_merge_not_specified"),
        ("records", "0016_squash_not_specified_ci"),
        ("records", "0017_ci_unique_on_vocab_names"),
        ("records", "0018_track_numeric_order"),
        ("records", "0019_alter_track_position_index_and_more"),
        ("records", "0020_recordsource_remove_track_track_position_index_gte_1_and_more"),
    ]

    operations = [
        # RECORD / TRACK
        migrations.AlterField(
            model_name="record",
            name="cover_image",
            field=sorl.thumbnail.fields.ImageField(
                blank=True,
                null=True,
                upload_to=path_by_instance("cover_image"),
                verbose_name="Record image",
            ),
        ),
        migrations.AddField(
            model_name="record",
            name="is_expected",
            field=models.BooleanField(db_index=True, default=False, verbose_name="Предзаказ (ожидается)"),
        ),
        migrations.AddField(
            model_name="record",
            name="release_day",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="День релиза"),
        ),
        migrations.AddField(
            model_name="record",
            name="release_month",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Месяц релиза"),
        ),
        migrations.AlterField(
            model_name="record",
            name="release_year",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Год релиза"),
        ),
        migrations.AlterField(
            model_name="track",
            name="position",
            field=models.CharField(
                blank=True,
                max_length=10,
                verbose_name="Position",
                help_text="Original position from the source (e.g., 'A1', 'B2'); may be empty.",
            ),
        ),
        migrations.AddField(
            model_name="track",
            name="audio_preview",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=path_by_instance("audio_preview"),
                verbose_name="Audio preview (MP3)",
                help_text="Local preview file stored in media (optional).",
            ),
        ),
        migrations.AddField(
            model_name="track",
            name="position_index",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                verbose_name="Order",
                help_text="Sequential order across the release (1..N), independent of sides.",
            ),
        ),
        migrations.AlterField(
            model_name="track",
            name="duration",
            field=models.CharField(
                blank=True,
                null=True,
                max_length=10,
                verbose_name="Duration",
                help_text="Optional; e.g., '05:58'.",
            ),
        ),
        migrations.AlterModelOptions(
            name="track",
            options={
                "verbose_name": "Track",
                "verbose_name_plural": "Tracks",
                "ordering": ("record", "position_index", "position"),
            },
        ),

        # GENRE / STYLE / FORMAT
        migrations.AlterField(
            model_name="genre",
            name="name",
            field=models.CharField(max_length=100, unique=True, verbose_name="Name"),
        ),
        migrations.AlterField(
            model_name="style",
            name="name",
            field=models.CharField(max_length=100, unique=True, verbose_name="Name"),
        ),
        migrations.AlterField(
            model_name="format",
            name="name",
            field=models.CharField(
                choices=[
                    ('Not specified', 'Not specified'),
                    ('7"', '7"'),
                    ('10"', '10"'),
                    ('12"', '12"'),
                    ('EP', 'EP'),
                    ('Single', 'Single'),
                    ('LP', 'LP'),
                    ('2LP', '2LP'),
                    ('3LP', '3LP'),
                    ('4LP', '4LP'),
                    ('Box Set', 'Box Set'),
                    ('Picture Disc', 'Picture Disc'),
                ],
                max_length=100,
                unique=True,
                verbose_name="Name",
            ),
        ),

        # RecordSource
        migrations.CreateModel(
            name="RecordSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(
                    max_length=24,
                    choices=[("redeye", "Redeye"), ("discogs", "Discogs"), ("juno", "Juno")],
                    help_text="Провайдер данных: redeye / discogs / juno и т.д.",
                    verbose_name="Provider",
                )),
                ("role", models.CharField(
                    max_length=24,
                    default="product_page",
                    choices=[("product_page", "Product page"), ("api", "API"), ("listing", "Listing")],
                    help_text="Роль ссылки: product_page / api / listing.",
                    verbose_name="Role",
                )),
                ("url", models.URLField(help_text="Ссылка на внешний источник для этой записи.", verbose_name="Source URL")),
                ("can_fetch_audio", models.BooleanField(default=False, help_text="Можно ли пытаться собирать mp3-превью с этой страницы.", verbose_name="Can fetch audio previews")),
                ("last_audio_scrape_at", models.DateTimeField(blank=True, null=True, help_text="Когда последний раз пытались собрать mp3-ссылки с этой страницы.", verbose_name="Last audio scrape at")),
                ("audio_urls_count", models.PositiveIntegerField(default=0, help_text="Сколько mp3-ссылок нашли в прошлую попытку.", verbose_name="Audio URLs found (last scrape)")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                ("record", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="sources",
                    to="records.record",
                    verbose_name="Record",
                )),
            ],
            options={
                "verbose_name": "Record source",
                "verbose_name_plural": "Record sources",
            },
        ),
        migrations.AddIndex(
            model_name="recordsource",
            index=models.Index(fields=["provider", "role"], name="idx_source_provider_role"),
        ),
        migrations.AddIndex(
            model_name="recordsource",
            index=models.Index(fields=["can_fetch_audio"], name="idx_source_can_fetch_audio"),
        ),
        migrations.AddConstraint(
            model_name="recordsource",
            constraint=models.UniqueConstraint(fields=("record", "provider", "role"), name="uq_recordsource_record_provider_role"),
        ),

        # ДАННЫЕ
        migrations.RunPython(seed_and_normalize_forward, seed_backward),
    ]
