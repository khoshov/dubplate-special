# Contract: Celery Task Payloads for YouTube Audio Enrichment

## Task 1: `records.youtube_enrichment.run_job`

Executes full job processing for selected records.

### Payload

```json
{
  "job_id": "uuid",
  "record_ids": [123, 456],
  "overwrite_existing": true,
  "requested_by_user_id": 42,
  "source": "manual_list"
}
```

### Rules

1. `job_id` must exist in job-report table.
2. `record_ids` cannot be empty.
3. For each record, if active job already exists, mark `skipped (already_running)` and continue.

## Task 2: `records.youtube_enrichment.process_record`

Optional fan-out task for one record under a parent job.

### Payload

```json
{
  "job_id": "uuid",
  "record_id": 123,
  "overwrite_existing": true
}
```

### Rules

1. Conservative matching required (title + at least one artist).
2. Retry policy: max 3 attempts with exponential backoff.
3. On retry exhaustion, write terminal `failed` result with reason.

## Result Semantics

- Worker updates persistent job report entities (job, record, track rows).
- Final job status:
  - `completed` if no failures/skips,
  - `completed_with_errors` if mixed outcomes,
  - `failed` for run-level fatal interruption.
