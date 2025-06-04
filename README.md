
[![Ruff](https://github.com/khoshov/dubplate-special/actions/workflows/ruff.yml/badge.svg)](https://github.com/khoshov/dubplate-special/actions/workflows/ruff.yml)


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

**Синхронизация зависимостей проекта:**
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

**Установка Ruff через UV:**
```bash
uvx ruff  # Установит последнюю версию Ruff
```

**Проверка кода с помощью Ruff:**
```bash
uvx ruff check .  # Проверит все файлы в текущей директории
```
</details>

---

## Запуск проекта в Docker

**Сборка и запуск контейнеров:**
```bash
docker compose up --build  # Соберет и запустит сервисы
```
