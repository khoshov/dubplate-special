
[![Ruff](https://github.com/khoshov/dubplate-special/actions/workflows/ruff.yml/badge.svg)](https://github.com/khoshov/dubplate-special/actions/workflows/ruff.yml)

## Структура проекта
- `apps/` — Django‑приложения (`accounts`, `core`, `orders`, `records`).
- `config/` — настройки проекта и ASGI/WSGI.
- `tests/` — тесты (см. `pytest.ini`).
- `media/` — пользовательские файлы (не коммитить).
- `pgdata/` — локальные данные Postgres (не коммитить).
- В корне: `manage.py`, `Dockerfile`, `docker-compose.yml`.

## Безопасность репозитория
- Секреты хранятся только в `.env`/`.env.*`; эти файлы не коммитятся.
- `env.example` используется как шаблон переменных и может храниться в репозитории.
- Локальные данные `media/` и `pgdata/` не должны попадать в коммиты.

## Окружение разработки (основной режим — Docker Compose)
- Основная разработка ведётся локальным редактированием кода с запуском сервисов в контейнерах.
- В dev поднимаются `django`, `postgres`, `redis`, `celery`; изменения исходников подхватываются контейнерами автоматически.

### Docker Compose (рекомендуется)
- Поднять/обновить окружение: `docker compose up -d --build`
- Django-команды: `docker compose exec django uv run manage.py <command>`
- Линт: `docker compose exec django uv run ruff check .`
- Формат: `docker compose exec django uv run ruff format .`
- Тесты: `docker compose exec django uv run pytest`

### Локальные команды (дополнительно)
- Django команды: `uv run manage.py <command>`
- Сервер: `uv run manage.py runserver`
- Тесты: `uv run pytest`
- Линт: `uv run ruff check .`
- Формат: `uv run ruff format .`
- Типы: `uv run mypy .`

## Продакшн (без Docker)
- Продакшн-развёртывание использует systemd-сервисы на сервере.
- Минимальный набор сервисов: Django (ASGI/WSGI), Celery worker, Redis, PostgreSQL.
- Для операционного контроля используются стандартные команды `systemctl` и журналы `journalctl`.

## Команды Redeye (2 режима запуска)
- Для `parse_redeye` и `redeye_mp3_attach` поддерживаются оба режима: локально и в Docker.
- Для каждого режима ниже есть примеры под `bash` и под `PowerShell`.

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
