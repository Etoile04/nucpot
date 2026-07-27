# ADR-NFM-796: Review System Data Model (Provenance + Status)

**Status**: Accepted (re-asserted 2026-07-27)
**Date**: 2026-07-07 (original) / 2026-07-27 (re-assertion)
**Authors**: CTO (original), Hermes Agent (re-assertion after 4 schema commits)
**Supersedes**: NFM-796 architecture sketch (informal comment thread)
**Related**: NFM-1555 (Phase 3 Epic), NFM-1557 (front-end UI), NFM-1871–1878 (acceptance & follow-ups)

---

## 1. Context

The Phase 3 review system needs a data model that supports:

1. **Source provenance** — every reviewable item must be traceable to the literature passage, page, and DOI that produced it.
2. **Review state** — pending / approved / rejected / needs_revision / corrected.
3. **Audit trail** — who reviewed what, when, with what note; immutable history.
4. **Feedback loop** — `needs_revision` corrections should eventually be re-routed to `pending` once the correction is applied, with timing tracked.

The system must accept data from three extraction paths simultaneously: LightRAG, OCR, and vision-based extraction. All paths converge on the same review queue.

---

## 2. Decision (re-asserted)

We adopt a **dual-track data model**:

### Track A — `KGReviewQueue` (centralized queue)
- The single source of truth for **what is currently in the review pipeline**.
- Schema: `kg_review_queue` (existing) — `id`, `item_type`, `item_id`, `status`, `review_reason`, `created_at`, ….
- New columns added by migration 022 (commit `d468602`): `reviewed_by`, `review_action`, `reviewed_at`, `review_note`.
- Filterable: `WHERE status = 'pending'` for the active queue.
- This is what `/api/v1/review/pending` queries.

### Track B — Per-table `review_status` columns (audit snapshot)
- `extraction_results.review_status` + `reviewed_by` + `reviewed_at` + `review_note` + provenance columns (`source_paragraph`, `source_page`, `source_doi`).
- `kg_nodes.review_status` + `review_note` + `reviewed_at`.
- `kg_edges.review_status` + `review_note` + `reviewed_at`.
- `property_measurements.reviewed_at`.
- These are **audit snapshots** — they reflect the most recent decision and support fast filtering for downstream analytics (`adoption_rate`, `by_type` counts).

**Why dual-track instead of strict single-source-of-truth?**

- The strict "single queue" interpretation breaks two real query patterns:
  1. **Stats queries** (`/api/v1/review/stats` with `by_type` breakdown) need to read the current status **per row** without a join through the queue table. Putting status only on `kg_review_queue` means `GROUP BY item_type` requires three joins back to `kg_nodes` / `kg_edges` / `property_measurements`.
  2. **Backfills and seed data** (Phase 2 demo audit B6 — `kg_nodes.review_status = 'pending_review'`) pre-populate the per-row column before a queue entry exists, breaking the assumption that the queue is the only state holder.
- The pragmatic resolution: **queue is the queue (active items), per-row columns are the audit snapshot (history)**. They are written together in the same transaction (`PATCH /api/v1/review/{id}` updates both), so they stay consistent in normal operation.

---

## 3. Schema (as implemented in migration `022_phase3_review_traceability.py`)

### 3.1 `extraction_results` (new columns)
- `source_paragraph TEXT` — verbatim text passage
- `source_page INT` — page number in the source PDF
- `source_doi VARCHAR` — DOI of the source paper
- `source_id UUID` — FK to `data_sources` (added later in `b86ac29`)
- `source_title VARCHAR` — denormalized for fast UI render (added in `b86ac29`)
- `review_status VARCHAR(50) NOT NULL DEFAULT 'pending'` — one of the review states
- `reviewed_by VARCHAR(255)` — FK to users
- `reviewed_at TIMESTAMPTZ`
- `review_note TEXT`
- `item_type VARCHAR` — `property` / `entity` / `relation`
- `item_data JSONB` — extracted structured payload
- `confidence FLOAT`
- `extraction_method VARCHAR` — `regex` / `vision` / `llm`
- `job_id` (now nullable — standalone review items can exist without a job)
- `updated_at TIMESTAMPTZ`

### 3.2 `kg_nodes`, `kg_edges`
- `review_status VARCHAR NOT NULL DEFAULT 'pending'`
- `review_note TEXT`
- `reviewed_at TIMESTAMPTZ`

### 3.3 `property_measurements`
- `reviewed_at TIMESTAMPTZ` (status is queried via the join to `kg_review_queue`)

### 3.4 `kg_review_queue` (extended)
- `reviewed_by UUID`
- `review_action VARCHAR(20)` — `approved` / `rejected` / `needs_revision`
- Optional rename to `review_queue` (decided: keep `kg_review_queue` to avoid migration risk on legacy callers)

### 3.5 `reviews` (existing stub, expanded)
- `reviewer_id`, `action`, `comment` columns added.

---

## 4. State Machine

```
            approve           reject             needs_revision
   pending ─────────▶ approved ──────▶ rejected ─────────────────▶ corrected
     │                                                                   │
     │                                                                   │
     └────────── reset (NFM-1877) ──────────────▶ pending  ◀────────────┘
                                                       ▲
                                                       │
                                              (correction applied)
```

Status enum values (defined in `models/review.ReviewStatus`):

- `pending` — newly extracted, awaiting first review
- `pending_review` — re-queued after correction (alias of pending; some seed data uses this string)
- `approved` — accepted by reviewer
- `rejected` — rejected, item excluded from downstream
- `needs_revision` — flagged for correction; remains in queue until `corrected`
- `corrected` — correction applied; feedback-loop metric recorded

Valid transitions are enforced server-side via `VALID_TRANSITIONS` and a 409 on invalid moves.

---

## 5. API Surface

All routes under `apps/api/src/nfm_db/api/v1/review.py`, mounted at `/api/v1/review/*` via `main.py:239`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/review/pending` | Paginated list of pending review items, filterable by `item_type` and `status`. Treats `pending_review` as alias of `pending` (b16e32b). |
| GET | `/api/v1/review/{item_id}/source` | Source provenance — paragraph + page + DOI + source title + source id. |
| PATCH | `/api/v1/review/{item_id}` | Update review status (approve / reject / needs_revision). Updates both queue and per-row columns. |
| POST | `/api/v1/review/batch` | Batch status update. |
| GET | `/api/v1/review/stats` | Aggregate counts by status and `by_type`; includes `adoption_rate` (NFM-1876) and `feedback-metrics` (NFM-1875). |
| GET | `/api/v1/review/feedback-metrics` | Average feedback loop time + correction count. |

### Authentication
- All PATCH/POST endpoints require `require_reviewer` dependency (reviewer role).
- All endpoints are auth-gated at the web layer by `ReviewAuthGuard`, which redirects unauthenticated users to `/login` (NFM-1557 AC).

### Cookie security
- `secure=False` for local HTTP development (`auth_endpoints.py`, commit `b86ac29`).

---

## 6. Front-End Components (`apps/web/src/components/review/`)

| Component | Responsibility |
|---|---|
| `ReviewAuthGuard` | Client-side JWT check; redirects to `/login` on miss (commit `9ba7714` switched from `router.replace` to `window.location.replace` for reliability). |
| `ReviewQueueTable` | Paginated list, status filter, bulk select, approve/reject buttons. Status display uses `STATUS_CONFIG` mapping (pending / approved / rejected). |
| `SourceProvenancePanel` | Source paragraph render with `<mark>` highlight on the extracted value. Shows DOI + page + source title (added in `b86ac29`). |
| `ConflictResolutionCard` | Side-by-side conflict resolution between conflicting review items. |

Routes:
- `/review/kg` — KG review queue (`(dashboard)/review/kg/page.tsx`)
- `/review/conflicts` — conflict resolution page
- `/admin/blog/review` — separate admin-blog review (unaffected by this ADR)
- `/admin/reference-data/review` — separate reference-data review (unaffected)

---

## 7. Feedback Loop (NFM-1875)

When a reviewer marks an item as `needs_revision`, the system:
1. Records the action in the queue (`review_action = 'needs_revision'`, `review_note` populated).
2. The item remains queryable under pending until `corrected`.
3. Once the correction is applied (manual or auto), `feedback-metrics` records `loop_time = corrected_at − reviewed_at` and increments `correction_count`.
4. Stats endpoint surfaces `adoption_rate = corrected / (corrected + rejected)` (NFM-1876).

---

## 8. Migration History

| Migration | Description |
|---|---|
| `022_phase3_review_traceability.py` | Original Phase 3 schema — adds provenance + review columns to `extraction_results`, `kg_nodes`, `kg_edges`, `property_measurements`; expands `reviews` stub. |
| `027_merge_heads_011_and_026.py` | Alembic merge to reconcile divergent heads from concurrent migration work (commit `b86ac29`). |

---

## 9. Consequences

### Positive
- All reviewable data has traceable provenance from day one (CTO's original requirement).
- Stats endpoint is fast — per-row columns avoid expensive joins.
- Backfills and seed data work without requiring queue entries first.
- Feedback loop metrics provide continuous quality signal.

### Negative
- Dual-track creates a consistency requirement: every state mutation must update both the queue and the per-row column. This is enforced server-side in `PATCH /api/v1/review/{id}`, but bugs in custom paths (e.g. direct DB writes during seed) can desync them.
- Strict reviewers expecting "single source of truth" may flag the dual-track as a violation. This ADR documents the trade-off explicitly.

### Mitigations
- Server-side state machine validation (`VALID_TRANSITIONS` + 409 on invalid).
- API contract test (NFM-1878) exercises the dual-track writes.
- Feedback-metrics endpoint surfaces inconsistencies as a measurable signal.

---

## 10. References

- Issue: NFM-796 (cancelled; absorbed into NFM-1555)
- Epic: NFM-1555 (Phase 3 Review System, done 2026-07-19)
- Subtask (front-end UI): NFM-1557 (done 2026-07-19)
- Acceptance Epic: NFM-1871 (done 2026-07-26)
- Follow-ups: NFM-1873 (provenance UI), NFM-1874 (split-view), NFM-1875 (feedback loop), NFM-1876 (adoption rate), NFM-1877 (pending reset), NFM-1878 (API contract tests)
- Code anchors: `d468602` (initial Phase 3 commit), `9ff8ae8` (stats schema fix), `b86ac29` (source_id + secure cookie), `b16e32b` (pending + pending_review expansion), `9ba7714` (ReviewAuthGuard redirect reliability), `74fd944` #390 (derive paragraph + stats schema polish)