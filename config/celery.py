"""Celery application bootstrap for Django project."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.imports = ("records.services.tasks",)
app.autodiscover_tasks()
