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