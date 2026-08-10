# ADR-NFM-2739 — `ExtractionJob` dual-class debt, and the canonical dict serialization boundary

- **Status:** Accepted (decision D3 implemented; column migration deferred to NFM-2739)
- **Date:** 2026-08-10
- **Author:** Lead Engineer (per CTO architectural contract in NFM-2743)
- **Issue:** NFM-2739 / NFM-2743 (D3 seam)
- **Related:** NFM-2737 (PR cascade architecture review #725/#726/#728), NFM-2738, NFM-2564 (Epic)

---

## 1. Context

The extraction pipeline carries **two** `ExtractionJob` classes that look like one
thing but model different lifecycle stages:

| Class | Module | Lifecycle stage | Persistence |
| --- | --- | --- | --- |
| `@dataclass ExtractionJob` | `nfm_db.services.extraction_pipeline` | Job *orchestration / request* state — what the caller asked for, plus job-local results (`figures`, `tables`, `extracted_count`, `staged_count`, `rejected_count`) and the staging linkage (`fill_batch_id`). | In-memory `_job_store: dict[str, ExtractionJob]`. |
| `class ExtractionJob(TimestampMixin, Base)` | `nfm_db.models.extraction_job` | *Ingestion / results* state — `total_received`, `created_measurements`, `reused_entities`, `skipped_duplicate_measurements`, `skipped_unknown_properties`, `skipped_duplicates`, `validation_errors`. | `extraction_jobs` table (alembic head `050_extraction_chunk_v2_provenance` lineage). |

This dual-class debt has accumulated for two reasons:

1. **The dataclass came first** (NFM-66, NFM-523.3) as a lightweight in-memory tracker.
2. **The ORM arrived later** (NFM-2013) for the `/api/v1/extraction/ingest/{job_id}/status` handler and never absorbed the orchestration fields — because adding columns to a hot ingestion path is invasive and the dataclass path was still load-bearing for the v4 endpoint (`api/v4/extraction.py:158-163` reads `job.fill_batch_id`).

### 1.1 The field diff (verified against `origin/main` @ `77ad7c4`)

| Bucket | Fields |
| --- | --- |
| **Common to both (12)** | `source_reference`, `source_type`, `status`, `error_message`, `started_at`, `completed_at`, `ontology_version_id`, `ontology_version_str`, `extract_figures`, `extract_tables`, `confidence_threshold`, `figure_types` |
| **Renamed (1)** | dataclass `job_id` → ORM `id` |
| **Both — do NOT treat as gap (1)** | `created_at` (ORM inherits from `TimestampMixin`) |
| **Present on dataclass, ABSENT on ORM (10)** | `fill_batch_id`, `extracted_count`, `staged_count`, `rejected_count`, `element_systems`, `cache_level`, `max_confidence`, `conflict_strategy`, `figures`, `tables` |
| **Present on ORM, ABSENT on dataclass (8)** | `corpus_id`, `total_received`, `created_measurements`, `reused_entities`, `skipped_duplicate_measurements`, `skipped_unknown_properties`, `skipped_duplicates`, `validation_errors` |

### 1.2 The confusion that prompted this ADR

PR #726's CI failures originated in **one** bug — `job.id` is a `uuid.UUID`, the
caller formatted it into a JSON response, and `json.dumps` raised `TypeError: Object
of type UUID is not JSON serializable`. Two classes with the same name, different
identity types, different field sets, and a hand-written ad-hoc normalization at
every dispatch boundary. Every call-site carried its own version of "what shape is
the result, again?" That is exactly the debt this ADR resolves.

---

## 2. Decision

**The dict — not either class — is the stable public interface for callers.**

A single module-private helper, `_extraction_job_to_dict`, is the **one** place
that converts either representation to the canonical 24-key dict. Both the legacy
in-memory dataclass path and the future V2 ORM path converge on it; call-sites
never have to branch on `is_extraction_v2_enabled()` to read the response.

### 2.1 The canonical dict shape (binding)

24 keys. Identical set regardless of input type. Defaults on the ORM path fill the
10 gap fields with the documented values:

| Key | Type | ORM default when field absent | Notes |
| --- | --- | --- | --- |
| `job_id` | `str` | — (ORM `id` → `str(job.id)`) | UUID → `str` is **mandatory**; this is the bug #726 fix. |
| `source_reference` | `str \| None` | — | |
| `source_type` | `str \| None` | — | |
| `status` | `str` | — | JobStatus enum `.value` on dataclass, raw str on ORM. |
| `error_message` | `str \| None` | — | |
| `created_at` | `str \| None` | — | ISO-8601 string or `None`; never raw `datetime`. |
| `started_at` | `str \| None` | — | ISO-8601 string or `None`. |
| `completed_at` | `str \| None` | — | ISO-8601 string or `None`. |
| `fill_batch_id` | `str \| None` | `None` | |
| `extracted_count` | `int` | `0` | |
| `staged_count` | `int` | `0` | |
| `rejected_count` | `int` | `0` | |
| `element_systems` | `list[str] \| None` | `None` | |
| `cache_level` | `str \| None` | `None` | |
| `max_confidence` | `str \| None` | `None` | |
| `conflict_strategy` | `str` | `"prefer_vlm"` | |
| `figures` | `list[dict]` | `[]` | |
| `tables` | `list[dict]` | `[]` | |
| `extract_figures` | `bool` | — | |
| `extract_tables` | `bool` | — | |
| `confidence_threshold` | `float` | — | |
| `figure_types` | `list[str] \| None` | — | |
| `ontology_version_id` | `uuid.UUID \| None` | — | Both classes carry this as `UUID \| None`. |
| `ontology_version_str` | `str \| None` | — | |

> **NFM-2759 note (NFM-2746 ruling):** On the ORM path, unset columns on
> transient (unflushed) instances are coalesced to the documented default
> via a `_coalesce(value, default)` guard inside `_extraction_job_to_dict`.
> This ensures the dict type contract holds regardless of flush state —
> SQLAlchemy 2.0 only fires `Column.default` / `server_default` at
> INSERT time, so `getattr(job, name, default)` returns `None` (the
> attribute exists but is unset) for transient instances.

### 2.2 What is **out of scope** for this ADR

- **Adding the 10 missing columns** to `extraction_jobs`. That migration is a separate
  reviewed PR under NFM-2739 — touching it now would conflate the D3 seam (a
  serialization decision) with a schema change (a persistence decision).
- **Removing the `@dataclass ExtractionJob`**. It stays as the live path while
  `extraction_v2_enabled` is `False` at `config.py:51` and the v4 endpoint continues
  to read `job.fill_batch_id` off it (`api/v4/extraction.py:158-163`).
- **Flipping `extraction_v2_enabled` to True.** Not part of this work; deferred
  to the V2 launch gate (per the strangler-fig pattern NFM-2680).

### 2.3 What changes

- **Added** `_extraction_job_to_dict` to
  `apps/api/src/nfm_db/services/extraction_pipeline.py`. Module-private
  (leading underscore), exported for the dispatch wrapper.
- **Migrated** `apps/api/src/nfm_db/services/extraction_pipeline_dispatch.py:105-109`
  from inline dict normalization to a single call to the helper.
- **Untouched**: `apps/api/src/nfm_db/models/extraction_job.py` (no schema change).
- **Untouched**: `apps/api/src/nfm_db/api/v4/extraction.py:158-163` (load-bearing
  legacy `fill_batch_id` read).
- **No new alembic migration.** Schema is unchanged.

---

## 3. Consequences

### 3.1 Positive

- **Single serialization boundary.** Bug #726 cannot recur because there is exactly
  one implementation of `job_id → str` and `JobStatus → str`.
- **Call-sites stop branching on the V2 flag.** The dispatch wrapper already
  encapsulated this for legacy callers; the dict shape makes the contract
  enforceable at the type level rather than convention.
- **Future V2 flip** is a one-line migration: replace
  `trigger_extraction` with the ORM-path orchestrator in the dispatch wrapper.
  The helper does not change.
- **Testability.** The key-set identity test (`assert set(from_dataclass) ==
  set(from_orm)`) is a regression guard for the entire D3 seam — any future
  field addition that breaks symmetry will fail CI immediately.

### 3.2 Negative

- **The dispatch now returns a 24-key dict**, not a 4-key dict. Callers that
  type-stub their response may break; the only in-tree caller is the v4 submit
  endpoint, which reads four keys and accepts the rest transparently.
- **Consumers that rely on `created_at` being a `datetime` will now see a `str`.**
  Pydantic v2 coerces ISO strings back to `datetime` in model construction, so
  the v4 submit response is unchanged. Any future caller that consumes the
  dispatch's dict directly must remember to coerce.
- **`ontology_version_id` stays as `uuid.UUID | None` in the dict.** This is
  asymmetric with `job_id` (always `str`). The choice is deliberate: callers
  that read `ontology_version_id` typically hand it to SQLAlchemy, which
  accepts `UUID` directly. Coercing it to `str` would force every downstream
  caller to coerce back. We accept this asymmetry because the contract binds
  only `job_id`, `status`, and the three timestamps.

### 3.3 What we deliberately did **not** do

- We did not introduce a `Union[ExtractionJob, OrmExtractionJob]` parameter
  type annotation via runtime import. The helper uses `TYPE_CHECKING` for the
  annotation and `getattr` / `hasattr` for dispatch. This avoids making
  `services/extraction_pipeline` load SQLAlchemy at import time.
- We did not extract the helper to a separate module. It lives next to
  `_job_store` and `trigger_extraction` because the dataclass it serializes is
  defined there. Moving it would create an import cycle or a thin wrapper
  module with no cohesion.

---

## 4. Followups

- **NFM-2739**: Add the 10 dataclass-only columns to `extraction_jobs` via an
  alembic migration, deprecate the dataclass, remove the helper (or leave it as
  a thin alias for one release for back-compat with internal callers).
- **NFM-2743 successor tasks**: When `extraction_v2_enabled` flips to `True`,
  the dispatch wrapper's V2 branch must return the same 24-key dict. The helper
  already supports this; only the wrapper changes.
- **3 issues link to this ADR before it existed**: NFM-2737, NFM-2738, NFM-2739.
  This ADR closes that documentation gap.