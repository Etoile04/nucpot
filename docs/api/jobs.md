# `/jobs/{id}/steps/{name}` — Step Lifecycle API

This document describes the per-extraction-job step lifecycle endpoints
introduced by NFM-3543 Phase 1.

The routes deliberately mirror the resource hierarchy
(`/jobs/{id}/steps/{name}`) rather than the legacy extraction-action
paths (`/extraction/jobs/{id}/steps/{name}` from NFM-2883/NFM-2884)
so clients can transition cleanly to the V2 contract.

| Method | Path | Owner |
| --- | --- | --- |
| `GET` | `/jobs/{job_id}/steps/{step_name}` | Sibling C (NFM-3597) |
| `POST` | `/jobs/{job_id}/steps/{step_name}/rerun` | NFM-3543-D (NFM-3598) |

---

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
