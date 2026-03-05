# Contract: Admin Audio Enrichment Interfaces

## Scope

This contract defines admin-facing interaction for async YouTube audio enrichment.

## Interface A: `update_from_discogs` enrichment trigger

- **Trigger**: existing admin action updates record from Discogs and enqueues YouTube enrichment.
- **Input**:
  - selected `record_id` set
  - acting admin user
- **Immediate Output**:
  - `job_id`
  - `queued_records`
  - `skipped_already_running`
  - human-readable enqueue confirmation
- **Deferred Output** (job report):
  - per-record and per-track `updated/skipped/failed`
  - skip/error reasons

## Interface B: `Обновить аудио из YouTube` (new admin action)

- **Trigger**: manual list-level admin action for selected records.
- **Input**:
  - selected `record_id` set
  - `overwrite_existing=true` (required)
  - acting admin user
- **Immediate Output**:
  - enqueue confirmation and `job_id`
- **Deferred Output**:
  - report with counters and reason details

## Interface C: Record-form button trigger

- **Trigger**: record change form button for single record.
- **Input**:
  - one `record_id`
  - acting admin user
  - overwrite mode according to feature rule for manual operations
- **Immediate Output**:
  - enqueue confirmation + `job_id`
- **Deferred Output**:
  - report entries for that record only

## Status/Reason vocabulary

- Track statuses: `updated`, `skipped`, `failed`
- Record conflict reason: `already_running`
- Track skip/error reasons: `mismatch`, `missing_youtube_url`, `invalid_url`, `download_error`, `retry_exhausted`

## Deterministic Behavior Guarantees

1. No parallel active enrichment processing for the same record.
2. Retry count is capped at 3 attempts per track.
3. Manual YouTube action always uses overwrite mode.
