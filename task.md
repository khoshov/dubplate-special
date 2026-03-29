# Task Checklist: django-unfold admin migration

## Правило выполнения

- [ ] Выполнять работу блоками, строго по одному блоку за раз.
- [ ] Перед началом блока сверять цель, объем и критерии готовности из `plan.md`.
- [ ] После реализации блока запускать проверки, достаточные для этого блока.
- [ ] После каждого блока обновлять `README.md`, если изменился setup, UI-поведение или порядок проверки.
- [ ] Перед commit показывать пользователю изменения в `README.md` и получать согласование.
- [ ] После commit переходить к следующему блоку только если текущий блок закрыт полностью.

## Блок 1. Подготовка инфраструктуры unfold

- [ ] Добавить `django-unfold` в зависимости проекта.
- [ ] Обновить `uv.lock`.
- [ ] Подключить `unfold` в `INSTALLED_APPS` в правильном порядке.
- [ ] Добавить базовую секцию `UNFOLD` в `config/settings.py`.
- [ ] Проверить, требуется ли адаптация `config/urls.py`.
- [ ] Убедиться, что `admin:index` рендерится без ошибок.
- [ ] Обновить `README.md`, если изменились setup/зависимости.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] Подготовить commit для блока 1.

## Блок 2. Перевод простых admin-модулей

- [ ] Адаптировать `apps/accounts/admin.py` под `unfold`.
- [ ] Адаптировать `apps/core/admin.py` под `unfold`.
- [ ] Адаптировать `apps/orders/admin.py` под `unfold`.
- [ ] Проверить списки, фильтры, поиск и формы редактирования.
- [ ] Проверить совместимость `BaseUserAdmin` и `SingletonModelAdmin`.
- [ ] Обновить/добавить тесты при необходимости.
- [ ] Обновить `README.md`, если это влияет на описание админки.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] Подготовить commit для блока 2.

## Блок 3. Базовая интеграция records admin с unfold

- [ ] Адаптировать admin-классы в `apps/records/admin/*` под `unfold`.
- [ ] Сохранить кастомные actions, `get_urls`, redirect и сообщения.
- [ ] Проверить changelist/changeform/delete для `RecordAdmin`.
- [ ] Проверить связанные admin-классы служебных моделей.
- [ ] Проверить совместимость ручной группировки через `admin.site.get_app_list`.
- [ ] Обновить/добавить тесты на admin-поведение.
- [ ] Обновить `README.md`, если нужно описать новые особенности.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] `uv run pytest tests/test_vk_schedule.py -q`
- [ ] Подготовить commit для блока 3.

## Блок 4. Адаптация кастомных base templates

- [ ] Адаптировать `templates/admin/base_site.html`.
- [ ] Проверить необходимость `apps/records/templates/admin/base_site.html`.
- [ ] Сохранить `youtube_session_banner`.
- [ ] Проверить branding, messages и базовую навигацию.
- [ ] Убрать дублирование шаблонов, если это безопасно.
- [ ] Обновить тесты, если меняется шаблонное поведение.
- [ ] Обновить `README.md`, если меняется описание админки.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] Подготовить commit для блока 4.

## Блок 5. Адаптация inline-интерфейсов records

- [ ] Адаптировать `TrackInline` под `unfold`.
- [ ] Адаптировать `StructuredFormatInline` под `unfold`.
- [ ] Обновить inline templates для треков и structured formats.
- [ ] Доработать связанный CSS/JS при необходимости.
- [ ] Проверить кнопки загрузки/удаления mp3.
- [ ] Проверить variant selector и readonly-поля.
- [ ] Обновить `tests/admin/test_record_admin_track_inline.py`.
- [ ] Обновить `tests/admin/test_record_admin_structured_formats.py`.
- [ ] Обновить `README.md`, если появились особенности UI.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin/test_record_admin_track_inline.py -q`
- [ ] `uv run pytest tests/admin/test_record_admin_structured_formats.py -q`
- [ ] `uv run pytest tests/admin -q`
- [ ] Подготовить commit для блока 5.

## Блок 6. Адаптация кастомных workflow-страниц

- [ ] Адаптировать `apps/records/templates/admin/records/record/vk_schedule.html`.
- [ ] Адаптировать `apps/records/templates/admin/records/youtube_session_recover.html`.
- [ ] Проверить все страницы, наследующие `admin/base_site.html`.
- [ ] Привести кнопки, формы и help-блоки к стилю `unfold`.
- [ ] Обновить тесты, если меняется шаблонное поведение.
- [ ] Обновить `README.md`, если нужно задокументировать сценарии проверки.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/test_vk_schedule.py -q`
- [ ] `uv run pytest tests/admin/test_youtube_audio_admin.py -q`
- [ ] Подготовить commit для блока 6.

## Блок 7. Навигация, группировка и polish

- [ ] Решить, остается ли ручная группировка `get_app_list` или заменяется конфигом `UNFOLD`.
- [ ] Настроить sidebar/navigation.
- [ ] Проверить названия разделов и визуальную иерархию.
- [ ] Убрать остаточные визуальные дефекты.
- [ ] Проверить отображение служебных моделей.
- [ ] Обновить тесты, если меняется навигационная логика.
- [ ] Обновить `README.md`, если изменилась структура админки.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] Подготовить commit для блока 7.

## Блок 8. Тесты и фиксация покрытия

- [ ] Пересмотреть все admin-тесты на предмет устаревших ожиданий.
- [ ] Обновить assertions, завязанные на шаблоны или HTML, только там, где это оправдано.
- [ ] Добавить тесты на новый адаптационный код, если он появился.
- [ ] Прогнать расширенный набор тестов по админке и смежным сценариям.
- [ ] Зафиксировать финальный набор проверок для фичи.
- [ ] Обновить `README.md`, если нужно описать финальный сценарий верификации.
- [ ] Показать пользователю diff `README.md` перед commit.
- [ ] Прогнать:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] `uv run pytest tests/test_vk_schedule.py -q`
- [ ] Подготовить commit для блока 8.

## Блок 9. Документация и финализация

- [ ] Обновить `README.md` по итогам всей миграции.
- [ ] Описать новую зависимость и конфигурацию админки.
- [ ] Описать команды проверки после миграции.
- [ ] Проверить, что документация соответствует фактическому состоянию кода.
- [ ] Показать пользователю финальный diff `README.md` перед commit.
- [ ] Прогнать финальные проверки:
- [ ] `uv run ruff format .`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest tests/admin -q`
- [ ] Подготовить commit для блока 9.

## Финальный контроль

- [ ] Все блоки закрыты.
- [ ] Все обещанные проверки выполнены.
- [ ] `README.md` актуален.
- [ ] История работы разбита на небольшие понятные commits.
- [ ] Фича готова к дальнейшему review.
