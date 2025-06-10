#!/bin/sh
set -e

# Apply migrations
uv run manage.py migrate --no-input

# Collect static
uv run manage.py collectstatic --no-input

# Compile Django translation messages (.po -> .mo)
uv run manage.py compilemessages

# Transfer control to docker-compose "command"
exec "$@"
