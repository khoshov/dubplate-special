# Quickstart: YouTube Track Audio Enrichment

## 1. Start development environment (Docker-first)

```powershell
docker compose up -d --build
```

Expected runtime services for this feature:
- django
- postgres
- redis
- celery worker

## 2. Apply migrations

```powershell
docker compose exec django uv run manage.py makemigrations
docker compose exec django uv run manage.py migrate
```

## 3. Run quality gates

```powershell
docker compose exec django uv run ruff format .
docker compose exec django uv run ruff check .
docker compose exec django uv run pytest
```

## 4. Manual verification flow

1. Open Django admin.
2. Trigger `admin action` "Обновить из Discogs" for records with Discogs IDs.
3. Confirm immediate enqueue feedback and capture `job_id`.
4. Trigger `admin action` "Обновить аудио из YouTube" for selected records.
5. Trigger record-form button for single-record run.
6. Open job report and verify:
   - `updated/skipped/failed` counters,
   - `mismatch` reasoning,
   - retry count <= 3 for failed tracks,
   - `skipped (already_running)` for concurrent launch conflict.

## 5. Redeye regression checks

Validate unchanged behavior:
- existing `admin action` "Обновить из Redeye"
- existing record-form button "Закачать mp3 с Redeye"
- existing CLI `redeye_mp3_attach`

## 6. Production note

Production target is non-Docker:
- Django service via systemd
- Celery worker via systemd
- Redis service via systemd
- PostgreSQL service via systemd
