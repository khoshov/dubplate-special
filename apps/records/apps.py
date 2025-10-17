from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _bootstrap_vocab(sender, **kwargs):
    """
    После миграций:
    - создаём недостающие базовые значения из TextChoices,
    - нормализуем регистр 'Not specified' (склеиваем case-insensitive дубликаты),
    - удаляем только лишние дубликаты 'Not specified' (остальные значения не трогаем).
    """

    from .models import Genre, Style, GenreChoices, StyleChoices, Record

    CANON = "Not specified"

    def ensure_base(Model, base_names):
        # создать недостающие (сравнение регистронезависимое)
        existing = {n.lower(): n for n in Model.objects.values_list("name", flat=True)}
        for name in base_names:
            if name.lower() not in existing:
                Model.objects.get_or_create(name=name)

    ensure_base(Genre, [c.label for c in GenreChoices])
    ensure_base(Style, [c.label for c in StyleChoices])

    # --- нормализация/склейка только для 'Not specified' ---
    def merge_not_specified(Model, through, fk_name):
        candidates = list(
            Model.objects.filter(name__iexact="not specified").order_by("id")
        )
        if len(candidates) <= 1:
            # если один и он не канонический — переименуем
            if candidates and candidates[0].name != CANON:
                Model.objects.filter(id=candidates[0].id).update(name=CANON)
            return

        # выбираем хранителя: точный CANON если есть, иначе первый
        keeper = next((x for x in candidates if x.name == CANON), candidates[0])
        victims = [x for x in candidates if x.id != keeper.id]

        # удалить дублирующие связи и переназначить остальные на keeper
        keeper_rec_ids = set(
            through.objects.filter(**{fk_name: keeper}).values_list(
                "record_id", flat=True
            )
        )
        for v in victims:
            if keeper_rec_ids:
                through.objects.filter(
                    **{fk_name + "_id": v.id, "record_id__in": keeper_rec_ids}
                ).delete()
            through.objects.filter(**{fk_name + "_id": v.id}).update(
                **{fk_name + "_id": keeper.id}
            )

        # удалить жертвы
        Model.objects.filter(id__in=[v.id for v in victims]).delete()

        # привести имя хранителя к канону
        if keeper.name != CANON:
            Model.objects.filter(id=keeper.id).update(name=CANON)

    merge_not_specified(Genre, Record.genres.through, "genre")
    merge_not_specified(Style, Record.styles.through, "style")


class RecordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "records"

    def ready(self):
        # регистрируем сигналы модели
        from . import signals  # noqa: F401

        # однократная подписка на post_migrate
        post_migrate.connect(
            _bootstrap_vocab,
            dispatch_uid="records_bootstrap_vocab",
        )
