# Latched-error recovery runbook (NFM-3995)

**Audience:** on-call SRE / Board members / CTO Research / Lead Engineer when
the latched-error watchdog
([`scripts/latched_error_watchdog.py`](../../scripts/latched_error_watchdog.py))
emits a `[SRE-LATCHED]` issue, or when an agent is observed in `error` past
its heartbeat grace window.

**Source of truth for the contract:** NFM-3995 acceptance criteria and
[NFM-3993 P1 escalation postmortem](#narrative-behind-this-runbook).

---

## 1. What "latched error" means

A `claude_local` agent is **latched** when **all** of the following hold:

1. `GET /api/companies/{companyId}/agents` reports the agent with
   `status === "error"`.
2. `lastHeartbeatAt` is older than **10 minutes** (the default
   `--threshold-minutes`).
3. The on-disk workspace at
   `~/.paperclip/instances/default/workspaces/{agentId}/` exists but the `.git`
   anchor is missing (the NFM-3935 RCA pattern, classified `transient`)
   **OR** the workspace exists with `.git` present but is still in error
   (classified `terminal`).

If the workspace directory does not exist at all, the watchdog treats it
as `never_provisioned` and **does not alert** — that is the expected
state for inactive `claude_local` agents (only a small handful of
workspaces in `~/.paperclip/instances/default/workspaces/` are git
repos at any given time; the rest are placeholder dirs).

The heartbeat scheduler at `server/src/services/heartbeat.ts:12454` skips
`error`-status agents when `intervalSec <= 0` (the default for
`claude_local`), so once an agent enters `error` it produces **zero
board signal** until something else observes it. This watchdog is the
"something else".

---

## 2. The three recovery paths

Pick the one that matches the classification in the issue body. The
issue body includes a `Classification` line:

* **TRANSIENT — workspace `.git` anchor missing** → path **A** (one-line
  disk fix), then path **B** (clear-error) to confirm.
* **TERMINAL — workspace `.git` present; error not the anchor pattern**
  → skip path A and go straight to path **C** (escalate).

### Path A — One-line disk fix (TRANSIENT only)

If the issue body says `Classification: TRANSIENT`, the `.git` anchor
is missing. Per the NFM-3935 RCA and the
`claude-local-agent-home-missing-git-anchor` memory, the operational
fix is one reversible command:

```bash
cd ~/.paperclip/instances/default/workspaces/<agentId>
git init -q .
```

Reversibility: removing `.git` restores the previous state. There is no
risk to user data — `git init` only creates the gitdir scaffolding.

After running the init, the agent's next scheduled heartbeat will see a
healthy workspace and clear the error automatically. To force-clear
without waiting, run path **B** next.

### Path B — Board clear-error UI path (any classification)

This is the canonical recovery, valid for both TRANSIENT and TERMINAL
cases. Requires Board access (`assertBoard` in
`server/src/routes/agents.ts:3005`).

**Via the Paperclip UI** (preferred — gives the activity log a clean
audit trail):

1. Open the agent's page from the link in the issue body
   (`PAPERCLIP_API_URL/agents/{urlKey}`).
2. Click the **Clear error** action.
3. Confirm; the `agent.error_cleared` activity entry is recorded with
   the Board user as the actor.

**Via the API** (one-liner for scripted recovery):

```bash
curl -X POST \
  -H "Authorization: Bearer $PAPERCLIP_BOARD_KEY" \
  -H "Content-Type: application/json" \
  "$PAPERCLIP_API_URL/api/agents/<agentId>/clear-error"
```

The agent's `status` returns to `idle` and `errorReason` is cleared.
If the org chain is invalid (rare), the endpoint returns 409 with
`repairGuidance` — fix the chain first.

**403 — Board access required**: the agent JWT issued to a `claude_local`
session does NOT have Board permission. Switch to a Board user account
or to the human operator's JWT. The watchdog cannot clear errors
itself — by design (NFM-3993).

### Path C — Escalate to CTO Research / Lead Engineer (TERMINAL)

If the issue body says `Classification: TERMINAL`, the `.git` anchor is
present but the agent is still in error. The root cause is **not** the
NFM-3935 pattern; common alternatives:

* Workspace provisioning failure (the harness failed mid-bootstrap).
* Adapter-level runtime error (e.g. a missing tool the agent requires).
* Corrupted state inside the workspace (large files staged, broken
  lock files, etc.).

Do **not** attempt path A — `git init` would clobber the existing anchor
and obscure the root cause. Instead:

1. Open a `Code Reviewer`-style RCA ticket assigned to **CTO Research**
   (`3a0e0b92-5e86-4cd4-99fb-e84db376d5a2`) with a link to the
   `[SRE-LATCHED]` issue.
2. Include `docker logs --tail 200 nucpot-prod-api | grep <agentId>` and
   any harness stderr captured during the agent's last run.
3. CTO Research decides whether to escalate to Lead Engineer (who
   owns application code changes) for a harness or adapter fix.

---

## 3. False-positive handling

AC-3 requires a false-positive rate ≤ 0 over a 7-day window. Three
mechanisms keep it that way:

* **Heartbeat-grace window** (`--threshold-minutes`, default 10): the
  watchdog does not flag an agent whose heartbeat is fresher than the
  threshold, even if `status === "error"` (which can happen transiently
  during a normal run).
* **Never-provisioned skip**: if the workspace directory is absent, the
  watchdog does not alert — that is the expected state for inactive
  `claude_local` agents and produces the bulk of would-be false
  positives if naively flagged.
* **Dedup fingerprint**: the watchdog parses a sentinel line out of
  each `[SRE-LATCHED]` issue's description
  (`<!-- latched-watchdog: agentId=… fingerprint=… -->`). If a matching
  open issue already exists, the watchdog posts a timestamp comment
  instead of a duplicate issue.

If a `[SRE-LATCHED]` issue fires for a healthy agent:

1. Confirm the agent's `status` and `lastHeartbeatAt` via
   `GET /api/agents/{id}` — was the timestamp stale? Was the agent
   transitioning out of `error`?
2. If the agent is genuinely healthy, mark the issue `done` with a
   comment naming the false-positive signature.
3. Open a `CTO arch-verify`-style review to tune
   `--threshold-minutes` / `--dedup-hours` / workspace probe if the
   pattern recurs.

---

## 4. Operating the watchdog itself

The watchdog is a stdlib-only Python script. No new dependencies.

### One-shot scan

```bash
python3 scripts/latched_error_watchdog.py --verbose
```

Exit codes:

| Code | Meaning                                                  |
|------|----------------------------------------------------------|
| 0    | Clean — no latched `claude_local` errors detected       |
| 1    | Configuration / API failure (check env vars)             |
| 2    | Latched errors detected, no mutations (dry-run only)    |

### Dry-run

Always dry-run first when investigating a new failure mode:

```bash
python3 scripts/latched_error_watchdog.py --dry-run --verbose
```

### Loop mode (rare — most teams should use the scheduled task)

```bash
python3 scripts/latched_error_watchdog.py --loop-seconds 300
```

Loop mode exits only on configuration / API failure (exit 1). On
EXIT_ERROR it stops so the upstream problem is fixed before the next
iteration.

### Required environment

| Variable                  | Required | Notes                                       |
|---------------------------|----------|---------------------------------------------|
| `PAPERCLIP_API_URL`       | yes      | e.g. `http://100.65.135.2:3101`             |
| `PAPERCLIP_API_KEY`       | yes      | Bearer token (agent JWT is sufficient)      |
| `PAPERCLIP_COMPANY_ID`    | yes      | UUID                                        |
| `PAPERCLIP_WORKSPACE_ROOT`| no       | Default: `~/.paperclip/instances/default/workspaces`   |

---

## 5. Scheduling the watchdog

The simplest scheduling path is to wire it into the SRE heartbeat.
Each heartbeat already runs the four-step protocol in the SRE
agent's `HEARTBEAT.md`; append a step 5 that calls the watchdog. That
keeps scheduling inside the same SRE pilot scope as the rest of the
heartbeat and avoids introducing a separate cron.

The fallback is a dedicated Paperclip scheduled task (routine) — call
this when the SRE heartbeat cadence (every 2 hours) is too coarse for
the operational risk. A 5-minute cadence is typical for the
latched-error watchdog because the failure mode is silent for hours
otherwise.

---

## 6. Narrative behind this runbook

NFM-3993 closed a P1 escalation where **Strategy Director**
(`46be9587-18bb-45d5-9512-370a6adbd6eb`) was latched in `error` for
~32 hours (`lastHeartbeatAt = 2026-08-31T01:02:18Z`). The root cause
was transient: the workspace `.git` anchor was missing and the
heartbeat service crashed on `git status`. After CTO/SRE seeded
`.git`, the workspace was fine, but the harness did not auto-recover
the agent's `status` from `error`.

Two agents (CSO + Strategy Director) were dark for 24–32h with **zero
board signal**. CSO self-recovered on its next scheduled heartbeat;
Strategy Director did not. The only signal was CEO-manual-observation
during weekly standup synthesis.

This runbook + the accompanying watchdog closes that gap by:

1. Adding a deterministic scan that emits board-level issues for any
   `claude_local` agent stuck in `error` past the heartbeat-grace
   window.
2. Documenting the operational recovery paths so an on-call operator
   or Board member can act on the alert in minutes, not hours.
3. Differentiating TRANSIENT (`.git` missing — one-line fix) from
   TERMINAL (`.git` present — escalate) via a workspace fingerprint
   probe, so path A does not clobber healthy agents.

References:

* NFM-3995 — this issue's parent
* NFM-3993 — original P1 escalation
* NFM-3935 — `.git` anchor RCA
* Memory: `claude-local-agent-home-missing-git-anchor`,
  `cso-w36-recovery-standup-context`,
  `nfm-2704-strangler-fig-orchestration-failure`
* Paperclip source: `server/src/services/heartbeat.ts:12454` (scheduler
  skip path), `server/src/routes/agents.ts:3005` (clear-error route)
