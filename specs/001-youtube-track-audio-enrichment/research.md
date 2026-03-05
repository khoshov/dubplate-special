# Phase 0 Research: YouTube Track Audio Enrichment

## Decision 1: Async execution model

- **Decision**: Use Celery + Redis for all YouTube audio enrichment jobs (Discogs update path + manual admin actions).
- **Rationale**: The spec requires asynchronous execution and transparent reporting for long-running batch operations; queue workers prevent admin request timeouts.
- **Alternatives considered**:
  - Synchronous execution in admin request: rejected due to timeout and poor UX for medium/large batches.
  - Custom DB-only queue without Celery: rejected due to higher implementation/operational complexity for retries and scheduling.

## Decision 2: Audio extraction strategy

- **Decision**: Use `yt-dlp` in worker context for normalized audio extraction into local `audio_preview`.
- **Rationale**: `yt-dlp` provides reliable source handling, format selection, and resilient extraction behavior needed for heterogeneous YouTube media.
- **Alternatives considered**:
  - Direct HTTP download from `youtube_url`: rejected due to unstable direct media URLs and frequent source format variability.
  - Browser automation for media extraction: rejected due to heavier runtime cost and lower reliability for background jobs.

## Decision 3: Job reporting model

- **Decision**: Persist async run output in dedicated job report entities (job, per-record, per-track results).
- **Rationale**: Admin UX requires immediate enqueue feedback plus detailed post-run result (`updated/skipped/errors` with reasons).
- **Alternatives considered**:
  - Keep result only in logs: rejected because admin users need deterministic in-app visibility.
  - One flat JSON blob per job: rejected due to weak queryability and poor partial-progress observability.

## Decision 4: Matching policy

- **Decision**: Conservative match required: track title match + at least one artist match; otherwise `skipped (mismatch)`.
- **Rationale**: Reduces false-positive audio attachments and keeps acceptance tests deterministic.
- **Alternatives considered**:
  - Title-only matching: rejected due to higher risk of incorrect track attachment.
  - Always download when URL exists: rejected due to unacceptable quality risk.

## Decision 5: Retry and failure policy

- **Decision**: Up to 3 attempts per track with exponential backoff; then terminal `failed`.
- **Rationale**: Balances resilience against transient external failures and queue throughput predictability.
- **Alternatives considered**:
  - No retries: rejected due to high transient-failure sensitivity.
  - 5+ retries: rejected due to queue latency amplification with limited practical win.

## Decision 6: Concurrency conflict policy

- **Decision**: Do not allow parallel active enrichment jobs for the same record; new conflict run becomes `skipped (already running)`.
- **Rationale**: Prevents race conditions and non-deterministic overwrite order for `audio_preview`.
- **Alternatives considered**:
  - Allow parallel jobs (last-write-wins): rejected due to non-deterministic outcomes.
  - Cancel running job in favor of newest: rejected due to potential partial/hidden data loss.

## Decision 7: Environment topology

- **Decision**: Development is Docker-first (Django/Postgres/Redis/Celery in containers). Production uses systemd-managed services without Docker.
- **Rationale**: Matches actual team workflow and deployment architecture from project context.
- **Alternatives considered**:
  - Local-first non-container dev for this feature: rejected as mismatched to current team runtime.

## Decision 8: Module placement and naming

- **Decision**: Implement YouTube enrichment provider at `apps/records/services/audio/providers/youtube_audio_enrichment.py`.
- **Rationale**: Aligns with existing provider architecture and keeps functional responsibility explicit.
- **Alternatives considered**:
  - Put logic in generic `audio_service.py` only: rejected to avoid provider mixing and SRP drift.
  - Place module outside `audio/providers`: rejected as inconsistent with current code organization.

## Clarification Resolution Status

All Technical Context unknowns are resolved; no `NEEDS CLARIFICATION` remains.
