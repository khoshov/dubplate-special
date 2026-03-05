# Implementation Plan: YouTube Track Audio Enrichment

**Branch**: `[001-youtube-track-audio-enrichment]` | **Date**: 2026-03-06 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `C:\projects\dubplate-special\specs\001-youtube-track-audio-enrichment\spec.md`

## Summary

Добавить асинхронное YouTube-аудио-обогащение треков Discogs-записей через Celery/Redis, не ломая существующий Redeye-поток.  
Ключевой UX: `admin action` сразу подтверждает запуск, а детальный результат публикуется в отдельном job report.  
Ключевая архитектурная точка: новый провайдерный модуль `apps/records/services/audio/providers/youtube_audio_enrichment.py` и единый orchestration-поток из admin actions/record form/update-from-discogs.

## Technical Context

**Language/Version**: Python 3.13, Django 6.x  
**Primary Dependencies**: Django, Celery, Redis, requests, Playwright (existing), `yt-dlp` (planned for YouTube audio retrieval)  
**Storage**: PostgreSQL (records + job reports), media file storage (`Track.audio_preview`), Redis (queue broker/result backend)  
**Testing**: pytest (unit + integration + admin action flows), regression tests for Redeye flows  
**Target Platform**: Development: Docker containers (django + postgres + redis + celery); Production: systemd services (without Docker)  
**Project Type**: Django web application  
**Performance Goals**: enqueue response <= 2s for 100 selected records; for validation runs under baseline network RTT <= 150ms and external source error rate < 5%, 95% jobs for 100 records complete <= 15 min  
**Constraints**: non-destructive data handling; no secret exposure; preserve Redeye behavior; module path fixed to `apps/records/services/audio/providers/youtube_audio_enrichment.py`  
**Scale/Scope**: typical manual batch 1-100 records, up to ~1500 tracks per job, retries up to 3 attempts per track

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] Runtime and execution model is Docker-first for development (Django/PostgreSQL/Redis/Celery in containers where required by scope).
- [x] If scope touches `parse_redeye` or `redeye_mp3_attach`, plan includes bash + PowerShell examples for local and Docker execution. (Not in scope)
- [x] If models change, migration impact is described and includes `uv run manage.py makemigrations` and `uv run manage.py migrate`.
- [x] Plan forbids destructive data actions unless explicit confirmation is part of scope.
- [x] Dependency-change path includes `uv lock` and `uv sync --dev --locked`.
- [x] Code-change path includes `uv run ruff format .` and `uv run ruff check .` (directly or via `docker compose exec ...`).
- [x] Behavior changes include test updates and execution via `uv run pytest` (directly or via `docker compose exec ...`).
- [x] Safety checks include secret hygiene and non-committable local artifacts (`media/`, `pgdata/`).
- [x] Role handoff order is preserved: `code-writer` -> `reviewer` -> `tester` -> `documenter`.

**Gate Status (pre-design)**: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/001-youtube-track-audio-enrichment/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── admin-audio-enrichment-contract.md
│   └── celery-task-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
apps/
└── records/
    ├── admin/
    │   ├── actions.py
    │   ├── mixins.py
    │   └── record_admin.py
    ├── models.py
    ├── services/
    │   ├── record_service.py
    │   └── audio/
    │       ├── audio_service.py
    │       └── providers/
    │           ├── redeye/
    │           └── youtube_audio_enrichment.py   # new
    ├── templates/admin/records/record/submit_line.html
    └── migrations/
config/
├── settings.py
└── celery.py                                   # new
tests/
docker-compose.yml
pyproject.toml
```

**Structure Decision**: Использовать текущую сервисную архитектуру `records.services.audio.providers`, добавить отдельный YouTube-провайдер и асинхронный job orchestration слой через Celery, без изменения назначения существующих Redeye-сервисов.

## Complexity Tracking

No constitution violations requiring justification.

## Phase 0: Research

Артефакт: [research.md](./research.md)

Результат Phase 0:
- Выбран Celery+Redis как единый async path для Discogs YouTube enrichment.
- Утверждён job report как пользовательский итог ручных операций.
- Зафиксированы retry/backoff и concurrency policies.
- Уточнены deployment паттерны для Docker dev и systemd prod.

## Phase 1: Design & Contracts

Артефакты:
- [data-model.md](./data-model.md)
- [quickstart.md](./quickstart.md)
- [contracts/admin-audio-enrichment-contract.md](./contracts/admin-audio-enrichment-contract.md)
- [contracts/celery-task-contract.md](./contracts/celery-task-contract.md)

### Post-Design Constitution Check

- [x] Runtime and execution model remains Docker-first for development.
- [x] Scope still excludes `parse_redeye` and `redeye_mp3_attach`.
- [x] Model-change migration impact documented.
- [x] Dependency-change steps include `uv lock` and `uv sync --dev --locked`.
- [x] Destructive data actions forbidden unless explicitly approved.
- [x] Lint/format/test gates preserved with `uv run` commands (container execution accepted).
- [x] Safety checks cover secrets and non-committable local artifacts.
- [x] Role handoff order preserved.

**Gate Status (post-design)**: PASS.

## Phase 2: Implementation Planning

1. **Infrastructure**
   - Добавить Celery app (`config/celery.py`) и конфигурацию broker/backend в `config/settings.py`.
   - Расширить `docker-compose.yml` сервисами `redis` и `celery` для dev.
   - Обновить зависимости (`pyproject.toml`, lockfile) и зафиксировать `uv lock` + `uv sync --dev --locked`.

2. **Domain & Persistence**
   - Добавить модели job-report (job/job-record/track-result) + миграции.
   - Ввести ограничение активной задачи на запись (конкурентный запуск -> `skipped (already running)`).

3. **Audio Enrichment Module**
   - Реализовать `apps/records/services/audio/providers/youtube_audio_enrichment.py`.
   - Добавить orchestration-методы в `AudioService`/`RecordService`.
   - Подключить строгий matching (track title + >=1 artist), retry<=3 with exponential backoff.

4. **Admin UX Integration**
   - `update_from_discogs` enqueue enrichment.
   - Новый `admin action` "Обновить аудио из YouTube" enqueue with overwrite.
   - Кнопка формы записи enqueue single-record job.
   - Добавить отображение отчёта выполнения.

5. **Verification**
   - `docker compose exec django uv run ruff format .`
   - `docker compose exec django uv run ruff check .`
   - `docker compose exec django uv run pytest`
   - Проверить отсутствие секретов в изменениях и отсутствие коммита `media/`/`pgdata/`.
   - Отдельный regression run по Redeye-сценариям.
