FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1

# 1. Установка системных зависимостей + uv

RUN apt-get update && apt-get install -y curl gettext

WORKDIR /app

# 2. Копируем только файлы зависимостей
COPY pyproject.toml uv.lock ./

## 3. Установка зависимостей через uv (прямо из pyproject.toml)
RUN uv sync --locked


# 4. Копируем весь код
COPY . .

# 5. compilemessages
RUN uv run manage.py compilemessages

# 6. Запускаем сервер django
CMD ["uv","run", "manage.py", "runserver", "0.0.0.0:8000"]



