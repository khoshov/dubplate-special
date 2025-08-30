import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")

# Загрузка настроек с префиксом CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Явно регистрируем задачи
app.autodiscover_tasks(packages=["apps.records.services"], related_name="tasks")
