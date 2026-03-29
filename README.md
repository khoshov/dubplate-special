
[![Ruff](https://github.com/khoshov/dubplate-special/actions/workflows/ruff.yml/badge.svg)](https://github.com/khoshov/dubplate-special/actions/workflows/ruff.yml)

## Структура проекта
- `apps/` — Django‑приложения (`accounts`, `core`, `orders`, `records`).
- `config/` — настройки проекта и ASGI/WSGI.
- `tests/` — тесты (см. `pytest.ini`).
- `media/` — пользовательские файлы (не коммитить).
- `pgdata/` — локальные данные Postgres (не коммитить).
- В корне: `manage.py`, `Dockerfile`, `docker-compose.yml`.

## Окружение разработки (основной режим — локально через uv)
- Основные команды (миграции/линт/тесты) выполняются локально через `uv run`.

### Локальные команды
- Django команды: `uv run manage.py <command>`
- Сервер: `uv run manage.py runserver`
- Тесты: `uv run pytest`
- Линт: `uv run ruff check .`
- Формат: `uv run ruff format .`
- Типы: `uv run mypy .`

## Админка
- Админка проекта использует UI-слой `django-unfold`, основной URL остается прежним: `/admin/`.
- В sidebar вынесены быстрые переходы к ключевым разделам: релизы, заказы, пользователи, курс валют, VK-публикации и audio jobs.
- Раздел `records` сохраняет кастомное поведение поверх новой темы: actions, inline-редактирование треков и structured formats, восстановление YouTube-сессии и планирование публикаций в VK.
- Служебные модели `records` продолжают группироваться отдельно через существующую серверную логику админки.

## Проверка миграции админки
- Форматирование: `uv run ruff format . --exclude .pytest-tmp`
- Линт: `uv run ruff check . --exclude .pytest-tmp`
- Админка: `uv run pytest tests/admin -q --reuse-db`
- VK workflow: `uv run pytest tests/test_vk_schedule.py -q --reuse-db`
### Docker Compose (опционально)
- Поднять окружение: `docker compose up -d --build`
- При необходимости команды можно выполнять в контейнере `django` через `docker compose exec`.
- Фоновая обработка выполняется обычным контейнером `celery`.
- Контейнер `youtube_session_login` используется только для ручной интерактивной YouTube-авторизации через noVNC и поднимает внутри себя отдельный worker только для очереди `youtube_session_login`.

## Команды Redeye (2 режима запуска)
- Для `parse_redeye` и `redeye_mp3_attach` поддерживаются оба режима: локально и в Docker.
- Для каждого режима ниже есть примеры под `bash` и под `PowerShell`.

## Кнопки и action-команды в админке

### YouTube
- Кнопка `Обновить аудио треков из YouTube` на странице записи ставит одну фоновую Celery-задачу только для этой записи.
- Mass action `Обновить аудио треков из YouTube` ставит одну фоновую задачу сразу для выбранных записей.
- Оба варианта работают с `overwrite=true`:
  - если у трека уже есть `audio_preview`, он будет заменён новым mp3 из YouTube;
  - если при замене путь к файлу изменился, старый физический mp3 удаляется из storage.
- После обычного `import_from_discogs` YouTube-задача ставится автоматически, но с `overwrite=false`:
  - уже существующие mp3 не трогаются;
  - загружаются только отсутствующие.
- `update_from_discogs` YouTube-задачу автоматически не запускает.
- Кнопка `Найти аудио на YouTube` на странице записи ставит задачу поиска ссылок только для этого релиза.
- Mass action `Найти аудио на YouTube` запускает поиск ссылок сразу для выбранных записей.
- Поиск заполняет только пустые поля `youtube_url` у треков и выполняется в фоне через Celery.
- Поле `youtube_url` поддерживает ссылки на YouTube и Bandcamp (Bandcamp используется для загрузки mp3 через `yt-dlp`).

### Redeye
- Кнопка `Закачать mp3 с Redeye` на странице записи работает только для одной записи и вызывает загрузку с `force=false`.
- Mass action `Обновить из Redeye` работает для выбранных записей и тоже использует `force=false`.
- В обоих режимах Redeye:
  - уже существующие mp3 не перезаписываются;
  - дозагружаются только отсутствующие аудио-файлы;
  - если у записи нет валидного Redeye source или точного `catalog_number`, запись будет пропущена или завершится ошибкой в отчёте админки.
- Management command `redeye_mp3_attach --force` работает в overwrite-режиме:
  - существующие mp3 могут быть заменены;
  - при смене пути старый физический mp3 удаляется из storage.

### `parse_redeye`
Локально (bash):
```bash
uv run manage.py parse_redeye \
  --category all \
  --limit 2 \
  --save
```

Локально (PowerShell):
```powershell
uv run manage.py parse_redeye `
  --category all `
  --limit 2 `
  --save
```

Docker (bash):
```bash
docker compose exec django uv run manage.py parse_redeye \
  --category all \
  --limit 2 \
  --save
```

Docker (PowerShell):
```powershell
docker compose exec django uv run manage.py parse_redeye `
  --category all `
  --limit 2 `
  --save
```

### `redeye_mp3_attach`
Локально (bash):
```bash
uv run manage.py redeye_mp3_attach \
  --limit 20 \
  --force
```

Локально (PowerShell):
```powershell
uv run manage.py redeye_mp3_attach `
  --limit 20 `
  --force
```

Docker (bash):
```bash
docker compose exec django uv run manage.py redeye_mp3_attach \
  --limit 20 \
  --force
```

Docker (PowerShell):
```powershell
docker compose exec django uv run manage.py redeye_mp3_attach `
  --limit 20 `
  --force
```

## Зависимости (uv)
- Dev‑инструменты закреплены в `[dependency-groups].dev` и запускаются через `uv run ...`.
- `uvx` использовать только для разовых утилит, не являющихся зависимостями проекта.
- При изменении зависимостей: `uv lock`, затем `uv sync --dev --locked` (локально).

## Миграции
- После изменения моделей: `uv run manage.py makemigrations`, затем `uv run manage.py migrate`.
- Разрушительные операции с БД/данными выполнять только после явного подтверждения.

## Стиль и правила кода
- Следуем PEP 8/PEP 484, имена переменных и параметров — на английском.
- Русский допустим в докстрингах/логах, публичные идентификаторы — на английском.
- Импорты предпочтительно абсолютные.
- Типизация обязательна для `config.*`, `apps.accounts.*`, `apps.core.*`, `apps.orders.*`, `apps.records.*`.

## Установка и использование UV

<details>
<summary>📦 Способы установки UV</summary>

### 1. Установка через автономные установщики (рекомендуется)

**Для macOS и Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Для Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Установка через PyPI (альтернативный способ)
```bash
pip install uv
```

### Обновление UV
После установки вы можете обновить UV до последней версии:
```bash
uv self update
```

🔗 Подробнее об установке: [Официальная документация](https://docs.astral.sh/uv/getting-started/installation/)
</details>

---

<details>
<summary>🚀 Основные команды UV</summary>

### Управление Python-окружением

**Установка конкретной версии Python:**
```bash
uv python install 3.13  # Установит Python 3.13
```

### Управление зависимостями

**Синхронизация зависимостей проекта без dev группы:**
```bash
uv sync --no-dev
```

**Синхронизация всех зависимостей проекта:**
```bash
uv sync  # Аналог pip install + pip-compile
```

**Запуск команд в окружении проекта:**
```bash
uv run <COMMAND>  # Например: uv run pytest
```

**Запуск Django-сервера:**
```bash
uv run manage.py runserver  # Альтернатива python manage.py runserver
```
</details>

---

<details>
<summary>🔍 Интеграция с Ruff</summary>

[Ruff](https://github.com/astral-sh/ruff) - это молниеносный линтер для Python, также разработанный Astral.

**Запуск Ruff через UV (из зависимостей проекта):**
```bash
uv run ruff --version
```

**Проверка кода с помощью Ruff:**
```bash
uv run ruff check .  # Проверит все файлы в текущей директории
```
</details>

---

## Запуск проекта в Docker

**Сборка и запуск контейнеров:**
```bash
docker compose up --build  # Соберет и запустит сервисы
```

