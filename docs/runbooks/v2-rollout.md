# V2 Extraction Pipeline Rollout Runbook

> **Status:** Active — streak in progress (1/7 days). Per
> [NFM-2924](/NFM/issues/NFM-2924): pre-flight complete, CI fix merged
> (PR #843, 2026-08-15), first scheduled run GREEN
> ([run 31869721752](https://github.com/Etoile04/nucpot/actions/runs/31869721752),
> 2026-08-15 06:32 UTC). Next expected run: 2026-08-16 ~06:00 UTC.

This runbook is the operational source of truth for promoting the V2
extraction pipeline from default-OFF to default-ON in production. The
parent PRD is [NFM-2916](/NFM/issues/NFM-2916) ("[P0-1] Flip
`NFM_EXTRACTION_V2_ENABLED=True` with V1/V2 parity"); the supporting
architecture decisions are captured in
[ADR-NFM-2737](../architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md)
(strangler-fig dispatch) and ADR-0007 §4 (7-day staging gate). Every step
below traces back to one of those sources.

The three phases — **Pre-flight**, **Staging green streak**, and
**Production shadow** — must be completed in order. Do not begin a later
phase until the prior phase's acceptance criteria are recorded here with a
link to the source issue.

---

## Pre-flight

The Pre-flight phase captures everything that must be true on `main` and
in the staging environment **before** the scheduled parity job starts
accumulating days toward the 7-day green streak. This is a one-shot
checklist executed by the Lead Engineer on the branch that flips
`NFM_EXTRACTION_V2_ENABLED` default to `True` (see
[NFM-2876](/NFM/issues/NFM-2876) and
[ADR-NFM-2737](../architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md)
D3 for the `_extraction_job_to_dict` prerequisite).

- [x] Parity harness lands on `main` (per [NFM-2922](/NFM/issues/NFM-2922))
      and runs GREEN on the canonical fixture set with the ADR-0007
      tolerance: structural equivalence on `KEntity` / `KRelation` shapes,
      numeric properties ±5 %, missing entities FAIL.
- [x] Observational CI is wired (per
      [NFM-2923](/NFM/issues/NFM-2923)) so PRs that touch extraction
      dispatch surface parity results without blocking merge.
- [x] `_extraction_job_to_dict` helper exists and is unit-tested against
      both the dataclass `ExtractionJob` and the ORM
      `extraction_jobs.ExtractionJob` shapes (ADR-NFM-2737 D3).
- [x] Scheduled parity cron is registered and has produced **at least one
      successful run** before the streak table is considered live.
- [x] `docs/runbooks/v2-rollout.md` (this file) is on `main` and contains
      the empty **Staging green streak** table below with today's date as
      row 1's scheduled-run anchor.

The Pre-flight phase closes when every box above is checked and the
issue [NFM-2924](/NFM/issues/NFM-2924) is unblocked.

---

## Staging green streak

This section is the audit trail for ADR-0007 §4 (7-day staging gate). Each
row corresponds to one calendar day of the scheduled parity job running
GREEN against the staging fixture set. The table is append-only: rows are
never edited, only added, so an aborted or back-dated streak is visible
at a glance.

Outcome is determined by the **parity test step** (`Run parity harness`),
not the overall CI job status. A CI job may fail for infra reasons (e.g.
annotation-posting step) while the parity test itself passed — such rows
are still PASS.

| Date (UTC) | Parity fixtures run | V1 fail count | V2 fail count | Parity delta | Outcome | Operator initials |
|------------|---------------------|---------------|---------------|--------------|---------|-------------------|
| 2026-08-15 | 4 | 0 | 0 | 0 % | PASS | LE (automated) |
| _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

> **Streak: 1/7 days.** First row sourced from scheduled run
> [31869721752](https://github.com/Etoile04/nucpot/actions/runs/31869721752)
> (06:32 UTC). Earlier rows dated 2026-08-13 and 2026-08-14 were removed —
> they were sourced from the now-removed `extraction-parity-staging-cron.yml`
> (a sibling-branch artifact), not the canonical `extraction-parity.yml`
> scheduled trigger per ADR-0007 §4.

**Parity fixtures** (4 canonical): mox-thermal-conductivity,
thoria-mixed-oxide, uo2-fcc-lattice, zircaloy-cladding-modulus.

**CI run log:** each row is sourced from the
[`extraction-parity`](https://github.com/Etoile04/nucpot/actions/workflows/extraction-parity.yml)
scheduled-run artifacts (`parity-report.json` / `parity-output.txt`), not from
PR-triggered runs or local re-runs. Download the `parity-report` artifact
from the corresponding run page.

**Acceptance for promotion to Production shadow:** seven consecutive rows
all showing `PASS`, the parity delta stays at or below the ADR-0007
±5 % tolerance, and no row in the streak is older than 10 calendar days.
[NFM-2924](/NFM/issues/NFM-2924) is the owning task; the Code Reviewer
verifies the table before clearing the gate.

---

## Production shadow

The Production shadow phase mirrors the staging green streak against real
production traffic with `NFM_EXTRACTION_V2_ENABLED=true` but **without**
flipping the default. Per NFM-2916 scope item 5, divergence above 1 %
triggers an automatic rollback; the runbook records each 24-hour window
here so the Code Reviewer and SRE Monitor can audit the rollout
post-hoc.

This section is a stub today. It will be expanded by the production
shadow task that follows NFM-2924 to capture:

- the 24-hour windowing scheme (start / end timestamps in UTC);
- the divergence counter source (the `logging.WARN` emitter referenced in
  NFM-2916 scope item 5) and its alert wiring;
- the auto-rollback path (flipping `NFM_EXTRACTION_V2_ENABLED=false` via
  the production env file, per
  [ADR-NFM-2737](../architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md)
  D1);
- the post-shadow decision record (promote / re-run / abandon) and its
  link back to NFM-2916.

| # | Window start (UTC) | Window end (UTC) | Divergence % | Result | Recorded by | Issue |
|---|--------------------|------------------|--------------|--------|-------------|-------|
| _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

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
