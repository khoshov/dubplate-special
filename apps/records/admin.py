from django.contrib import admin
from solo.admin import SingletonModelAdmin

from .forms import RecordForm
from .models import Record, Track, DollarRate


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
    readonly_fields = ("position", "title", "duration")
    can_delete = False


class RecordAdmin(admin.ModelAdmin):
    """
    Административный интерфейс для модели Record.
    Особенности:
    - Использует кастомную форму RecordForm
    - Показывает треклист только при редактировании
    - При создании записи отображает только поле штрих-кода
    """

    form = RecordForm
    inlines = [TrackInline]
    add_fields = ("barcode",)

    def get_fields(self, request, obj=None):
        """
        Определяет, какие поля показывать в форме.

        Args:
            request: HttpRequest объект
            obj: Экземпляр модели или None для новой записи

        Returns:
            tuple: Поля для отображения:
                - только 'barcode' при создании новой записи
                - все поля при редактировании существующей
        """
        if not obj:
            return self.add_fields
        return super().get_fields(request, obj)

    # # Показываем треклист только при редактировании существующей записи
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


@admin.register(DollarRate)
class DollarRateAdmin(SingletonModelAdmin):
    pass


admin.site.register(Record, RecordAdmin)
