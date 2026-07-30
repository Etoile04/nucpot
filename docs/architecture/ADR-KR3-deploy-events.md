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

## Amendment C6.2 — runs-on fact correction (NFM-2074 CTO ruling, 2026-07-30)

Status: **Accepted.** Author: CTO (3a0e0b92). Corrects the factual premise of §C6 reason (1). The **ruling** of §C6 stands; the **premise** is amended.

### Why C6.2

The §C6 reason (1) premises that the prod workflow is `runs-on: ubuntu-latest`
(11 jobs, all hosted). That premise is **factually wrong**.

`.github/workflows/production-deployment.yml:278` declares the `deploy-prod`
job as `runs-on: [self-hosted, production]` — a self-hosted runner on the
Mac Studio host (same machine as the prod compose stack; see the workflow
header comment at lines 270-275). The runner workspace persists across runs,
so a JSONL append in that job WOULD survive the job end.

The 11 `ubuntu-latest` jobs in that workflow are pre- and post-deploy
checks (yaml-lint, security-scan, type-check, python-lint, test-web,
test-api, performance-test, build-web, smoke-test, notify). The deploy
itself is self-hosted; those hosted jobs are not where a deploy event
would land. There is no durability blocker for v1 prod emission on the
`deploy-prod` step.

### Effect on C6 vs C6.1

- **§C6 reason (1) premise**: withdrawn. A simple `deploy_event_emit`
  on the self-hosted `deploy-prod` job WOULD persist.
- **§C6 *ruling*** (v1 staging-only): stands. Withdrawing it now would
  re-scope NFM-2042 mid-Code-Review and reopen an already-stable
  schema. The staging-only baseline is preserved.
- **§C6 reason (2)** (no automated rollback in prod) is independent and
  unchanged. `rollback_triggered` remains a staging-only meaningful
  field; prod emits the literal `false` per C6.1.3.
- **§C6.1** (production durable producer) already accommodates the
  corrected fact: stage 1 lands on a *hosted* runner because the
  event-assembler step can run independently of the deploy, and
  C6.1.1's GHA-artifact durability hop is still valuable (decouples
  event assembly from deploy status). C6.1 stands as-is.

### CTO reconciliation outcome for NFM-2074

The uncommitted working-tree state on branch `NFM-1909-okr-weekly-standup`
already implements the C5 + C6 reconciliation in six files
(`scripts/staging_deploy.sh`, `.github/workflows/production-deployment.yml`,
`scripts/lib/deploy_event.sh`, `scripts/okr/coverage_kr3.py`,
`scripts/okr/tests/test_deploy_event.py`,
`scripts/okr/tests/test_staging_deploy_emits_event.py`).

- **C5 ruling**: accept. The shipped NFM-2042 code at commit `9c1209b7`
  violated §C5 by introducing `SKIP_HEALTH_GATE` handling in
  `staging_deploy.sh:135-140`. The working-tree state removes it and
  hardcodes `--skip-flag-used false`, exactly per the reserved-literal
  rule.
- **C6 ruling**: accept. The shipped NFM-2042 code at commit `9c1209b7`
  violated §C6 by shipping a prod emission step in
  `.github/workflows/production-deployment.yml:367` writing to a
  host-absolute JSONL path. The working-tree state removes that step,
  restoring v1 staging-only.

**Required for NFM-2042 to merge cleanly**: Code Reviewer must NOT
approve commit `9c1209b7` as-is. Lead Engineer must commit the
working-tree state, and Code Reviewer re-reviews the amended commit.
Once that lands, the committed document and the merged code agree —
fulfilling acceptance criterion 3.

## Amendment C6.1 — Production durable producer (NFM-2092, 2026-07-30)

Status: **Accepted.** Author: CTO (3a0e0b92). Amends §C6 above.

### Why C6.1

NFM-2042 closed the v1 baseline with staging-only emission. KR-COMPANY-3 is a
whole-platform KR and reading it as "staging only" misleads the board. C6.1
extends the same JSONL aggregator pattern to production without weakening the
two guarantees §C6 was written to protect.

### C6.1.1 — Two-stage durable producer

Stage 1 (hosted runner, ephemeral):
- A new job in `.github/workflows/production-deployment.yml`, `emit-prod-event`
  (or an extension of the existing `smoke-test` job), runs on `ubuntu-latest`
  with `if: always() && needs.deploy-prod.result != 'skipped'`.
- It receives the deploy commit, triggered-by, duration, and final pipeline
  status as job outputs.
- It assembles one deploy event object that conforms to the §3.1 schema
  (same field names, same `event_id` UUID-v4 convention, same boolean
  literalisation rules — reuse `scripts/lib/deploy_event.sh` is not possible
  because bash on `ubuntu-latest` cannot reach the ThinkStation JSONL; the
  assembly is mirrored in Python so the field order and escape rules match).
- It uploads that JSON object as a **GHA artifact** named
  `nfm-deploy-event-<run_id>-<attempt>.json` with
  `retention-days: 90`. Artifacts are GHA-managed storage and survive job
  teardown — this is the durability hop that fixes §C6 reason (1).

Stage 2 (self-hosted collector, persistent):
- New workflow `.github/workflows/collect-prod-deploy-events.yml`.
- Runs on `[self-hosted, prod-collector]` with `schedule: cron: '*/5 * * * *'`
  plus `workflow_dispatch` for back-fill. The collector label pins it to the
  Mac Studio runner (same host as the prod compose stack).
- Uses `gh api` with a `GH_TOKEN` (repo-scoped PAT or fine-grained token with
  `actions:read`) to enumerate `production-deployment.yml` runs since the last
  successfully-processed run.
- For each new run, downloads the `nfm-deploy-event-*.json` artifact, runs
  the same schema validation the staging writer uses, then **atomically
  appends** the line to `${NFMD_DEPLOY_EVENTS_PATH:-docker/.deploy-events.jsonl}`.
  Atomic-append uses the same `O_APPEND`-on-short-line discipline
  `scripts/lib/deploy_event.sh` enforces, so concurrent staging and prod
  appenders cannot interleave a line.
- Records the processed `run_id` and a SHA-256 of the event payload in
  `${NFMD_DEPLOY_EVENTS_PROCESSED_PATH:-${NFMD_DEPLOY_EVENTS_PATH}.processed}`
  (default: `<jsonl-path>.processed`), one line per processed run. On restart
  the collector re-reads this ledger before the API query so no event is
  ever appended twice (idempotency is on `sha256`, not `run_id`, to handle
  GHA retry-on-replay of the producer job).

### C6.1.2 — Why this satisfies both §C6 reasons

1. **Ephemeral filesystem.** The `ubuntu-latest` job's append is **not** a
   filesystem write — it is an artifact upload. GHA artifact storage is
   persistent (90-day retention by default; configurable per-repo). The job
   teardown does not destroy the event.
2. **No automated rollback in prod.** Production events record
   `rollback_triggered: false` as a reserved literal — the field is present
   for schema forward-compatibility (staging sets it true on auto-rollback
   per §C4) but on prod the value is **not measurable** and **not used in the
   denominator**. The prod schema does not invent a "manual rollback" flag;
   the design is honest about the gap. Rollback observability for prod is a
   separate problem (out of scope for KR-3).

### C6.1.3 — `rollback_triggered` semantics change

This is the only semantic change to the event schema. Producers MAY emit
`rollback_triggered: false` for production; consumers MUST treat the field as
"staging-only meaningful". The aggregator implementation will not filter on
this field for prod rows; it filters only on `first_pass_success` and
`environment`.

### C6.1.4 — `coverage_kr3.py --environment` filter

`scripts/okr/coverage_kr3.py` gains a new `--environment` flag with values
`staging` (default — preserves the v1 baseline) and `production`. The flag
filters `load_events()` output before `filter_window()` and `compute_value()`.
The `n` field in the report reflects the filtered count, so the same JSONL
file can answer "what is the staging success rate?" and "what is the prod
success rate?" independently.

Acceptance criterion: `coverage_kr3.py --environment staging` returns the
**exact same** value, `n`, and `computed_at` shape as the pre-C6.1 run on
the same JSONL. `coverage_kr3.py --environment production` returns the new
prod series once the collector has merged at least one event.

### C6.1.5 — `NFMD_DEPLOY_EVENTS_PATH` repo variable

The path is no longer a developer's home directory. A repo variable
`NFMD_DEPLOY_EVENTS_PATH` is read by:
- `coverage_kr3.py` (already does — see the docstring)
- `scripts/lib/deploy_event.sh::deploy_event_path` (already does)
- The new collector workflow

Default fallback for all three is `<repo>/docker/.deploy-events.jsonl`. The
variable lives at the **repo** level (`Settings → Secrets and variables →
Actions → Variables`) so every workflow reads the same value without a
per-developer override.

### C6.1.6 — Idempotency / failure modes

- **Collector crash mid-run.** The processed-run ledger is appended **after**
  the JSONL append succeeds. A crash between the two means the next
  collector invocation re-processes the run, but idempotency on
  `sha256(event_json)` (not `run_id` alone) means re-processing is a no-op.
- **Artifact missing for a run.** The collector logs and skips, leaving the
  run_id in the ledger with `status=missing`. A daily sanity check (a
  workflow on the same self-hosted runner) compares the count of prod events
  emitted by `coverage_kr3.py` against the count of `production-deployment`
  runs in the trailing 7 days and alerts on drift.
- **Schema-invalid artifact.** The collector quarantines the artifact
  contents to `${NFMD_DEPLOY_EVENTS_PATH}.quarantine/<run_id>.json` and
  records `status=invalid` in the ledger. The aggregator is unaffected.

### C6.1.7 — Out of scope (deliberate)

- **Manual rollback observability for prod.** Not a KR-3 input.
- **Multi-region prod deploys.** Single prod compose stack on Mac Studio;
  scale-out is a separate ADR.
- **Streaming emission.** This design is poll-based (5-min cron). For
  real-time dashboards, an HTTP POST to the aggregator service would be
  a future enhancement but is not required to compute the KR.

### Cross-references

- Source issue: [NFM-2092](/NFM/issues/NFM-2092)
- Parent KR-3 implementation: [NFM-2039](/NFM/issues/NFM-2039)
- v1 baseline: [NFM-2042](/NFM/issues/NFM-2042)
- Original §C6 rationale: see above

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

## Amendment C6.3 — Reconciliation of ADR-KR3-A1 C6.1 with ADR-KR3-A2 (NFM-2108 CTO ruling, 2026-07-30)

Status: **Accepted.** Author: CTO (3a0e0b92). Reconciles this ADR's §C6.1.4 and
§C6.1.5 with `ADR-KR3-prod-emission.md` (ADR-KR3-A2, author CPO, issue NFM-2053).

### Why C6.3

A2 was authored in parallel with the C6.1 build-out and reached implementation
(NFM-2109, commit `e00c28f0` plus uncommitted worktree edits) while still
carrying `Status: Proposed`. A2 amends §C6 and, in doing so, contradicts two
C6.1 clauses. Both designs edit the same file (`scripts/okr/coverage_kr3.py`),
and NFM-2113 is scheduled to merge them. Leaving an Accepted design and a
Proposed design in conflict would make that integration ambiguous. This
amendment rules on each conflict.

### C6.3.1 — `--environment` default: **A2 wins.** C6.1.4 is amended.

C6.1.4 specified `staging` as the default so an unfiltered read could not
silently conflate the two series once prod events landed in the same JSONL.
A2 splits the series into **two files** (a staging JSONL and a prod master
JSONL), which removes the conflation hazard *by construction*. With separate
files the reason for the `staging` default no longer exists, and `all` is the
strictly more backward-compatible default: a pre-C6.1 caller that passes no
flag continues to read everything, exactly as before.

Ruling: `ENVIRONMENTS = ("all", "staging", "production")` with `all` as the
default is **accepted**. C6.1.4's "default — preserves the v1 baseline"
wording is superseded.

The C6.1.4 acceptance criterion is **restated**, because the original phrasing
is unsatisfiable under an exact-match filter: `--environment staging` reproduces
the pre-C6.1 run only for events that carry `"environment": "staging"`; any
legacy line written before the field existed is dropped. The binding criterion
is therefore that the **default** (`all`) invocation must reproduce the
pre-C6.1 `value` and `n` on the same JSONL. That is the backward-compatibility
gate.

### C6.3.2 — Hardcoded home-directory paths: **rejected.** C6.1.5 stands.

C6.1.5 states verbatim: *"The path is no longer a developer's home directory."*
The current implementation defaults to one operator's home directory in at
least five places:

- `scripts/okr/coverage_kr3.py` — `_DEFAULT_PROD_PATH = Path("/Users/lwj04/.nfmd/master-deploy-events.jsonl")`
- `scripts/okr/prod_event_collector.py` — `--sync-state` and `--master-jsonl` argparse defaults

This is the exact defect C6.1.5 was written to prevent, and it is disqualifying
for a platform meant to outlive any one operator's machine: the collector
becomes unrunnable by a second maintainer, in CI, or on a replacement host, and
it fails by silently reading or writing the wrong file rather than erroring.

Ruling: env-var-first resolution is correct and is retained, but the **fallback
must not name a user**. Required shape:

- prod master JSONL ← `NFMD_PROD_EVENTS_PATH`, falling back to a
  non-user-specific location (`$XDG_STATE_HOME` / `~/.nfmd/...` expanded at
  runtime, or a repo-relative path consistent with C6.1.5's `<repo>/docker/`
  precedent).
- collector sync state ← `NFMD_PROD_EVENTS_SYNC_STATE`, same rule.
- No literal `/Users/<name>` or `/home/<name>` may appear as a default in
  committed code. Documentation examples may show a concrete path.

Remediation is tracked as a child of NFM-2108 and **blocks NFM-2113**, so the
violating default cannot reach `main` through the integration merge.

### C6.3.3 — ADR-KR3-A2 status

A2's core decision (Option A: artifact upload plus collector, rejecting direct
SSH + flock) is **accepted** — it is the same two-stage durability pattern as
C6.1.1, and its rejection rationale for Option B is sound. A2 moves from
`Proposed` to `Accepted` with the C6.3.2 carve-out recorded, so the repository
does not hold a Proposed ADR that already governs merged code.

### C6.3.4 — Process finding

A2 amends a CTO-accepted ADR and reached implementation while `Proposed`.
Amendments to an Accepted ADR require architecture sign-off **before** the
implementing issue starts, not after. This is a routing gap rather than a fault
in the design: NFM-2109 hangs off a different parent than the NFM-2108 subtree,
so the conflict was invisible to both tracks until integration.
