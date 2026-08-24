# Jobs API

## Overview

The Jobs API exposes endpoints for inspecting the state of individual
extraction pipeline jobs and their constituent steps. These endpoints
back the operator dashboard, the downstream status re-checks, and
Sibling D's idempotent rerun flow (which uses ``track_id`` as the
durable correlation key).

This document covers the **NFM-3597** Phase 1.5/1.6/1.7 reconciliation
surface:

- ``GET /api/v1/jobs/{job_id}/steps/{step_name}`` — single-step state
- ``GET /api/v1/extraction/jobs/{job_id}/steps/{step_name}`` (NFM-2883) —
  legacy single-step endpoint, preserved for backward compatibility.
- ``POST /api/v1/extraction/jobs/{job_id}/steps/{step_name}/rerun``
  (NFM-2884) — Sibling D, idempotent rerun.

The NFM-3597 endpoint supersedes the NFM-2883 surface for new callers:
it adds ``artifacts`` and ``error`` fields, renames
``completed_at`` → ``finished_at``, normalizes ``completed`` to
``succeeded`` in the public status enum, and supports ETag/304
revalidation. The integration task NFM-3599 will reconcile the two
paths in a follow-up rollout.

## Base URL

```
${NEXT_PUBLIC_API_URL:-http://localhost:8000}/api/v1
```

## Authentication

All requests require a JWT bearer token issued by ``/api/v1/auth/login``.
Service-account JWTs (scope ``extraction:ingest``) are NOT permitted on
this read-only endpoint — it is intended for the operator dashboard and
internal monitoring only.

```typescript
const headers = {
  Authorization: `Bearer ${token}`,
}
```

## Endpoints

### `GET /jobs/{job_id}/steps/{step_name}`  (NFM-3597 — Sibling C)

Return the current state of a single pipeline step within an
extraction job: status, ``track_id`` (the durable identity used by
Sibling D's rerun), artifacts, timestamps, and error message. Supports
ETag/304 revalidation via ``If-None-Match``.

#### Path parameters

| Name        | Type   | Description                                              |
|-------------|--------|----------------------------------------------------------|
| ``job_id``  | UUID   | Parent extraction job identifier.                         |
| ``step_name`` | string | One of ``chunk``, ``extract``, ``map``, ``quality_gate``, ``gap_scan``. |

#### Headers

| Name              | Required | Description                                          |
|-------------------|----------|------------------------------------------------------|
| ``If-None-Match`` | optional | ETag from a prior response. When matched → ``304``. |

#### Response — ``200 OK``

```json
{
  "job_id": "<uuid>",
  "step_name": "<string>",
  "status": "pending | running | succeeded | failed | skipped",
  "track_id": "<uuid or string>",
  "artifacts": [
    { "key": "<string>", "url": "<string>", "size_bytes": 0 }
  ],
  "started_at": "<iso8601 or null>",
  "finished_at": "<iso8601 or null>",
  "error": "<string or null>"
}
```

Field semantics:

- ``status``: normalized from the on-disk enum; ``completed`` is
  surfaced as ``succeeded``.
- ``track_id``: per-step UUID when NFM-3595's column lands; falls back
  to the parent job's ``track_id`` (NFM-2881) for backward compat.
  ``null`` when neither is set.
- ``artifacts``: list of output artifacts produced by the step. Sourced
  from ``step.metadata_.artifacts``; empty list when absent.
- ``started_at`` / ``finished_at``: ISO 8601 timestamps. ``null`` for
  steps that have not started / finished.
- ``error``: last failure reason when ``status`` is ``failed``;
  ``null`` otherwise.

Response headers:

- ``ETag``: deterministic over ``(track_id, status, finished_at)`` so
  any state change invalidates the cache. Format: ``"<sha256-prefix>"``.

#### Response — ``304 Not Modified``

Empty body. ``ETag`` header mirrors the matched validator. Returned
when ``If-None-Match`` matches the current ETag.

#### Response — ``404 Not Found``

Both unknown ``job_id`` and unknown ``step_name`` return the SAME
shape to avoid disclosing job/step existence:

```json
{
  "error": "step_not_found",
  "job_id": "<uuid>",
  "step_name": "<string>"
}
```

#### Response — ``503 Service Unavailable``

Returned when the database is unreachable (handled by existing
``503`` middleware). Includes ``Retry-After: 5``.

#### Example

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "If-None-Match: \"abc123def456\"" \
  http://localhost:8000/api/v1/jobs/8f2.../steps/extract
```

## Cross-references

- NFM-2883 — legacy ``/extraction/jobs/.../steps/...`` (different
  response shape, preserved for backward compat).
- NFM-2884 — Sibling D POST rerun endpoint; uses ``track_id`` from this
  endpoint as the durable identity.
- NFM-3595 — adds ``track_id`` column to ``extraction_steps``.
- NFM-3596 — orchestrator threads ``track_id`` into every step write.
- NFM-3599 — integration task that merges A+B+C+D.
## `POST /jobs/{job_id}/steps/{step_name}/rerun`

Re-execute a single pipeline step on an existing `extraction_job`. The
historical step's `track_id` is preserved on the original row; the
response carries a **new** `track_id` referring to the rerun row.

### Request

**Headers (preferred):**

| Header | Required | Notes |
| --- | --- | --- |
| `Idempotency-Key` | optional | Wins over the body field below when both are set. Replays within 24h return the original `track_id` with `Idempotent-Replayed: true`. |
| `Content-Type` | required | `application/json` (the request body is empty in practice). |

**Body** (`application/json`, all fields optional):

```json
{
  "client_request_id": "uuid-or-token-up-to-255-chars",
  "force": false
}
```

- `client_request_id` — only used when the `Idempotency-Key` header is
  absent. Same semantics (24h TTL replay detection).
- `force` — when `false` (default), rerunning a step whose latest
  status is `completed` returns `422 step_succeeded`. Set to `true`
  to rerun anyway.

### Response

**Success — 202 Accepted**

```json
{
  "job_id": "9d9c1f7e-...-uuid",
  "step_name": "extract",
  "track_id": "3a2b1c0d-...-uuid",
  "original_track_id": "5e6f7a8b-...-uuid",
  "status": "completed",
  "accepted_at": "2026-08-24T03:15:42.123456+00:00"
}
```

**Response headers:**

| Header | Value |
| --- | --- |
| `Idempotent-Replayed` | `true` if this was a 24h-window replay of an earlier request with the same `Idempotency-Key`; `false` on a fresh request. |

### Error responses

| Status | `error_code` | When |
| --- | --- | --- |
| `404` | `step_not_found` | `job_id` does not exist, or `step_name` is not in `EXTRACTION_STEP_TYPES` (`chunk`, `extract`, `map`, `quality_gate`, `gap_scan`). |
| `409` | `step_in_flight` | Another rerun for the same `(job_id, step_name)` is currently in `pending` or `running` state. |
| `422` | `step_succeeded` | The latest step row is in a terminal-success state (`completed`/`skipped`) and `force` is `false`. |

All error envelopes follow the project's standard shape:

```json
{ "detail": { "error_code": "step_in_flight", "message": "..." } }
```

### Idempotency semantics

- The `rerun_idempotency_keys` table stores `(idempotency_key PK,
  track_id, job_id, step_name, created_at)`.
- TTL is **24 hours**. Rows older than 24h are ignored on replay
  detection. A periodic cleanup job is out of scope for NFM-3543-D
  but is the obvious follow-up.
- When a duplicate request arrives within the window with the same
  `(idempotency_key, job_id, step_name)` tuple, the route returns the
  **original** rerun row's `track_id` and sets
  `Idempotent-Replayed: true`. No new step row is created.
- The header takes precedence over the body field. A request with
  `Idempotency-Key: foo` and `client_request_id: bar` is treated as
  the `foo` request, not the `bar` one.

### Step types

`step_name` must be one of:

- `chunk`
- `extract`
- `map`
- `quality_gate`
- `gap_scan`

(Sourced from `nfm_db.models.extraction_step.EXTRACTION_STEP_TYPES`.)

### Notes

- The rerun is dispatched synchronously by the orchestrator's
  `rerun_step` method, which creates a fresh `ExtractionStep` row
  with `metadata_.track_id = <new>` and `metadata_.rerun = true`.
- The job's overall status is **not** modified by a step rerun; only
  the targeted step row reflects the new execution.
- For audit traceability, the original step's `track_id` is preserved
  in `original_track_id` of the response.
