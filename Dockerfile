# Use official Python 3.13 slim image based on Debian Bookworm
FROM python:3.13-slim-bookworm

# Install UV (ultra-fast Python package installer) from Astral.sh
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Ensure Python output is sent straight to terminal without buffering
ENV PYTHONUNBUFFERED=1

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
RUN uv sync --dev

# ======================
# APPLICATION CODE
# ======================
# Copy the rest of the application code
# Note: This is done after dependency installation for better caching
COPY . .

# ======================
# COMPILE TRANSLATIONS
# ======================
# Compile Django translation messages (.po -> .mo)
RUN uv run manage.py compilemessages

# ======================
# RUNTIME CONFIGURATION
# ======================
# Expose the port Django runs on
EXPOSE 8000

# Run Django development server:
# - Binds to all network interfaces (0.0.0.0)
# - Uses port 8000
# Note: For production, use a proper WSGI server like Gunicorn instead
CMD ["uv", "run", "manage.py", "runserver", "0.0.0.0:8000"]
