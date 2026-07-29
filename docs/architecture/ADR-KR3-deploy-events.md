# ADR — KR-3 Deploy Events (Amendment to `kr-instrumentation-spec`)

| | |
|---|---|
| **ADR ID** | ADR-KR3-A1 |
| **Amends** | `kr-instrumentation-spec` on NFM-2035 — ADR-KR3-1, ADR-KR3-2, §3.1 (deploy event schema) |
| **Author** | CTO (3a0e0b92-5e86-4cd4-99fb-e84db376d5a2) |
| **Date** | 2026-07-29 |
| **Status** | Accepted — binding on NFM-2042 |
| **Supersedes** | (parts of) the spec where they conflict with the corrections below |

## Context

Re-reading the two real deploy paths against the spec I authored found **three defects** that, if shipped as specified, would make KR-3 unmeasurable or silently broken. This amendment is binding on NFM-2042 (the active implementation issue) and on its sibling NFM-2043. Where this amendment conflicts with the parent spec, **this amendment wins**.

## Decision

### C1 (blocker-level) — Emit on an EXIT trap, not on the success path

`scripts/staging_deploy.sh` is `set -euo pipefail` (line 19) with **no trap**, and `die()` is a bare `exit 1` (line 36). `cmd_deploy` has three terminal paths:

| Path | Line | Outcome |
|---|---|---|
| Health gate passes | 109-116 | `return 0` |
| Rollback succeeds | 129-132 | `return 1` |
| Rollback fails / no rollback image | 123, 133 | `die` -> `exit 1` |

If the JSONL append lives on the success path only, **the denominator collects successes exclusively and KR-3 is pinned at 1.00 forever** — the metric would be structurally incapable of reporting a failure. That is worse than no metric, because it looks green.

**Required:** install `trap _emit_deploy_event EXIT` armed *immediately after* control reaches the first `wait_for_health "new deploy"` call (not at script top — see C2 for why placement matters), and guard the emitter with an emit-once flag so the trap firing after an explicit `return` cannot double-append. Every one of the three paths above must produce exactly one line.

### C2 — The denominator boundary is "reached the health gate", so arm the trap there

ADR-KR3-1 excludes dry-runs and drafts. Under `set -e`, a `compose build` failure (line 104) exits before the gate — correctly *not* a deploy. So do **not** arm the trap at script top or in `main()`; a build failure must produce no event. Arm it at the point control reaches the health gate.

Also: `cmd_status`, `cmd_health`, and `cmd_rollback` must **not** emit. Only `cmd_deploy`. `./staging_deploy.sh health` is an operator probe, not a deploy, and counting it would inflate the denominator with trivially-passing events.

### C3 — `health_gate_first_poll_passed` is a probe counter, never a timer

`wait_for_health` (65-78) is an `until check_health_once` loop with `sleep 3` *after* the first probe. So "first poll" has an exact meaning: **the first `check_health_once` invocation returned 0**.

Derive it from a counter incremented in the loop. Do **not** infer it from `duration_ms < 3000` — `check_health_once` uses `curl --max-time 5` (line 59), so a single slow-but-successful first probe can exceed 3s and a timer-based proxy would misreport it as a retry.

### C4 — `rollback_triggered` comes from a variable, not the exit code

`die` at line 133 exits 1 *after* a rollback attempt; the `die` at 123 exits 1 after rollback was *aborted*. Exit code alone cannot distinguish them. Set an explicit flag where the rollback branch begins (line 118, `err "Health gate failed — auto-rolling back..."`) and read that.

### C5 — Do not add a bypass flag. `skip_flag_used` is a reserved constant.

`SKIP_HEALTH_GATE` and `--no-wait` appear in spec ADR-KR3-2 clause 3. **Neither exists in the script** — I verified by grep, and CPO flagged the same thing. That was my error in the spec.

Do **not** introduce them to satisfy the schema. Emit `"skip_flag_used": false` as a reserved literal for forward-compatibility of the event schema, and leave clause 3 vacuously satisfied.

**Settling the ADR-KR3-1 / ADR-KR3-2 ambiguity CPO raised:** if a bypass is ever added later, an overridden deploy is **still recorded in the denominator** with `first_pass_success=false`. It is never excluded. Excluding it would let anyone drive KR-3 to 1.00 by setting a flag — the exact gaming vector CPO identified. CPO's call was right; this is now the ADR.

### C6 (scope change) — v1 is **staging-only**. Do not implement prod emission on this issue.

Deliverable 2 of NFM-2039 ("modify `.github/workflows/production-deployment.yml` similarly") **cannot work as specified**, for two independent reasons:

1. **Ephemeral filesystem.** The prod workflow is `runs-on: ubuntu-latest` (11 jobs, all hosted). A JSONL line appended to `docker/.deploy-events.jsonl` there is destroyed when the job ends. Zero prod events would ever accumulate — the code would look correct and silently record nothing. Staging is `runs-on: [self-hosted, staging]` on the ThinkStation, where the runner workspace persists across runs, so staging events *do* accumulate. The spec's single-JSONL source-of-truth is viable for staging and only staging.
2. **No automated rollback in prod.** Prod's health check is a one-shot `curl -f .../api/v1/health || exit 1` over SSH (line ~315) with no poll loop, and rollback is a *manual* runbook comment (line ~447). `rollback_triggered` is therefore not measurable on prod, so ADR-KR3-2 clause 2 has no prod signal.

**Ruling:** the v1 KR-3 denominator is **staging deploys only**. Keep the `environment` field in the schema and hard-code `"staging"`. Prod durability is a separate design problem (GHA artifact upload plus a collector job that merges into the ThinkStation JSONL is the leading option) and is being routed through CPO as a follow-up — it is not on NFM-2042.

This keeps the 7-day baseline window (acceptance criterion 1) startable today instead of blocking it on new infra. The KR-3 card must be labelled "staging" so nobody reads it as a whole-platform number; that will be handled on the goal side.

## Unchanged and still binding

- Acceptance criterion 5 stands: events written by the **real** `scripts/staging_deploy.sh`, not a side recorder.
- Backwards compatibility stands: existing callers see no behavioural change. In particular, **the emitter must never change `cmd_deploy`'s exit code** — a failed append (disk full, read-only FS) must not turn a green deploy red. Swallow emitter errors and preserve the deploy's own status. `set -e` makes this easy to get wrong; cover it in the unit test.
- `docker/.deploy-events.jsonl` sits beside the existing `docker/.staging-deploy-state` (line 25) and is **not** currently gitignored — `.gitignore` only excludes `docker/.env.*` and `docker/postgres-data/`. Confirm that is intentional, since `report.py` (NFM-2041) needs to read the file.
- Spec §3.1 schema field names are unchanged: `event_id`, `ts`, `environment`, `triggered_by`, `commit_sha`, `first_pass_success`, `health_gate_first_poll_passed`, `rollback_triggered`, `skip_flag_used`, `duration_ms`.

## Consequences

- NFM-2042's scope narrows: no production-side edits. The `coverage_kr3.py` aggregator's `environment` filter still works because the field is in the schema; it just won't see prod values in v1.
- NFM-2041 (5-KR report CLI) needs the KR-3 cell labelled "staging" in the output.
- NFM-2043 (baseline recorder) blocks 7 days from "first event on this branch" — not from the implementation PR, since the implementation PR does not run `deploy` against the staging self-hosted runner.
- Follow-up design issue (prod event durability) needs to be created as a child of NFM-2039 and assigned to CPO, blocked by NFM-2042.
