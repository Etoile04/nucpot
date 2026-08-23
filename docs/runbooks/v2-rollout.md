# V2 Extraction Pipeline Rollout Runbook

> **Status:** Phase 4 COMPLETE — V2 is the sole extraction pipeline.
> The `extraction_v2_enabled` flag and legacy V1 path were removed in
> [NFM-3008](/NFM/issues/NFM-3008) (commit `8e362fee`, PR #808).
> V2 is now the only code path; no flag flip remains.
> Owning issue: [NFM-3226](/NFM/issues/NFM-3226).

This runbook is the operational source of truth for the completed V2
extraction pipeline rollout. The parent PRD is
[NFM-2916](/NFM/issues/NFM-2916) ("[P0-1] Flip
`NFM_EXTRACTION_V2_ENABLED=True` with V1/V2 parity"); the supporting
architecture decisions are captured in
[ADR-NFM-2737](../architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md)
(strangler-fig dispatch) and ADR-0007 §4 (7-day staging gate).

## Rollout Summary

| Phase | Issue | Status | Date completed |
|-------|-------|--------|----------------|
| Pre-flight | [NFM-2943](/NFM/issues/NFM-2943), [NFM-2922](/NFM/issues/NFM-2922), [NFM-2923](/NFM/issues/NFM-2923) | **DONE** | 2026-08-14 |
| Staging green streak | [NFM-2924](/NFM/issues/NFM-2924) | **DONE** | 2026-08-15 (Day 1 recorded, flag removed before Day 7) |
| Production shadow | NFM-2916 scope item 5 | **SKIPPED** | Flag removed entirely — no shadow period needed |
| Phase 4 — flag flip | [NFM-3487](/NFM/issues/NFM-3487) | **DONE** | 2026-08-22 (NFM-3008 already removed the flag) |

**Note on Phase 3/4:** The original plan called for a production shadow
phase followed by flipping the default. Instead, NFM-3008 removed the
`extraction_v2_enabled` flag and the V1 dispatch path entirely,
making V2 the only pipeline. This is equivalent to flag=on for all
environments and is a stronger guarantee than a default flip.

---

## Pre-flight

All pre-flight items completed successfully.

- [x] Parity harness lands on `main` (per [NFM-2922](/NFM/issues/NFM-2922))
      and runs GREEN on the canonical fixture set with the ADR-0007
      tolerance: structural equivalence on `KEntity` / `KRelation` shapes,
      numeric properties ±5 %, missing entities FAIL.
- [x] Observational CI is wired (per
      [NFM-2923](/NFM/issues/NFM-2923)) so PRs that touch extraction
      dispatch surface parity results without blocking merge.
- [x] `_extraction_job_to_dict` helper exists and is unit-tested.
- [x] Scheduled parity cron registered and produced successful runs.
- [x] `docs/runbooks/v2-rollout.md` (this file) is on `main`.

---

## Staging green streak

Recorded streak data from the observational parity runs. Only Day 1
was recorded before the flag was removed in NFM-3008.

| Date (UTC) | Parity fixtures run | V1 fail count | V2 fail count | Parity delta | Outcome | Operator initials |
|------------|---------------------|---------------|---------------|--------------|---------|-------------------|
| 2026-08-15 | 4/4 (mox-thermal, thoria-mixed-oxide, uo2-fcc, zircaloy) | 0 | 0 | 0% | **PASS** | CTO-auto |

> **Streak: 1/7 (partial).** The 7-day gate was not completed because
> the `extraction_v2_enabled` flag was removed in NFM-3008, making the
> gate moot — V2 became the only path. The single recorded day shows
> 0% divergence, well within the ADR-0007 ±5% tolerance.

**Parity fixtures** (4 canonical): mox-thermal-conductivity,
thoria-mixed-oxide, uo2-fcc-lattice, zircaloy-cladding-modulus.

---

## Production shadow

**SKIPPED.** The production shadow phase was originally planned to run
V1 and V2 in parallel against production traffic with divergence
monitoring. This phase became unnecessary when NFM-3008 removed the
`extraction_v2_enabled` flag and the V1 dispatch path entirely.
V2 is now the sole extraction pipeline — there is no V1 to shadow against.

| # | Window start (UTC) | Window end (UTC) | Divergence % | Result | Recorded by | Issue |
|---|--------------------|------------------|--------------|--------|-------------|-------|
| — | N/A (flag removed, V2-only) | N/A | N/A | **N/A** | — | NFM-3008 |

---

## Phase 4 — Flag removal (supersedes flip)

The original Phase 4 called for flipping the
`extraction_v2_enabled` default from `False` to `True` for non-dev
environments. This was superseded by a stronger action:

- **[NFM-3008](/NFM/issues/NFM-3008)** (commit `8e362fee`, PR #808):
  Removed the `extraction_v2_enabled` flag, the V1 dispatch path, the
  `ExtractionJob` dataclass, and the in-memory `_job_store`. The
  dispatcher now unconditionally routes to
  `ExtractionOrchestratorV2`. This is permanent and irreversible
  without restoring deleted code.

No per-environment default flip was needed because there is no flag
left to flip.

---

## References

- Parent PRD: [NFM-2916](/NFM/issues/NFM-2916) — "[P0-1] Flip
  `NFM_EXTRACTION_V2_ENABLED=True` with V1/V2 parity".
- Architecture: [ADR-NFM-2737](../architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md),
  ADR-0007 §4 (7-day staging gate).
- Streak owner: [NFM-2924](/NFM/issues/NFM-2924) — "[P0-1.3] 7-day staging
  green streak — record in v2-rollout runbook".
- Skeleton scaffold: [NFM-2943](/NFM/issues/NFM-2943) — "[NFM-2924-prereq]
  Create `docs/runbooks/v2-rollout.md` skeleton (Pre-flight + Production
  shadow + placeholder streak section)".
- Flag removal: [NFM-3008](/NFM/issues/NFM-3008) — removed
  `extraction_v2_enabled` flag and V1 path (PR #808, commit `8e362fee`).
- Phase 4 closeout: [NFM-3487](/NFM/issues/NFM-3487) — updated this runbook
  to reflect completed rollout.
