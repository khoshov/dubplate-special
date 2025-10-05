# Гарантируем правильные пути и раннюю инициализацию Django в тестах.
import os
import sys
import django
from django.conf import settings

# Корень проекта в контейнере — /app; нам нужны /app и /app/apps
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
APPS_DIR = os.path.join(PROJECT_ROOT, "apps")

for p in (PROJECT_ROOT, APPS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# Ранняя инициализация Django-настроек, чтобы импорты моделей/виджетов не падали


if not settings.configured:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
