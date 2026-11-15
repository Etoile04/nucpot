# ADR — KR-3 Production-Side Deploy-Event Durability

| | |
|---|---|
| **ADR ID** | ADR-KR3-A2 |
| **Amends** | `kr-instrumentation-spec` (NFM-2035) §3.1 (schema), §4 (acceptance); ADR-KR3-A1 C6 (prod scope) |
| **Author** | CPO (7095567e-b1ff-4bba-a1ea-99263fbd48a4) |
| **Issue** | NFM-2053 |
| **Date** | 2026-07-30 |
| **Status** | Proposed — CPO design sign-off; implementation routed to Lead Engineer (see Hand-off below) |
| **Supersedes** | nothing |

## Context

ADR-KR3-A1 C6 ruled production emission **out of v1** because of two independent
defects in the spec's "modify `.github/workflows/production-deployment.yml`
similarly" deliverable:

1. **Ephemeral filesystem.** `production-deployment.yml` is `runs-on: ubuntu-latest`
   for 11 of 12 jobs. A JSONL line appended to a runner-workspace path is
   destroyed when the job ends, so the v1 emitter would record zero prod events
   silently — the code would look correct and emit nothing. (The deploy step
   itself is `runs-on: [self-hosted, production]` on the Mac Studio, but the
   post-deploy verification and reporting all run on hosted runners.)
2. **No automated rollback.** Prod's health check is a single
   `curl -f .../api/v1/health || exit 1` over SSH with no poll loop and no
   automated rollback path; rollback is a manual runbook step. `rollback_triggered`
   is therefore not measurable on prod, so ADR-KR3-2 clause 2 has no prod signal.

The 7-day staging baseline (NFM-2043) is therefore not blocked. Prod durability
is a separate design problem, tracked under NFM-2053 and now resolved here.

## Decision

**Adopt Option A: GHA artifact upload of a per-run JSONL fragment, plus a
collector workflow that downloads fragments since the last successful sync and
appends to the ThinkStation-resident master JSONL. Reject Option B (direct
SSH + flock write from inside `deploy-prod`) because it couples event survival
to the SSH step succeeding — the failure mode we are designing against.**

### Why Option A over Option B

| Criterion | A: artifact + collector | B: direct SSH + flock |
|---|---|---|
| Survives SSH deploy step failure | Yes — fragment is written before the SSH heredoc runs | No — emit happens after SSH, so SSH failure loses the event |
| Survives collector downtime | Yes — fragments sit in GHA storage up to 90 days | N/A |
| Replay / repair path | Yes — re-run collector with extended sync window | No |
| Real-time visibility | No — bounded by collector cadence (15 min default) | Yes |
| New infra | One new workflow (`prod-deploy-event-collector.yml`) | None |
| Couples to deploy-prod SSH step | No — emit is a separate step with `if: always()` | Yes |
| Failure-mode blast radius | Bounded to one fragment; rest of run is fine | Bounded to one event but only if SSH succeeded |

Both options keep the master JSONL on the Mac Studio — neither buys protection
against "Mac Studio is wiped" (a separate off-host backup problem, out of scope
here). The marginal value of B over A is real-time visibility, which we
sacrifice in exchange for decoupling the emitter from the SSH step. A future
optimization can layer a "fast path" notify (B-lite) on top of A if 15-min lag
proves painful; not now.

### Data flow

```
production-deployment.yml (deploy-prod job, runs-on: [self-hosted, production])
   |
   +-- pre-step: snapshot _PROD_EVENT_* globals at top of job
   |             (mirror staging's _DEPLOY_EVENT_* shape per ADR-KR3-A1 C1/C2)
   |
   +-- SSH heredoc -> Mac Studio -> docker compose up -> curl health -> exit
   |
   +-- post-step (if: always()):
   |     1. compute event JSON using deploy_event_emit --environment production
   |     2. write to $RUNNER_TEMP/prod-deploy-events/$RUN_ID.jsonl
   |     3. actions/upload-artifact@v7 (name: prod-deploy-event-$RUN_ID)
   |     4. arm-on-EXIT trap guarantees exactly one line per run (C1)
   |
   v
GHA artifact storage (90-day default retention)
   |
   v (triggered by workflow_run + schedule */15 * * * *)
prod-deploy-event-collector.yml (runs-on: self-hosted, production)
   |
   +-- read sync-state: /Users/lwj04/.nfmd/prod-event-sync-state.json
   |     { last_synced_run_id, last_synced_at, bad_run_ids: [] }
   |
   +-- gh api repos/:owner/:repo/actions/runs?created=>={last_synced_at}
   |     +-- filter runs where workflow == "Production Deployment"
   |     +-- for each, list artifacts named prod-deploy-event-* and download
   |
   +-- validate each fragment:
   |     * every line parses as JSON
   |     * every object has the section 3.1 schema fields
   |     * environment == "production"
   |     * event_id is a UUIDv4 (cheap uniqueness check)
   |     * if any check fails -> add run_id to bad_run_ids, alert Feishu, skip
   |
   +-- SSH lwj04@127.0.0.1 -> flock master JSONL -> tail -1 sanity check ->
   |     append valid fragments -> release lock
   |     (flock prevents self-collector race AND protects against the
   |      coverage_kr3.py reader racing the appender)
   |
   +-- update sync-state with last_synced_run_id + new bad_run_ids
        (only after successful append; sync-state write is on the same lock)
        v
/Users/lwj04/.nfmd/master-deploy-events.jsonl on Mac Studio
(ThinkStation-resident, same path the staging v1 emitter writes to,
or a sibling file per Schema below)
        v
coverage_kr3.py --environment production (new CLI flag in hand-off issue)
        v
KR-COMPANY-3 "Production Deployment First-Pass Success Rate" metric
```

### Retention

- **Master JSONL on Mac Studio:** indefinite, matching staging v1 behavior.
  Same `NFMD_DEPLOY_EVENTS_PATH` env var the staging reader already honors.
- **GHA artifacts:** 90-day default retention (the GHA storage tier we use).
  This is the durability floor: any fragment that the collector failed to
  merge within 90 days is unrecoverable from GHA. The collector cadence (15
  min) plus the GHA artifact lifecycle (90 days) gives us ~8 640 collection
  attempts before loss — adequate.
- **Sync-state file** (`/Users/lwj04/.nfmd/prod-event-sync-state.json`):
  indefinite. Backup strategy is the same as for the master JSONL (host-level
  Time Machine covers it; out of scope to redesign here).
- **Master JSONL rotation:** not in scope for v2. Staging has the same
  long-tail issue; weekly/monthly rotation can be added in a separate KR.

### Failure modes

1. **Network partition mid-sync (SSH to Mac Studio dies during append).**
   POSIX flock is released when the file descriptor closes (including on
   process death), so the lock does not leak. Sync-state is **not advanced**
   in this case; the collector's append step is "validate, append, advance
   state" — the advance is the last sub-step and only runs after a confirmed
   write. Next collector iteration picks up the same fragment and retries.
   The master JSONL may end with a partial line if the SSH process died
   mid-`printf '%s\n'`; recovery: the next collector iteration validates the
   last line of the master before appending, and if malformed, truncates to
   the last good line (logged + alerted).

2. **Partial artifact (collector downloads a truncated JSONL).**
   The fragment is expected to be exactly one line (one event per prod run).
   Validation step rejects fragments with zero lines or > 1 line, OR with any
   line failing JSON parse. Rejected fragments are recorded in
   `bad_run_ids` in sync-state and skipped on retry. A Feishu alert is sent
   per unique bad run_id. Manual recovery is required for genuine producer
   bugs (event-emit-step regression) — the collector cannot repair a corrupt
   fragment.

3. **Collector missed a run (cron downtime, GHA outage between runs).**
   Sync-state carries `last_synced_at`. The collector queries
   `actions/runs?created=>={last_synced_at - 5min skew}` so any run created
   since the last sync is picked up on the next iteration. The skew window
   protects against clock drift between the GH API and the Mac Studio.

4. **Collector races itself (two collector invocations overlap).**
   The collector's "append + advance state" sub-step runs under
   `flock /Users/lwj04/.nfmd/master-deploy-events.jsonl`. The second
   invocation blocks on `flock` until the first releases; the first's
   sync-state advance happens before release, so the second sees the
   advanced state and skips already-merged runs. This also protects
   `coverage_kr3.py` from racing the collector's append.

5. **Master JSONL grows unbounded.** Out of scope (see Retention). Same risk
   profile as staging; treated ad-hoc.

6. **`deploy-prod` runs on self-hosted but the SSH heredoc fails before the
   emit step runs.** Step uses `if: always()` AND is the last step in the
   job (with an EXIT trap, per ADR-KR3-A1 C1), so even on early job
   failure the fragment gets written and uploaded. The fragment in this
   case will have `first_pass_success=false` because `_PROD_EVENT_SUCCESS`
   was never set.

### Schema decisions

- **Reuse the section 3.1 schema verbatim.** No new fields, no field-name
  changes. `environment` is the discriminator (`"production"` for these
  events).
- **Single master JSONL.** Use the existing
  `docker/.deploy-events.jsonl` on the Mac Studio (or
  `/Users/lwj04/.nfmd/master-deploy-events.jsonl` — TBD in the hand-off
  issue; staging writer path is repo-relative `docker/`, prod collector
  path is host-absolute; pick one for v2 to avoid two-file aggregation).
- **`rollback_triggered` stays in the schema; prod always emits `false`.**
  Rationale: removing the field would break schema backwards-compatibility
  for the staging reader; emitting `null` would violate the JSON-bool
  contract the schema promises. The field's semantic for prod is
  documented here as **"prod has no automated rollback, so this field
  carries no signal — it is a schema-required placeholder."**
- **New metric name for prod.** Don't reuse
  "Deployment Success Rate" for prod, because that implies rollback
  semantics that don't exist on prod. The metric will be labelled
  "Production First-Pass Success Rate" (proposed; final naming is a CPO
  product call in the hand-off issue). The KR card for prod is a
  **separate card** from the staging KR-COMPANY-3 — they are not the same
  number.

### Acceptance criteria for the hand-off implementation issue

1. `production-deployment.yml` gains a `deploy-prod` post-step that calls
   the same `scripts/lib/deploy_event.sh` library with
   `--environment production`, wrapped in an EXIT trap per ADR-KR3-A1 C1/C2.
   Constraint: the emit step **never** changes `deploy-prod`'s exit code
   (same backward-compat rule as staging).
2. New workflow `prod-deploy-event-collector.yml` exists, triggers on
   `workflow_run` for Production Deployment + `schedule: */15 * * * *`,
   runs on self-hosted, with all six failure modes handled.
3. `scripts/okr/coverage_kr3.py` gains an `--environment` filter so the
   prod aggregator can be run separately from the staging aggregator
   without double-counting events.
4. New metric card **"Production First-Pass Success Rate"** is wired in
   `report.py` (NFM-2041 follow-up) with a label distinct from
   "Deployment Success Rate (staging)".
5. Unit tests cover: collector's flock race, partial-fragment rejection,
   sync-state advancement only-on-success, coverage_kr3.py `--environment`
   filter.
6. The implementation issue is created as a child of NFM-2053, assigned to
   Lead Engineer, `status=todo`, with `blockedByIssueIds` pointing at the
   artifacts issue (NFM-2053 done) so it auto-unblocks when NFM-2053
   closes.

## Consequences

- New GHA workflow (`prod-deploy-event-collector.yml`) to maintain. Owner:
  Lead Engineer for initial implementation; CPO for ongoing OKR ownership.
- `coverage_kr3.py` evolves to be env-aware. Backward-compatible: the
  default filter is "all environments" (staging + prod), same as today's
  behavior.
- The 7-day staging baseline (NFM-2043) is unaffected — staging-only events
  keep accumulating on the existing path. Prod begins accumulating as soon
  as the hand-off implementation lands and the next prod deploy runs.
- `rollback_triggered` semantics for prod are now explicitly documented as
  "schema placeholder, no signal". Future readers who see
  `rollback_triggered=false` in a prod event will not mistakenly infer "no
  rollback was needed".

## Hand-off

This issue is **design only**. Implementation will be created as a child
of NFM-2053 with title
`"Implement prod deploy-event durability (ADR-KR3-A2)"`, assigned to Lead
Engineer, with `status=todo` and `blockedByIssueIds=[NFM-2053.id]`.

The implementation issue must include:

- The exact set of files the LE will create / modify
  (production-deployment.yml step, prod-deploy-event-collector.yml,
  scripts/okr/coverage_kr3.py filter, docs).
- The schema-conformance test (same as staging's per ADR-KR3-A1 C5).
- An integration test that exercises all six failure modes above
  (the failure-mode table is the test plan).
- A coordination comment with CTO for whether `scripts/lib/deploy_event.sh`
  needs any change to support `environment=production` (current library
  already accepts `--environment` as a free-form string — should be no
  code change, but confirm in the LE PR).

CPO will sign off on the implementation after Lead Engineer hands it to
Code Reviewer and the implementation passes E2E.
---

## Amendment note — 2026-11-05 (NFM-5798 P2 Leg B): collector checkout diverted

The collector workflow this ADR introduced (`prod-deploy-event-collector.yml`,
runs-on `[self-hosted, production]` with a direct git fetch checkout) was
split under NFM-5798 P2 Leg B: the trigger set (`workflow_run` off
Production Deployment, `*/15` cron, `workflow_dispatch` + `dry_run`) moved
verbatim to a hosted orchestrator
(`prod-deploy-event-collector-orchestrator.yml`), which stages the collector
script as a manifest-verified artifact and dispatches the self-hosted
executor (`prod-deploy-event-collector-executor.yml`). The executor performs
zero git operations; the master JSONL, sync state, and loopback SSH hop
remain host-local exactly as this ADR specified. Motivation: the checkout
was the only failing step across the egress blackhole episodes
(NFM-5548 → 5556 → 5796 → 5798) and starved this lane 12h+ during the
2026-11-04 runner outage. The original workflow file is deleted; rollback =
revert. Guards: `scripts/okr/tests/test_nfm_5798_p2_collector_diversion.py`.

---

## Amendment note — 2026-11-11 (NFM-5938, NFM-5932 P6): master JSONL is the single KR-3 prod series source; 2026-09-07 → 2026-11-10 outage recorded; legacy NFM-2111 lane adjudicated (KEEP)

This addendum extends the 2026-11-05 amendment above. It records the
NFM-5932 repair tree (CTO Path-A adjudication 2026-11-10: **restore, do not
re-baseline**) and the P6 keep/retire decision for the legacy NFM-2111 lane.
Per-phase, first-hand evidence for every claim below lives on NFM-5932 and
its children (NFM-5933 … NFM-5939); commit- and sha-level pointers are in
the carrying PR. Ops procedures: `docs/runbooks/prod-deploy-events.md`.

### Single-source rule

`~/.nfmd/master-deploy-events.jsonl` is the **single authoritative KR-3
production deploy-event series**. The reporter is frozen on it:
`scripts/okr/coverage_kr3.py` resolves the prod series as `--prod-path` arg
→ `$NFMD_PROD_EVENTS_PATH` → `~/.nfmd/master-deploy-events.jsonl` (default).
Nothing may repoint the reporter at the legacy lane
(`~/.nfmd/deploy-events.jsonl`), and nothing writes the master except the
collector's SSH+flock append path or a reviewed, repo-committed one-shot
script with a timestamped pre-write backup (NFM-5932 hard constraint).

### Outage window 2026-09-07 → 2026-11-10 (data restored 2026-11-11)

- **Last pre-outage event:** run `34144432832`, ts `2026-09-07T17:11:01Z`
  (the anchor of master's 48 lines, last written by the 2026-09-08 manual
  backfill of `prod-event-backfill.jsonl`). CTO forensics 2026-11-10,
  re-verified first-hand: `prod-event-sync-state.json` showed
  `last_synced_run_id=0`, `last_synced_at=""`, 177 `bad_run_ids` — this lane
  **never completed an automated sync** in its entire life.
- **Era-A gaps** (single `prod-deploy-event-collector.yml`, ≤ 2026-11-05):
  1. *Permissions gap.* The workflow carried **no `permissions:` block**, so
     the runner `GITHUB_TOKEN` held only Contents/Metadata/Packages. Without
     `actions: read`, artifact listing saw nothing and every deploy run was
     false-marked bad — runner step `dfeca650…_4867f34c…` (2026-09-07
     08:14Z) shows a `advanced:false` tick completing in ~1s with the state
     already stuck. 177 poisoned `bad_run_ids` accumulated.
  2. *Alert gap.* `ALERT_WEBHOOK` was unset, so both alert call sites
     (`alert_bad_fragment`, `alert_append_failure`) silently no-op'd. The
     lane's death raised nothing for two months.
- **Era-B** (2026-11-05, NFM-5798 P2 Leg B — the amendment above) fixed the
  perms gap (executor: `actions: read`, `contents: read`) and the checkout
  fragility, but the lane stayed at zero syncs. Five latent defects,
  captured and confirmed first-hand under NFM-5933/5934: the never-synced
  window trap (`since = now−5min` made the backlog structurally unreachable),
  the macOS `flock(1)` absence (remote append rc=99 on every attempt, BSD
  `head -n -1` truncate failure in the same script), the fragment
  double-emit (deploy emit step + EXIT trap → two-line fragments), the
  silent download-retry class (stderr-only, no state change, no alert —
  the literal captured state of run `42473687433`), and `--dry-run` not
  being validate-only. Era-B was mechanically healthy and starved, not
  broken.
- **Repair** (NFM-5932 tree, 2026-11-10/11): P1 instrumented capture
  (PR #1534, CR PASS relay NFM-5944); P2 repair (PR #1537, CR PASS relay
  NFM-5942) — completed-run-only listing, python-`fcntl` remote critical
  section, emit-once guard, 7-day bootstrap lookback for never-synced
  state, per-run pending-failure sidecar with >3-consecutive-failure alert
  + non-zero exit, and a `--dry-run` side-effect gate; P3 reviewed one-shot
  backfill (PR #1536, squash-merged as `b61bd1e9` on human GO) executed
  2026-11-11T05:11→05:29:40Z under the collector flock with
  `--expect-*-sha` guards: **master 48 → 122 events** (Era-A bytes
  preserved + 74 legacy-lane events 2026-09-25→2026-11-08, zero duplicate
  ids, ts non-decreasing), sync-state advanced to
  `last_synced_run_id=42957760934` / `2026-11-11T05:29:25Z`, `bad_run_ids`
  177 → 255 (honest machine-reasoned marks); P4 alerting (PR #1538, CR PASS
  relay NFM-5946) — dedup-keyed gh-issue fallback + self-test door,
  end-to-end proven (issue #1539; runbook delta:
  `docs/runbooks/site-monitoring.md` §10.1, landing on `main` with the
  NFM-5932 merge batch). PRs #1534/#1537/#1538 were
  CR-passed but pending the NFM-5932 human merge batch at this addendum's
  drafting; statuses land with NFM-5939 integration.
- **Residual data gap:** 2026-09-08 → 2026-09-25 — 161 window runs
  unrecoverable as §3.1 events (123 corrupt two-line fragments + 38 with no
  artifact); ~123 may be salvageable later by a first-line salvage pass,
  and the surviving artifacts expire ≈2026-12-06 (board flag stands).
  The series is therefore frozen 2026-09-07T17:11:01Z → recovered through
  2026-11-08T12:49:40Z, with that 17-day interior gap permanent as §3.1
  events.
- **KR-3 computability:** the untouched reporter against the restored
  master computes **KR-3 = 0.4098** (2026-11-11); formal reporter proof and
  the stall tripwire are P5 (NFM-5937), gated on the NFM-5898 deploy-path
  resolution.

### Legacy NFM-2111 lane — decision: KEEP, non-authoritative

`~/.nfmd/deploy-events.jsonl` and its writers
(`collect-prod-deploy-events-{orchestrator,executor}.yml` +
`scripts/lib/collect_prod_events.py`, NFM-2111 per ADR-KR3-A1 §C6.1) are
**retained**. Rationale:

1. **It is the only writer that kept working through the outage** — 74
   events appended 2026-09-25→2026-11-08, and those appends are exactly
   what the P3 backfill used to restore the master series over that window.
   None of the failure classes that killed the ADR lane applied to it: the
   executor declares `actions: read`/`contents: read` explicitly and
   prefers a fine-grained PAT (`secrets.GH_TOKEN || secrets.GITHUB_TOKEN`),
   it appends with python `fcntl.flock` (no macOS `flock(1)` dependency),
   and its per-attempt artifacts sidestep the single-fragment defects.
2. **Independent redundancy with a different idempotency model** —
   `nfm-deploy-event-<run_id>-<attempt>` artifacts, processed-ledger
   coverage tokens (NFM-5829), and `sha256(event_json_text)` append
   idempotency. The master lane is single-fragment-per-run; a re-run's
   attempt-2 event is visible only here.
3. **Cost is low and the lane is healthy** post-Leg-B split (checkout-free
   executor; hosted `*/5` orchestrator; practical delivery cadence 3–4.5 h
   per the documented cron-drop history — late, not lossy, on its lane).

**Conditions of keep:** the legacy file is **not** a KR-3 source (the
reporter is frozen on master; the P5 tripwire watches master age
specifically); no new consumers attach to it; retirement may be revisited
only through a new ADR amendment after the repaired master lane has held a
clean sync record for ≥90 days (one artifact-retention floor). CTO sign-off
on this keep/retire decision is recorded on NFM-5932 before the carrying PR
merges (NFM-5938 AC).

Rollback for this addendum itself: revert the carrying PR (docs-only; no
runtime artifact references this section).
