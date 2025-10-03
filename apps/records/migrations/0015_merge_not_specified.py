from django.db import migrations


def merge_cs_insensitive(model_cls, through_cls, fk_name: str):
    """
    Склеивает case-insensitive дубликаты 'not specified' / 'Not specified'
    без нарушения UNIQUE(name).

    model_cls  — модель справочника (Genre/Style)
    through_cls — through-модель M2M (Record.genres.through / Record.styles.through)
    fk_name    — имя FK в through-модели ('genre' или 'style')
    """
    candidates = list(
        model_cls.objects.filter(name__iexact="not specified").order_by("id")
    )
    if len(candidates) <= 1:
        return

    # 1) выбираем хранителя: если есть точный 'Not specified' — берём его
    keeper = next((x for x in candidates if x.name == "Not specified"), candidates[0])
    victims = [x for x in candidates if x.id != keeper.id]

    # 2) переносим связи M2M с жертв на хранителя
    #    сначала убираем возможные дубликаты в through, затем обновляем FK
    keeper_record_ids = set(
        through_cls.objects.filter(**{fk_name: keeper}).values_list("record_id", flat=True)
    )
    for v in victims:
        # удалить дублирующие through-строки (там, где у record уже стоит keeper)
        if keeper_record_ids:
            through_cls.objects.filter(
                **{fk_name + "_id": v.id, "record_id__in": keeper_record_ids}
            ).delete()
        # переназначить остальные связи на keeper
        through_cls.objects.filter(**{fk_name + "_id": v.id}).update(
            **{fk_name + "_id": keeper.id}
        )

    # 3) удаляем жертв
    model_cls.objects.filter(id__in=[v.id for v in victims]).delete()

    # 4) нормализуем имя хранителя ПОСЛЕ удаления жертв (UNIQUE больше не мешает)
    if keeper.name != "Not specified":
        model_cls.objects.filter(id=keeper.id).update(name="Not specified")


def forwards(apps, schema_editor):
    Genre = apps.get_model("records", "Genre")
    Style = apps.get_model("records", "Style")
    Record = apps.get_model("records", "Record")

    GenresThrough = Record.genres.through
    StylesThrough = Record.styles.through

    merge_cs_insensitive(Genre, GenresThrough, "genre")
    merge_cs_insensitive(Style, StylesThrough, "style")


def backwards(apps, schema_editor):
    # откат ничего не делает
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0014_alter_format_name"),  # оставь как у тебя
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]




