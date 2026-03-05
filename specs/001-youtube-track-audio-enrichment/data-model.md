# Data Model: YouTube Track Audio Enrichment

## Entity: AudioEnrichmentJob

Represents one async enrichment run initiated by admin workflow.

| Field | Type | Rules |
|---|---|---|
| id | UUID | Primary key |
| source | enum(`discogs_update`,`manual_list`,`manual_record`) | Required |
| status | enum(`queued`,`running`,`completed`,`completed_with_errors`,`failed`) | Required |
| requested_by_user_id | FK -> auth user | Required |
| overwrite_existing | bool | Required; `true` for manual YouTube action |
| total_records | int | >= 0 |
| total_tracks | int | >= 0 |
| updated_count | int | >= 0 |
| skipped_count | int | >= 0 |
| error_count | int | >= 0 |
| created_at | datetime | Required |
| started_at | datetime nullable | Optional |
| finished_at | datetime nullable | Optional |

## Entity: AudioEnrichmentJobRecord

Represents per-record status within one job.

| Field | Type | Rules |
|---|---|---|
| id | UUID | Primary key |
| job_id | FK -> AudioEnrichmentJob | Required |
| record_id | FK -> records.Record | Required |
| status | enum(`queued`,`running`,`completed`,`completed_with_errors`,`failed`,`skipped`) | Required |
| reason_code | enum nullable | `already_running`,`missing_source`,`validation_error`,`none` |
| updated_count | int | >= 0 |
| skipped_count | int | >= 0 |
| error_count | int | >= 0 |
| created_at | datetime | Required |
| started_at | datetime nullable | Optional |
| finished_at | datetime nullable | Optional |

Constraints:
- Unique (`job_id`, `record_id`).
- For active statuses (`queued`,`running`), only one active job-record per `record_id` globally.

## Entity: AudioEnrichmentTrackResult

Represents per-track processing result.

| Field | Type | Rules |
|---|---|---|
| id | UUID | Primary key |
| job_record_id | FK -> AudioEnrichmentJobRecord | Required |
| track_id | FK -> records.Track | Required |
| status | enum(`updated`,`skipped`,`failed`) | Required |
| reason_code | enum nullable | `mismatch`,`missing_youtube_url`,`invalid_url`,`already_running`,`download_error`,`retry_exhausted` |
| attempts | int | 1..3 |
| matched_title | bool | Required |
| matched_artist | bool | Required |
| previous_audio_present | bool | Required |
| final_audio_name | string nullable | Saved file path in media |
| error_message | text nullable | Optional |
| created_at | datetime | Required |

Constraints:
- Unique (`job_record_id`, `track_id`).
- `attempts <= 3`.

## Existing Entity Impact

### records.Track

No new field required in baseline design.

Used fields:
- `youtube_url` (source URL)
- `audio_preview` (destination local mp3)
- `title`, `position_index`, related record/artists for matching

### records.Record

No breaking schema change required; linked to job-report entities by FK in `AudioEnrichmentJobRecord`.

## State Transitions

### Job state

`queued` -> `running` -> (`completed` | `completed_with_errors` | `failed`)

Transition rules:
- `completed`: no per-track failed results.
- `completed_with_errors`: at least one `skipped` or `failed`.
- `failed`: systemic run-level failure before normal completion.

### JobRecord state

`queued` -> `running` -> (`completed` | `completed_with_errors` | `failed` | `skipped`)

### TrackResult state

Terminal only on write:
- `updated`
- `skipped`
- `failed`

## Validation Rules

1. Match policy: update only if title matched and at least one artist matched.
2. Retry policy: max 3 attempts with exponential backoff.
3. Concurrency policy: if record already has active job, new run marks record as `skipped (already_running)`.
4. Overwrite policy:
   - manual YouTube action: overwrite existing `audio_preview`.
   - Discogs update path: behavior controlled by flow defaults from spec.
