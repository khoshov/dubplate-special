пере# Tasks: YouTube Track Audio Enrichment

**Input**: Design documents from `C:\projects\dubplate-special\specs\001-youtube-track-audio-enrichment\`  
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Include test tasks because feature changes behavior (admin flows, async processing, retry/concurrency rules, Redeye regression safety).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Every task includes an exact file path

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Initialize queue/runtime dependencies and baseline project wiring for this feature.

- [X] T001 Add `celery`, `redis`, and `yt-dlp` dependencies to `C:\projects\dubplate-special\pyproject.toml`
- [X] T002 Update dependency lock to include new packages in `C:\projects\dubplate-special\uv.lock` via `uv lock`
- [X] T003 Sync locked dev environment from `C:\projects\dubplate-special\pyproject.toml` / `C:\projects\dubplate-special\uv.lock` via `uv sync --dev --locked`
- [X] T004 [P] Add Docker dev services for `redis` and `celery` in `C:\projects\dubplate-special\docker-compose.yml`
- [X] T005 [P] Create Celery app bootstrap in `C:\projects\dubplate-special\config\celery.py`
- [X] T006 Wire Celery/Redis settings and imports in `C:\projects\dubplate-special\config\settings.py` and `C:\projects\dubplate-special\config\__init__.py`
- [X] T007 Document expected environment variables for Celery/Redis in `C:\projects\dubplate-special\env.example`
- [X] T008 Add setup-level safety guard for non-committable artifacts and secrets in `C:\projects\dubplate-special\.gitignore` and `C:\projects\dubplate-special\README.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared queue/report/enrichment foundation before any user story implementation.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T009 Validate FR-010 precondition by confirming accepted comparative analysis artifacts in `C:\projects\dubplate-special\specs\001-youtube-track-audio-enrichment\spec.md` and `C:\projects\dubplate-special\specs\001-youtube-track-audio-enrichment\research.md`
- [X] T010 Create job-report entities (`AudioEnrichmentJob`, `AudioEnrichmentJobRecord`, `AudioEnrichmentTrackResult`) in `C:\projects\dubplate-special\apps\records\models.py`
- [X] T011 Register job-report entities in admin in `C:\projects\dubplate-special\apps\records\admin\record_admin.py`
- [X] T012 Create DB migration for job-report models in `C:\projects\dubplate-special\apps\records\migrations\`
- [X] T013 Implement core provider module in `C:\projects\dubplate-special\apps\records\services\audio\providers\youtube_audio_enrichment.py`
- [X] T014 [P] Add YouTube enrichment orchestration methods in `C:\projects\dubplate-special\apps\records\services\audio\audio_service.py`
- [X] T015 [P] Implement Celery task handlers and payload contracts in `C:\projects\dubplate-special\apps\records\services\tasks.py`
- [X] T016 Wire task discovery/startup for records tasks in `C:\projects\dubplate-special\config\celery.py`
- [X] T017 Implement per-record active-job lock (`already_running`) in `C:\projects\dubplate-special\apps\records\services\audio\providers\youtube_audio_enrichment.py`
- [X] T018 [P] Implement status/reason/attempt_count serialization helpers in `C:\projects\dubplate-special\apps\records\services\audio\providers\youtube_audio_enrichment.py`
- [X] T019 [P] Add foundational model transition tests in `C:\projects\dubplate-special\tests\records\test_audio_enrichment_job_models.py`
- [X] T020 [P] Add foundational task contract tests in `C:\projects\dubplate-special\tests\records\audio\test_youtube_enrichment_task_contract.py`

**Checkpoint**: Queue/report/provider foundation ready; user stories can be implemented independently.

---

## Phase 3: User Story 1 - Автообогащение Discogs треков (Priority: P1) 🎯 MVP

**Goal**: Автоматически запускать YouTube-аудио-обогащение в Discogs-потоке (добавление и update_from_discogs) без регрессии Redeye.

**Independent Test**: Discogs add/update ставит задачу в очередь; после завершения `audio_preview` заполнен только у валидно совпавших треков; Redeye-поток без изменений.

### Tests for User Story 1

- [X] T021 [P] [US1] Add integration test for Discogs add flow enqueue in `C:\projects\dubplate-special\tests\records\test_discogs_add_enrichment_enqueue.py`
- [X] T022 [P] [US1] Add integration test for `update_from_discogs` enqueue in `C:\projects\dubplate-special\tests\records\test_discogs_update_enrichment_enqueue.py`
- [X] T023 [P] [US1] Add strict matching behavior test in `C:\projects\dubplate-special\tests\records\audio\test_youtube_enrichment_matching.py`
- [X] T024 [P] [US1] Add retry<=3 and terminal-failed behavior test in `C:\projects\dubplate-special\tests\records\audio\test_youtube_enrichment_retries.py`
- [X] T025 [P] [US1] Add job report attempts persistence/visibility test in `C:\projects\dubplate-special\tests\records\audio\test_youtube_enrichment_job_report_attempts.py`
- [X] T026 [P] [US1] Add Redeye regression test in `C:\projects\dubplate-special\tests\records\test_redeye_audio_regression.py`

### Implementation for User Story 1

- [X] T027 [US1] Integrate Discogs add/update with enrichment enqueue in `C:\projects\dubplate-special\apps\records\services\record_service.py`
- [X] T028 [US1] Integrate Discogs-triggered enrichment messaging in `C:\projects\dubplate-special\apps\records\admin\actions.py`
- [X] T029 [US1] Add Discogs-flow job/result aggregation updates in `C:\projects\dubplate-special\apps\records\services\tasks.py`
- [X] T030 [US1] Add structured outcome logging for Discogs-flow enrichment in `C:\projects\dubplate-special\apps\records\services\audio\providers\youtube_audio_enrichment.py`

**Checkpoint**: US1 fully functional and independently testable.

---

## Phase 4: User Story 2 - Массовое ручное обновление YouTube-аудио (Priority: P2)

**Goal**: Добавить массовый `admin action` "Обновить аудио из YouTube" с мгновенным enqueue и детальным job report.

**Independent Test**: Выборка записей в list view создаёт job report, сразу показывает enqueue, после выполнения содержит `updated/skipped/failed` и причины.

### Tests for User Story 2

- [X] T031 [P] [US2] Add admin action enqueue/job creation test in `C:\projects\dubplate-special\tests\records\admin\test_update_audio_from_youtube_action.py`
- [X] T032 [P] [US2] Add overwrite behavior test in `C:\projects\dubplate-special\tests\records\admin\test_youtube_action_overwrite.py`
- [X] T033 [P] [US2] Add `skipped (already_running)` conflict test in `C:\projects\dubplate-special\tests\records\admin\test_youtube_action_concurrency.py`

### Implementation for User Story 2

- [X] T034 [US2] Implement new `admin action` "Обновить аудио из YouTube" in `C:\projects\dubplate-special\apps\records\admin\actions.py`
- [X] T035 [US2] Register new list action in `C:\projects\dubplate-special\apps\records\admin\record_admin.py`
- [X] T036 [US2] Implement job report link/message rendering for list action in `C:\projects\dubplate-special\apps\records\admin\actions.py`
- [X] T037 [US2] Wire overwrite=true behavior from list action to orchestration in `C:\projects\dubplate-special\apps\records\services\record_service.py`

**Checkpoint**: US2 fully functional and independently testable.

---

## Phase 5: User Story 3 - Точечное обновление с формы записи (Priority: P3)

**Goal**: Добавить кнопку формы записи для одиночного enqueue YouTube-обогащения с асинхронным отчётом результата.

**Independent Test**: Кнопка формы запускает single-record job, не блокирует UI, итог доступен через report.

### Tests for User Story 3

- [X] T038 [P] [US3] Add single-record enqueue POST flow test in `C:\projects\dubplate-special\tests\records\admin\test_record_form_youtube_refresh.py`
- [X] T039 [P] [US3] Add permission test for single-record trigger in `C:\projects\dubplate-special\tests\records\admin\test_record_form_youtube_permissions.py`
- [X] T040 [P] [US3] Add running-conflict handling test in `C:\projects\dubplate-special\tests\records\admin\test_record_form_youtube_conflict.py`

### Implementation for User Story 3

- [X] T041 [US3] Add admin mixin route/handler for single-record YouTube refresh in `C:\projects\dubplate-special\apps\records\admin\mixins.py`
- [X] T042 [US3] Integrate mixin in `C:\projects\dubplate-special\apps\records\admin\record_admin.py`
- [X] T043 [US3] Add form button for YouTube refresh in `C:\projects\dubplate-special\apps\records\templates\admin\records\record\submit_line.html`
- [X] T044 [US3] Add single-record enqueue result messaging and redirect in `C:\projects\dubplate-special\apps\records\admin\mixins.py`

**Checkpoint**: US3 fully functional and independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Hardening, measurable validation, documentation updates, and final gates.

- [X] T045 [P] Add cross-story end-to-end job lifecycle test in `C:\projects\dubplate-special\tests\records\integration\test_audio_enrichment_job_lifecycle.py`
- [X] T046 [P] Add 100-record enqueue performance smoke test in `C:\projects\dubplate-special\tests\records\integration\test_audio_enrichment_enqueue_perf_smoke.py`
- [X] T047 Add SC-001 metric validation test scenario (30 Discogs records, >=95% success) in `C:\projects\dubplate-special\tests\records\integration\test_audio_enrichment_success_criteria.py`
- [X] T048 Update operations documentation for Docker dev + systemd prod services in `C:\projects\dubplate-special\README.md`
- [X] T049 Run migration commands from `C:\projects\dubplate-special\manage.py` via Docker (`docker compose exec django uv run manage.py makemigrations` and `docker compose exec django uv run manage.py migrate`)
- [X] T050 Run formatting/lint gates for `C:\projects\dubplate-special\` via Docker (`docker compose exec django uv run ruff format .` and `docker compose exec django uv run ruff check .`)
- [X] T051 Run full behavior test suite from `C:\projects\dubplate-special\tests\` via Docker (`docker compose exec django uv run pytest`)
- [X] T052 Run final safety pre-merge verification against `C:\projects\dubplate-special\.gitignore` and workspace diff (no secrets, no `media/`, no `pgdata/` committed)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundational)**: depends on Phase 1; blocks all user stories.
- **Phase 3 (US1)**: depends on Phase 2.
- **Phase 4 (US2)**: depends on Phase 2.
- **Phase 5 (US3)**: depends on Phase 2.
- **Phase 6 (Polish)**: depends on completed story phases.

### User Story Dependencies

- **US1 (P1)**: starts after Foundational.
- **US2 (P2)**: starts after Foundational; no dependency on US1 implementation.
- **US3 (P3)**: starts after Foundational; no dependency on US1/US2 implementation.

### Task Dependency Graph (High Level)

- T001 -> T002 -> T003
- T004/T005/T006/T007/T008 -> Foundation readiness
- T009 -> T010/T011/T012
- T010/T013/T014/T015/T016/T017/T018 -> T019/T020
- Phase 2 checkpoint -> (T021-T030), (T031-T037), (T038-T044)
- Story checkpoints -> T045/T046/T047/T048/T049/T050/T051/T052

### Parallel Opportunities

- Setup parallel: T004, T005, T007.
- Foundation parallel: T014, T015, T018, T019, T020.
- US1 parallel tests: T021-T026.
- US2 parallel tests: T031-T033.
- US3 parallel tests: T038-T040.
- Polish parallel: T045, T046, T048.

---

## Parallel Example: User Story 1

```bash
# Tests in parallel
Task: "T021 [US1] Discogs add enqueue test in tests/records/test_discogs_add_enrichment_enqueue.py"
Task: "T022 [US1] Discogs update enqueue test in tests/records/test_discogs_update_enrichment_enqueue.py"
Task: "T023 [US1] Strict matching test in tests/records/audio/test_youtube_enrichment_matching.py"

# Implementation split in parallel after foundational completion
Task: "T027 [US1] RecordService integration in apps/records/services/record_service.py"
Task: "T028 [US1] Admin messaging integration in apps/records/admin/actions.py"
```

## Parallel Example: User Story 2

```bash
# Tests in parallel
Task: "T031 [US2] Admin list enqueue test in tests/records/admin/test_update_audio_from_youtube_action.py"
Task: "T032 [US2] Overwrite behavior test in tests/records/admin/test_youtube_action_overwrite.py"
Task: "T033 [US2] Concurrency conflict test in tests/records/admin/test_youtube_action_concurrency.py"

# Implementation split
Task: "T034 [US2] New admin action in apps/records/admin/actions.py"
Task: "T035 [US2] Action registration in apps/records/admin/record_admin.py"
```

## Parallel Example: User Story 3

```bash
# Tests in parallel
Task: "T038 [US3] Form enqueue test in tests/records/admin/test_record_form_youtube_refresh.py"
Task: "T039 [US3] Permission test in tests/records/admin/test_record_form_youtube_permissions.py"
Task: "T040 [US3] Conflict handling test in tests/records/admin/test_record_form_youtube_conflict.py"

# Implementation split
Task: "T041 [US3] Mixin handler in apps/records/admin/mixins.py"
Task: "T043 [US3] Form button in apps/records/templates/admin/records/record/submit_line.html"
```

---

## Implementation Strategy

### MVP First (US1 only)

1. Complete Phase 1 (Setup).
2. Complete Phase 2 (Foundational).
3. Complete Phase 3 (US1).
4. Validate US1 independently via T021-T026.

### Incremental Delivery

1. Deliver US1 (Discogs auto enrichment on shared foundation).
2. Deliver US2 (mass list `admin action`).
3. Deliver US3 (single-record form action).
4. Finalize with Phase 6 cross-cutting validation and quality gates.

### Independent Test Criteria by Story

- **US1**: Discogs add/update enqueue and async completion updates `audio_preview` under strict matching/retry rules; Redeye remains unchanged.
- **US2**: List action enqueues immediately and produces deterministic final report with overwrite and conflict-skip behavior.
- **US3**: Form action enqueues single-record job and exposes final status via report without request blocking.

---

## Notes

- [P] tasks touch independent files and can be executed in parallel.
- Story phases are independently testable once Foundational phase is complete.
- Use `admin action` terminology consistently.
- Preserve existing Redeye flow and `redeye_mp3_attach` behavior.
