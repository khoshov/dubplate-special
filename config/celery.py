import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")

# Загрузка настроек с префиксом CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Явно регистрируем задачи
app.autodiscover_tasks(packages=["apps.records.services"], related_name="tasks")

app.conf.beat_schedule = {
    "daily-discogs-sync": {
        "task": "sync_discogs_collection",  # Имя из декоратора @shared_task
        "schedule": crontab(hour=10, minute=37),
        "options": {
            "expires": 3600,
        },
    },
    "check-sync-status": {
        "task": "verify_sync_status",  # Имя из декоратора @shared_task
        "schedule": crontab(minute=0, hour="*/6"),
    },
}
