from django.apps import AppConfig


class RecordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "records"
    verbose_name = "Релизы"

    def ready(self) -> None:
        # регистрируем сигналы моделей
        from . import signals  # noqa: F401
