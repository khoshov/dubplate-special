# Use official Python 3.13 slim image based on Debian Bookworm
FROM python:3.13-slim-bookworm

# Install UV (ultra-fast Python package installer) from Astral.sh
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Ensure Python output is sent straight to terminal without buffering
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/apps


# ======================
# SYSTEM DEPENDENCIES
# ======================
# Install required system packages:
# - curl: for downloading files
# - gettext: for Django translation utilities
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    gettext && \
    rm -rf /var/lib/apt/lists/*

# Set working directory inside container
WORKDIR /app

# ======================
# DEPENDENCY INSTALLATION
# ======================
# Copy dependency specification files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies using UV:
# --locked: ensures exact versions from lockfile are used
RUN uv sync --dev --locked

# ВАЖНО: добавляем виртуальное окружение uv в PATH
ENV PATH="/app/.venv/bin:$PATH"

# установить headless Chromium после установки пакета playwright ---
RUN python -m playwright install chromium --with-deps

# ======================
# APPLICATION CODE
# ======================
# Copy the rest of the application code
# Note: This is done after dependency installation for better caching
COPY . .

# Making the file executable
RUN chmod +x entrypoint.sh

# ======================
# RUNTIME CONFIGURATION
# ======================
# Expose the port Django runs on
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
