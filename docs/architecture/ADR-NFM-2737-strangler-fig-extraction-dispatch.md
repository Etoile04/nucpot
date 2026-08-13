# ADR-NFM-2737: Strangler-fig extraction pipeline dispatch + dual ExtractionJob resolution

**Status**: Accepted (CTO verdict 2026-08-10)
**Date**: 2026-08-10
**Authors**: CTO, on escalation from Hermes Agent audit (2026-08-09)
**Supersedes**: none
**Related**: [NFM-2737](/NFM/issues/NFM-2737) (this ADR's source issue), [NFM-2564](/NFM/issues/NFM-2564) (EPIC), [NFM-2677](/NFM/issues/NFM-2677) (strangler-fig decomposition, merged via PR [#728](https://github.com/Etoile04/nucpot/pull/728)), [NFM-2680](/NFM/issues/NFM-2680) (EXTRACTION_PIPELINE_V2 flag wrapper, superseded), [NFM-2687](/NFM/issues/NFM-2687) (ExtractionChunk V2 model + migration, PR [#725](https://github.com/Etoile04/nucpot/pull/725)), [NFM-2698](/NFM/issues/NFM-2698) (canonical ExtractionStep Protocol + flag verification, merged via PR [#727](https://github.com/Etoile04/nucpot/pull/727)), [NFM-2876](/NFM/issues/NFM-2876) (flip `NFM_EXTRACTION_V2_ENABLED` default to ON — **merged** to `main` via PR [#790](https://github.com/Etoile04/nucpot/pull/790), commit `4c5b03f3`). Implementation follow-ups: NFM-2738 (CPO: close #726 + fix #725 CI), NFM-2739 (long-term: ExtractionJob dataclass deprecation; see [ADR-NFM-2739](./ADR-NFM-2739-extraction-job-dual-class.md)). Architecture doc: [`nucpot-technical-architecture-2026-08-07.md`](./nucpot-technical-architecture-2026-08-07.md) §7.1 (strangler-fig rollout), §7.3 (data model dual-class debt).

---

## 1. Context

The NFM-2564 EPIC (本体驱动的材料数据抽取与问答平台) prescribed a **strangler-fig** migration from a 300-line monolithic `trigger_extraction()` to a 5-step `ExtractionOrchestrator` (chunk → extract → map → quality_gate → gap_scan) behind an `EXTRACTION_PIPELINE_V2` feature flag. The flag default-OFF lets the legacy path serve production traffic while V2 is wired behind it. Per §7.1 of the architecture doc, the rollout uses the "kills two birds with one stone" model: each step ships behind the flag, and only the last step (V2 fully wired) justifies flipping the flag ON.

Between 2026-08-08 and 2026-08-09, three PRs attempted different parts of this rollout and collided in the test queue:

| PR | Issue | Author intent | Real CI failure (this run, 2026-08-10) |
|---|---|---|---|
| **#727** (merged) | [NFM-2698](/NFM/issues/NFM-2698) | Canonical ExtractionStep Protocol + V2 flag verification | N/A — landed on main via `fbe9ec1` |
| **#725** (open) | [NFM-2687](/NFM/issues/NFM-2687) | ExtractionChunk V2 ORM + `050_…` migration | (a) ruff `extraction_chunk.py:381` UP037 — 1-line; (b) `test_alembic_has_a_single_head` hardcoded set (lines 222-234) excludes `050_…`; (c) `alembic heads` produces `049_…` only because migration 050 lives on an unmerged branch |
| **#726** (open) | [NFM-2680](/NFM/issues/NFM-2680) | EXTRACTION_PIPELINE_V2 flag dispatch wrapper (duplicates #727 inline) | 14 fail + 7 error: `ExtractionJob` has no `job_id` — the wrapper's `_run_v2_pipeline` would return ORM `ExtractionJob` (`.id`), not the dataclass (`.job_id`), crashing the response builder. 3 retarget commits already failed. |
| **#728** (merged) | [NFM-2677](/NFM/issues/NFM-2677) | Strangler-fig 5-step ExtractionStep + own dispatch wrapper | was: mypy `extraction_pipeline_dispatch.py:82` `[no-any-return]` — 1-line (now resolved by the merged commit) |

Verified during this review (2026-08-10, ~15 read-only operations):

1. **#728 is already on `main`.** `gh pr view 728` returns `state: "MERGED"`, all CI green as of 2026-08-09T14:57:56Z. The v4 endpoint already routes through `trigger_extraction_pipeline` (`apps/api/src/nfm_db/api/v4/extraction.py:252-256`), consuming the normalized dict (lines 266-269).
2. **The dual `ExtractionJob` is real but already mitigated at the wrapper layer.** `services/extraction_pipeline.py:189` defines `@dataclass class ExtractionJob: job_id: str` used by the in-memory `_job_store` (line 226). `models/extraction_job.py:30` defines `class ExtractionJob(Base): id: Mapped[uuid.UUID]` on the `extraction_jobs` table. These are NOT the same class. The dispatch wrapper at `extraction_pipeline_dispatch.py:105-109` returns `{"status": job.status.value, "job_id": job.job_id, ...}` — a flat dict — so the response builder never touches either class directly today.
3. **`NotImplementedError` is intentional.** `extraction_pipeline_dispatch.py:60-64` raises deliberately when `_run_v2_pipeline` is invoked, with a docstring explaining: "Content loading (fetching the actual document text before feeding the pipeline) is not yet implemented. Raising NotImplementedError prevents silent zero-result extractions when the flag is toggled ON prematurely." This is the safety guard that the legacy code path lacked.
4. **`lru_cache` on `is_extraction_v2_enabled`** (line 35) creates a real test-pollution hazard. The docstring (lines 18-22) already documents the remedy: `is_extraction_v2_enabled.cache_clear()` between cases.
5. **`#725` (ExtractionChunk V2) is a prerequisite for B2/B3 work** (open PRs #729 RawTextLoader, #730 SectionSegmenter, #731 ChunkBuilder), all of which assume the V2 chunk fields. It is **not** a hard blocker for #728 merge — the orchestrator's `from nfm_db.models.extraction_chunk import ExtractionChunk` (`extraction_orchestrator.py:25`) resolves against the V1 chunk model that already exists on main.

The structural redundancy that prompted this escalation: three layers of dispatch logic were in flight — inline flag routing in `trigger_extraction()` (already on main via #727), `#726`'s `trigger_extraction_dispatch()` wrapper (duplicate of inline), and `#728`'s `trigger_extraction_pipeline()` wrapper (different signature, normalized dict return, `NotImplementedError` guard). Only one is canonical.

## 2. Decision

We adopt four architectural choices, sequenced for risk:

### D1. Canonical dispatch = `trigger_extraction_pipeline` (the merged #728 wrapper)

All extraction call-sites use `trigger_extraction_pipeline(source_reference, source_type, **kwargs)` from `apps.api.src.nfm_db.services.extraction_pipeline_dispatch`. The wrapper is the **only** external entry point that consults `EXTRACTION_PIPELINE_V2` / `NFM_EXTRACTION_V2_ENABLED`. The inline flag routing in `trigger_extraction()` (the legacy path body) is untouched — it remains the fallback that the wrapper delegates to when `NFM_EXTRACTION_V2_ENABLED=false`.

Three properties of this wrapper make it canonical:
- **Normalized dict return** (`status`, `job_id`, `created_at`, `error_message`) decouples call-sites from the underlying source type. This is the **architectural** answer to Q3's dual-`ExtractionJob` response-builder concern — solved at the wrapper boundary, not at the dataclass.
- **`**kwargs` signature** is forward-compatible: B2/B3 step parameters (`RawTextLoader.doc_id`, `SectionSegmenter.max_heading_depth`, etc.) thread through without signature churn.
- **`NotImplementedError` guard** (lines 60-64) is preserved as the canonical safety mechanism. Production deploys must keep the flag OFF until `RawTextLoader` has production document-fetch wiring AND a `_extraction_job_to_dict` helper exists (see D3).

### D2. Close #726 as superseded; fix #725 CI to land between #728 and B2/B3

- **#726 (`trigger_extraction_dispatch`, NFM-2680)** duplicates #727 inline logic with the same flag but no `NotImplementedError` guard and no normalized dict return. It exists in PR form only — no production caller. Close as `cancelled` (superseded by #728).
- **#725 (ExtractionChunk V2 model + `050_…` migration, NFM-2687)** is the data-model half of the V2 path. Three CI fixes are required before merge:
  1. `apps/api/src/nfm_db/models/extraction_chunk.py:381` — 1-line ruff UP037 fix (PEP 604 union syntax)
  2. `apps/api/tests/test_extraction_provenance.py:222-234` — add `"050_extraction_chunk_v2_provenance"` to the `heads[0] in {…}` set
  3. From `apps/api/`, run `alembic heads` and confirm exactly one revision printed
  4. Rebase onto current `main` (post-#728) before re-running CI

#725 lands **between** #728 (already merged) and the B2/B3 series (#729/#730/#731). It is the dependency on which B2/B3 builds.

### D3. Before the V2 flag can be flipped ON, ship a `_extraction_job_to_dict` helper

The wrapper at `extraction_pipeline_dispatch.py:105-109` currently assumes the dataclass shape: `job.status.value` (enum), `job.job_id` (str), `job.created_at` (datetime), `job.error_message` (str|None). When `_run_v2_pipeline` no longer raises `NotImplementedError`, it returns an ORM `ExtractionJob` whose shape differs:
- `status` is a `str` column, not a `JobStatus.value` enum attribute
- `.id` is `uuid.UUID`, not `.job_id` is `str`
- `created_at` is mapped but on a different TimestampMixin base

**Resolution:** introduce `def _extraction_job_to_dict(job: ExtractionJob | OrmExtractionJob) -> dict[str, Any]` that normalizes both shapes. This helper must land **before** the V2 flag can be enabled in production. The acceptance criterion is a test that constructs both shapes, runs them through the helper, and asserts identical dict output.

This is the **short-term Q3 fix** — not the long-term one (see D4). It is a 1-helper-class change with strict scope (no dataclass mutation, no ORM schema change).

### D4. Long-term: deprecate the `ExtractionJob` dataclass in favor of ORM-only persistence

Within 1 sprint of D3 landing, the in-memory `_job_store: dict[str, ExtractionJob]` at `services/extraction_pipeline.py:226` should be replaced with a thin cache index over the ORM `extraction_jobs` table. The dataclass docstring at line 191-194 already names this as the extension point: "Stored in-memory for now. Extension point: persist to a dedicated `extraction_jobs` table for durability across restarts."

This eliminates the dual-class debt entirely. The legacy `trigger_extraction()` function either (a) reads ORM rows directly and constructs dicts via D3's helper, or (b) is replaced by an `ExtractionOrchestrator` thin wrapper that does the same. Either way, the dataclass becomes a transient object only used inside `trigger_extraction()`'s local scope and is removed when V2 is fully wired.

D4 is tracked as **NFM-2739** (follow-up issue). It is **not** in scope for the #725/#726/#728 cascade.

### D5. V2 `_load_v2_content` contract for non-`file` source types (NFM-2909)

Before the `EXTRACTION_PIPELINE_V2` flag can be flipped to default-True,
`_load_v2_content` must accept the source types that staging / prod
traffic actually uses today. The original implementation rejected every
non-`file` type with a generic `ValueError`, which would have converted
working DOI extractions into hard failures on the strangler-fig flip
([NFM-2869](/NFM/issues/NFM-2869)).

Decision matrix (locked in `extraction_pipeline_dispatch._load_v2_content`):

| `source_type` | Behavior                                                                |
| ------------- | ----------------------------------------------------------------------- |
| `file`        | Read from `source_reference` on disk. Missing path → `FileNotFoundError`. |
| `doi`         | Try `source_reference` as a file path first (matches V1 locally-resolved-PDF semantics). If absent and `EXTRACTION_STUB_MODE=true`, return the placeholder markdown in `_STUB_DOI_CONTENT` so the 5-step orchestrator can run end-to-end in CI. Otherwise raise `NotImplementedError` with the documented migration path (`process_literature` or pre-cached PDF). |
| `url`         | Explicit `NotImplementedError` — staging/prod traffic does not yet exercise it. |
| `datasource`  | Explicit `NotImplementedError` — V1 loads `content_md` from the `DataSource` row; V2 needs that wiring before this contract can move. |
| anything else | `NotImplementedError` with the supported list. |

Rationale for the rejected cases: better a loud, documented error class
(`NotImplementedError` with the migration path) than a silent half-working
implementation. The `url` and `datasource` types are out of scope for the
strangler-fig flip; tracking the wiring as separate follow-up tickets
keeps this change small and reviewable.

Rationale for `doi` in stub mode: tests that route through the dispatcher
with placeholder references like `doi:10.1234/example` (or local paths
that don't exist) previously failed with `FileNotFoundError` and produced
42 false-positive test failures on PR #790. The placeholder content
includes at least one markdown heading so the V2 `SectionSegmenter` step
emits ≥1 section — the same invariant the legacy stub fixture relied on.

D5 is implemented in [NFM-2909](/NFM/issues/NFM-2909). The loader contract
is locked by `tests/services/test_extraction_v2_content_loader.py`.

## 3. Rationale

- **D1's safety guard is the differentiator.** #728's `NotImplementedError` is not a missing-feature bug — it is the architectural commitment that the flag must remain OFF until the entire content-loading chain is wired. #726 lacks this guard and would silently produce empty extractions if flipped ON. The CTO escalation rule (§7.1 of the architecture doc: "重写 trigger_extraction 打断管线 → 必须用绞杀者模式 + feature flag") requires the guard.
- **D1's normalized dict is the architectural answer to Q3.** The audit correctly identified the dual-`ExtractionJob` as a structural risk, but the **mitigation** is not at the response-builder (where the audit suggested adding a `str(orm_job.id)` alias) — it's at the wrapper boundary, where the dict construction forces a uniform contract. This makes future changes (D3's helper, D4's deprecation) local to one file instead of touching every call-site.
- **D2 keeps #725 from blocking the dispatch story.** The three CI failures on #725 are all 1-line fixes; the model itself is sound. Closing #726 is correct because no production code paths to it and #728 already provides the canonical wrapper.
- **D3 is the minimum-surface-area fix for the day V2 actually runs.** The wrapper currently builds a dict from a dataclass; when V2 returns ORM, the wrapper needs to handle both shapes. Putting this in a single helper (instead of inline branching) keeps the wrapper's read-path linear and makes the helper independently testable against both shapes.
- **D4 is independent urgency.** The dataclass has been "temporary, in-memory" since NFM-2013 added the ORM table; the debt is real but not blocking. D3's helper can co-exist with the dataclass indefinitely; D4 only matters when someone needs durability across restarts (which the production system has had via ORM since NFM-2013).
- **#728's `lru_cache` pollution is a known and documented hazard.** The docstring (lines 18-22) prescribes `cache_clear()` between test cases. This is the established mitigation; no new architecture is required.

## 4. Non-goals

- No change to `trigger_extraction()`'s legacy code path. The wrapper continues to delegate to it when the flag is OFF.
- No schema change for the V2 chunk model. #725 ships the schema; we don't pre-empt its review.
- No flag-flip in production as part of this ADR. ~~The flag stays OFF.~~ D3 + a code-complete B2/B3 chain were prerequisites; the flip landed via NFM-2876 (`4c5b03f3`).
- No registry/migration-authority consolidation. (See [ADR-NFM-2139](./ADR-NFM-2139-deploy-rollback-architecture.md) for the parallel decision on production migration authority.)
- No new abstraction over the `**kwargs` signature. The dict-style pass-through is the contract; introducing a typed `ExtractionRequest` Pydantic model is out of scope here (could be a follow-up if B2/B3 work surfaces a real need).
- No retroactive rename of `trigger_extraction_dispatch` (#726). That PR is closed; the symbol never lands.

## 5. Acceptance criteria

- [ ] **D1:** `apps/api/src/nfm_db/api/v4/extraction.py:252-269` continues to import `trigger_extraction_pipeline` from `extraction_pipeline_dispatch`. No other module reads `is_extraction_v2_enabled()` directly.
- [ ] **D1:** Production deploy relies on the V2 pipeline by default — `NFM_EXTRACTION_V2_ENABLED` is unset, which now resolves to ON (post-[NFM-2876](/NFM/issues/NFM-2876)). The `NFM_EXTRACTION_V2_ENABLED=false` env override is the documented rollback path; flipping it ON manually is no longer a canary — it is the production path. See PR [#790](https://github.com/Etoile04/nucpot/pull/790) / commit `4c5b03f3` for the flip; [ADR-NFM-2739](./ADR-NFM-2739-extraction-job-dual-class.md) for the dataclass contract. **Source of truth:** `apps/api/src/nfm_db/config.py:52` (`extraction_v2_enabled: bool = True`). **Invariant:** any future default change to this flag MUST update `config.py` and this ADR in the same PR — no ordering inversion between code and docs.
- [ ] **D2:** PR [#726](https://github.com/Etoile04/nucpot/pull/726) is closed as `cancelled` with a comment linking to this ADR.
- [ ] **D2:** PR [#725](https://github.com/Etoile04/nucpot/pull/725) merges after: (a) ruff UP037 fix, (b) `test_alembic_has_a_single_head` set update, (c) `alembic heads` prints exactly one revision, (d) rebase onto post-#728 main.
- [ ] **D3:** Before V2 flag can be enabled, `_extraction_job_to_dict` helper exists in `extraction_pipeline_dispatch.py` (or a sibling module) with tests covering both dataclass and ORM input shapes producing identical dict output.
- [ ] **D4:** NFM-2739 issue created and assigned to Lead Engineer (or Codebase Onboarding) for the long-term dataclass deprecation. Not gated by D1–D3.
- [ ] **Audit trail:** This ADR is referenced from the comment thread on [NFM-2737](/NFM/issues/NFM-2737) and the implementation child issue NFM-2738.
- [ ] **D5:** `_load_v2_content` resolves `doi` via the V1 file-fallback path and falls back to `_STUB_DOI_CONTENT` in `EXTRACTION_STUB_MODE`; `url`, `datasource`, and unknown types raise `NotImplementedError` with the migration path. Locked by `tests/services/test_extraction_v2_content_loader.py`.

## 6. Changelog note

The architecture changelog should record:

> 2026-08-10 — Strangler-fig extraction pipeline dispatch: `trigger_extraction_pipeline` (PR #728 / NFM-2677) is the canonical wrapper. `EXTRACTION_PIPELINE_V2` flag remains default-OFF until `RawTextLoader` ships production wiring and a `_extraction_job_to_dict` helper exists. PR #726 (`trigger_extraction_dispatch`, NFM-2680) closed as superseded. PR #725 (ExtractionChunk V2 model, NFM-2687) lands between #728 and the B2/B3 step series. Long-term `ExtractionJob` dataclass deprecation tracked as NFM-2739. See [ADR-NFM-2737](/docs/architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md).
>
> 2026-08-12 — **V2 default flipped to ON** ([NFM-2876](/NFM/issues/NFM-2876), PR [#790](https://github.com/Etoile04/nucpot/pull/790), commit `4c5b03f3`). `NFM_EXTRACTION_V2_ENABLED` now defaults to `True`; an unset env var resolves to the V2 pipeline. The D1 acceptance criterion above is updated to reflect this — flipping the flag ON is no longer a canary, it is the production path, with `NFM_EXTRACTION_V2_ENABLED=false` as the documented rollback. E2E QA passed with one P2 doc warning (W1), which this entry closes; follow-up tracked as [NFM-2907](/NFM/issues/NFM-2907). Dataclass contract still governed by [ADR-NFM-2739](./ADR-NFM-2739-extraction-job-dual-class.md).
>
> 2026-08-13 — **Doc-fix (NFM-2935):** corrected the commit hash cited for NFM-2876's flip from the stale branch-only `573ddc48` to the actual merge commit `4c5b03f3`. Added source-of-truth citation (`config.py:52`) and a same-PR invariant: any future default change to `extraction_v2_enabled` MUST update code and this ADR in the same PR to prevent ordering inversion between code and documentation.
>
> 2026-08-12 — D5 ([NFM-2909](/NFM/issues/NFM-2909)): V2 `_load_v2_content` now resolves `doi` (file fallback + stub-mode placeholder) and explicitly rejects `url` / `datasource` with a documented migration path. Closes the loader-contract gap that PR #790's 42 false-positive `FileNotFoundError: test_paper.md` failures surfaced. Locked by `tests/services/test_extraction_v2_content_loader.py`.
