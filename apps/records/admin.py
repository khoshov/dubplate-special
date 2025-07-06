import logging

from django.contrib import admin
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .forms import RecordForm
from .models import Record, Track

logger = logging.getLogger(__name__)


class TrackInline(admin.TabularInline):
    """
    Inline-администратор для отображения треков в интерфейсе записи (Record).
    Настройки:
    - model: Модель Track для отображения
    - extra: Количество дополнительных пустых форм (0 - только существующие треки)
    - readonly_fields: Поля только для чтения
    - can_delete: Запрет удаления треков через админку
    """

    model = Track
    extra = 0
    readonly_fields = ("position", "title", "duration", "url")
    can_delete = False


class RecordAdmin(admin.ModelAdmin):
    """
    Административный интерфейс для модели Record.
    Особенности:
    - Использует кастомную форму RecordForm
    - Показывает треклист только при редактировании
    - При создании записи отображает только поле штрих-кода
    - Обрабатывает дубликаты и перенаправляет на существующие записи
    """

    form = RecordForm
    inlines = [TrackInline]
    add_fields = ("barcode", "catalog_number")

    # Поля для отображения в списке
    list_display = ('title', 'barcode', 'catalog_number', 'discogs_id', 'created')
    list_filter = ('created', 'modified')
    search_fields = ('title', 'barcode', 'catalog_number', 'discogs_id')

    def get_fields(self, request, obj=None):
        """
        Определяет, какие поля показывать в форме.

        Args:
            request: HttpRequest объект
            obj: Экземпляр модели или None для новой записи

        Returns:
            tuple: Поля для отображения:
                - только 'barcode' и 'catalog_number' при создании новой записи
                - все поля при редактировании существующей
        """
        if not obj:
            return self.add_fields
        return super().get_fields(request, obj)

    def get_inline_instances(self, request, obj=None):
        """
        Управляет отображением inline-форм.

        Args:
            request: HttpRequest объект
            obj: Экземпляр модели или None для новой записи

        Returns:
            list: Список inline-форм:
                - пустой список при создании новой записи
                - список с TrackInline при редактировании существующей
        """
        if obj:
            return [inline(self.model, self.admin_site) for inline in self.inlines]
        return []

    def save_model(self, request, obj, form, change):
        """
        Переопределяем сохранение модели для добавления сообщений.

        Args:
            request: HttpRequest объект
            obj: Сохраняемый объект Record
            form: Форма RecordForm
            change: True если редактирование, False если создание
        """
        # Запоминаем, был ли у объекта discogs_id до сохранения
        had_discogs_id = bool(obj.discogs_id) if change else False

        # Сохраняем оригинальный pk для сравнения
        original_pk = obj.pk

        # Вызываем стандартное сохранение через форму
        super().save_model(request, obj, form, change)

        # Проверяем, был ли обнаружен дубликат
        if hasattr(form, 'duplicate_record'):
            duplicate = form.duplicate_record
            # Это дубликат - устанавливаем флаг для перенаправления
            messages.warning(
                request,
                format_html(
                    'Запись с Discogs ID {} уже существует. '
                    'Вы будете перенаправлены на существующую запись.',
                    duplicate.discogs_id
                )
            )
            self._redirect_to_existing = duplicate
            return

        # Стандартная обработка для обычного сохранения
        if not change and obj.discogs_id:
            # Импорт успешен
            messages.success(
                request,
                f'Запись "{obj.title}" успешно импортирована из Discogs '
                f'(ID: {obj.discogs_id})'
            )
            logger.info(f"Successfully imported record {obj.pk} from Discogs")
        elif not change and not obj.discogs_id:
            # Импорт не удался, но запись создана
            messages.warning(
                request,
                'Запись создана, но данные из Discogs не были найдены. '
                'Вы можете заполнить информацию вручную.'
            )
            logger.warning(f"Created record {obj.pk} without Discogs data")
        elif change and not had_discogs_id and obj.discogs_id:
            # Успешный импорт при редактировании
            messages.success(
                request,
                f'Данные из Discogs успешно импортированы '
                f'(ID: {obj.discogs_id})'
            )

    def response_add(self, request, obj, post_url_continue=None):
        """
        Переопределяем редирект после создания записи.

        Обрабатывает случаи:
        1. Перенаправление на существующий дубликат
        2. Перенаправление на редактирование после успешного импорта
        3. Стандартное поведение
        """
        # Проверяем, нужно ли перенаправить на существующую запись
        if hasattr(self, '_redirect_to_existing'):
            existing_obj = self._redirect_to_existing
            delattr(self, '_redirect_to_existing')

            # Перенаправляем на страницу редактирования существующей записи
            return redirect(
                reverse(
                    f'admin:{obj._meta.app_label}_{obj._meta.model_name}_change',
                    args=[existing_obj.pk]
                )
            )

        if obj.discogs_id:
            # Импорт успешен - перенаправляем на редактирование
            messages.info(
                request,
                "Проверьте импортированные данные и при необходимости отредактируйте их."
            )
            return super().response_change(request, obj)

        # Стандартное поведение для неимпортированных записей
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        """
        Переопределяем редирект после изменения записи.

        Обрабатывает случай перенаправления на дубликат при редактировании.
        """
        # Проверяем, нужно ли перенаправить на существующую запись
        if hasattr(self, '_redirect_to_existing'):
            existing_obj = self._redirect_to_existing
            delattr(self, '_redirect_to_existing')

            # Перенаправляем на страницу редактирования существующей записи
            return redirect(
                reverse(
                    f'admin:{obj._meta.app_label}_{obj._meta.model_name}_change',
                    args=[existing_obj.pk]
                )
            )

        return super().response_change(request, obj)


# Регистрация модели с кастомным админом
admin.site.register(Record, RecordAdmin)