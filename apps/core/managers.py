from typing import Optional

from django.db import models


class BaseQuerySet(models.QuerySet):
    """Базовый QuerySet с общими методами.

    Предоставляет переиспользуемые методы для всех QuerySet в проекте.
    """

    def find_by_id(self, id: int) -> Optional[models.Model]:
        """Безопасный поиск по ID.

        Args:
            id: Идентификатор объекта.

        Returns:
            Найденный объект или None, если не найден.
        """
        try:
            return self.get(pk=id)
        except self.model.DoesNotExist:
            return None


class BaseManager(models.Manager):
    """Базовый менеджер с поддержкой кастомного QuerySet.

    Предоставляет общую функциональность для всех менеджеров в проекте.
    """

    def get_queryset(self) -> BaseQuerySet:
        """Возвращает кастомный QuerySet.

        Returns:
            BaseQuerySet для текущей модели.
        """
        return BaseQuerySet(self.model, using=self._db)

    def find_by_id(self, id: int) -> Optional[models.Model]:
        """Поиск объекта по ID.

        Args:
            id: Идентификатор объекта.

        Returns:
            Найденный объект или None, если не найден.
        """
        return self.get_queryset().find_by_id(id)
