# Production Deploy-Event Capture Runbook (KR-3)

> **Scope:** the two production deploy-event capture lanes on the prod host
> (`wenjiedeMac-Studio` — this host IS prod) plus the repair/backfill
> tooling introduced by the NFM-5932 tree. Ops note per NFM-5938 (P6).
> Design source of truth:
> [ADR-KR3-prod-emission](../architecture/ADR-KR3-prod-emission.md)
> (ADR-KR3-A2) and its 2026-11-11 addendum. Anything in this runbook that
> drifts from the workflows/scripts is a bug — fix the runbook, not the
> operator.

**Audience:** on-call operators and agents handling KR-3 prod deploy-event
capture health, alert triage, and repair/backfill execution.

---

## 1. The single-source rule (read first)

- `~/.nfmd/master-deploy-events.jsonl` is the **single KR-3 prod series
  source**. The reporter is frozen: `scripts/okr/coverage_kr3.py` resolves
  the prod series as `--prod-path` arg → `$NFMD_PROD_EVENTS_PATH` →
  `~/.nfmd/master-deploy-events.jsonl` (default). **Never repoint the
  reporter at the legacy lane.**
- Master writes are allowed ONLY via (a) the collector's SSH+flock append
  path or (b) a reviewed, repo-committed one-shot script — always with a
  timestamped pre-write backup. No ad-hoc host edits, ever.
- The legacy lane (`~/.nfmd/deploy-events.jsonl`, NFM-2111) is a
  **non-authoritative continuity + per-attempt capture** copy (kept by the
  2026-11-11 ADR addendum, with conditions). It is never a KR-3 source.

## 2. Host files

| Path | What | Owner lane |
|---|---|---|
| `~/.nfmd/master-deploy-events.jsonl` | KR-3 prod series (§3.1 events, one line per deploy run) | master lane |
| `~/.nfmd/master-deploy-events.jsonl.lock` | flock target for all master writes | both lanes / one-shot |
| `~/.nfmd/prod-event-sync-state.json` | master-lane sync state (`last_synced_run_id`, `last_synced_at`, `bad_run_ids`; pending-failure sidecar post-#1537) | master lane |
| `~/.nfmd/deploy-events.jsonl` | legacy-lane series (NFM-2111) | legacy lane |
| `~/.nfmd/deploy-events.jsonl.processed` | legacy processed-run ledger — TSV `run_id \t sha256 \t status \t [coverage token]`, per-attempt tokens (NFM-5829) | legacy lane |
| `~/.nfmd/backups/<timestamped>/` | pre-write backups (one dir per write operation) | repair ops |

Two recon gotchas (both explained under NFM-5935, 2026-11-10):

- `prod-event-sync-state.json` mtime can move with **byte-identical
  content** — the collector saves state unconditionally each tick outside
  the flock. A same-sha rewrite is benign; repair scripts re-verify shas
  fresh at write time (`--expect-*-sha`) and abort on any real drift.
- Legacy-lane file mtime ≠ event time: a late pickup appends the event
  with the deploy-time `ts` (delivery lag up to ~5 h; see §4), so an
  mtime-vs-ts mismatch on `deploy-events.jsonl` is expected, not corruption.

## 3. Lane A — KR-3 master lane (ADR-KR3-A2)

Workflows: `prod-deploy-event-collector-orchestrator.yml` (hosted;
`workflow_run` off Production Deployment + `*/15` cron + manual dispatch;
stages a manifest-verified payload artifact; repository-dispatches) →
`prod-deploy-event-collector-executor.yml` (self-hosted, checkout-free;
`permissions: actions: read, contents: read`).

Script: `scripts/okr/prod_event_collector.py` — downloads
`prod-deploy-event-<run_id>` fragments, validates each (JSON parse, §3.1
schema fields, `environment == "production"`, UUIDv4 `event_id`, exactly
one line), appends to the master under `flock($MASTER.lock)` with a tail-1
sanity check, then advances sync-state. **State advances only after a
confirmed append** — a killed SSH hop leaves the lock released and the
state unadvanced, so the next tick retries the same fragment.

Behavior after the NFM-5934 repair (PR #1537 — CR PASS; live once the
NFM-5932 merge batch lands):

- lists only `status == "completed"` deploy runs (an in-flight run can no
  longer be false-marked for not-yet-uploaded artifacts);
- the remote critical section is a python3 `fcntl.flock` script — macOS
  ships no `flock(1)`, which made every pre-repair append fail rc=99;
- the deploy emit step carries an emit-once guard, killing the two-line
  fragment defect (early call + EXIT trap both fired pre-repair);
- never-synced state bootstraps a 7-day lookback (pre-repair it fell back
  to `now−5min`, which made any backlog structurally unreachable);
- a per-run consecutive-failure sidecar alerts and exits non-zero after
  >3 consecutive failures on a run (stall tripwire); the run stays pending
  — no data loss, never silent;
- `--dry-run` is validate-only (side-effect gate inside `collect()`;
  pre-repair it could append for real).

## 4. Lane B — legacy NFM-2111 lane (kept, non-authoritative)

Workflows: `collect-prod-deploy-events-orchestrator.yml` (hosted, `*/5`
cron + manual dispatch) → `collect-prod-deploy-events-executor.yml`
(self-hosted, checkout-free, `actions: read` + `contents: read`; token
`secrets.GH_TOKEN || secrets.GITHUB_TOKEN`). The PAT preference is why this
lane was immune to the Era-A default-token permissions gap that killed the
master lane (see the ADR addendum).

Script: `scripts/lib/collect_prod_events.py` — downloads
`nfm-deploy-event-<run_id>-<attempt>` artifacts, validates §3.1, appends
atomically to `$NFMD_DEPLOY_EVENTS_PATH` (host:
`~/.nfmd/deploy-events.jsonl`) under python `fcntl.flock` with
`sha256(event_json_text)` idempotency, and records coverage in
`$NFMD_DEPLOY_EVENTS_PROCESSED_PATH` (the `.processed` TSV). Per-attempt
dedup (NFM-5829): a re-run's attempt-2 artifact is collected even when the
attempt-1 row is already ledgered — the master lane has no equivalent, so
attempt-2 events are visible only here.

Cadence reality: the `*/5` schedule delivers in practice every ~3–4.5 h
(documented cron-drop history). Late, not lossy, on its own lane.

## 5. Alerting path

Full transport detail:
[site-monitoring.md §10.1](./site-monitoring.md) (NFM-5936; that section
lands on `main` with the NFM-5932 merge batch). Summary:

- Two transports, picked at fire time: the Feishu webhook via the
  `ALERT_WEBHOOK` repo secret (unset as of 2026-11-11 — the URL is
  human-held; set it via repo Settings / `gh secret set`, never paste it
  anywhere), else the **dedup-keyed gh-issue fallback**: one open
  `[collector-alert] <class>` issue per alert class; repeat fires comment
  into it; closing it resets the dedup key.
- Self-test door: executor `workflow_dispatch` with `orchestrator_run_id`
  fires one `self-test` alert through the real executor context with no
  host-state writes. Proof pattern: issue created, second dispatch dedup'd
  into it, issue closed after evidence capture.
- Alerting is non-fatal by construction; the stalled non-zero executor
  exit remains the load-bearing tripwire surface until P5 (NFM-5937) adds
  the master-age tripwire.

## 6. Backfill / repair — the reviewed one-shot pattern

Tooling of record: `scripts/okr/backfill_prod_events_nfm5935.py`
(repo-committed, CR-reviewed; imports the collector's own
`validate_fragment`/`SyncState` so semantics cannot drift). Future repairs
follow the same shape — never an ad-hoc host script.

Operation contract (NFM-5932 hard constraints):

1. timestamped `cp -p` backup of **every** ledger file + sha256 posted to
   the governing issue BEFORE any write;
2. `--dry-run` first — validate-only, prints the plan, zero writes;
3. execute under the collector's own `$MASTER.lock` flock with
   `--expect-*-sha` guards re-verified fresh at write time;
4. post apply-summary evidence (line counts, uniq ids, ts monotonicity,
   dedup overlap) to the governing issue before closing anything.

Execution of record (NFM-5935, 2026-11-11T05:11→05:29:40Z, exit 0):
master 48 → 122 events (Era-A bytes preserved + 74 legacy-lane events
2026-09-25→2026-11-08; zero duplicate ids; ts non-decreasing), sync-state →
`last_synced_run_id=42957760934`, `bad_run_ids` 177 → 255 (honest,
machine-reasoned marks). Known residual: **2026-09-08 → 2026-09-25 holds
161 runs unrecoverable as §3.1 events** (123 corrupt two-line fragments +
38 no-artifact); ~123 are salvageable later via a first-line salvage pass,
and the surviving artifacts expire ≈2026-12-06 — the board flag stands.

## 7. Host-level change log & rollback notes (NFM-5932 P2–P5)

| Change | When / status | Rollback |
|---|---|---|
| P3 one-shot backfill wrote master + sync-state (this host) | executed 2026-11-11T05:11→05:29:40Z | restore the 2026-11-11T051019 pre-apply backup dir (byte-verified shas). Older rollback points: `nfm5935-20261110T083630Z` (pre-dry-run), `nfm5935-apply-20261111T041333Z` (aborted apply attempt) |
| P4 alerting fallback + self-test door | repo-only (PR #1538, CR PASS); self-test issue #1539 created + dedup-proven + closed | revert the PR — pre-P4 behavior returns (alerts silently no-op while the secret is unset). No host deltas by construction; the alert path never writes host state |
| P1 instrumentation / P2 collector repair | repo-only until the NFM-5932 merge batch lands (#1534/#1537, CR PASS) | revert the PRs — pre-P2 behavior returns. No host deltas |
| P5 stall tripwire + verification deploy + reporter proof | pending — gated on the NFM-5898 deploy-path resolution (board card `2bc50484`); no deploy attempts while pending | its rollback note lands with P5's own evidence when executed |

Rollback for this runbook itself: revert the carrying PR (docs-only).

## 8. Operator quick checks

- **Lane A health:** `last_synced_run_id` / `last_synced_at` in
  `prod-event-sync-state.json` advance after prod deploys; master line
  count grows; the pending-failure sidecar (post-#1537) stays empty or
  small; `[collector-alert]` issues get triaged.
- **Lane B health:** last `.processed` row tracks the newest deploy run;
  `deploy-events.jsonl` tail `ts` matches recent successful deploys
  (mtime may lag hours — see §2).
- **KR-3 read:** `python -m scripts.okr.coverage_kr3` — honors the frozen
  reporter chain (`--prod-path` → `$NFMD_PROD_EVENTS_PATH` → master default,
  §1). Do not use `python -m scripts.okr.report` here: it resolves KR-3 via
  `$NFMD_DEPLOY_EVENTS_PATH` → repo default and never reads master. On the
  restored series: KR-3 = 0.4098 (2026-11-11); formal verification is P5.
- **Never:** repoint the reporter at the legacy lane; hand-edit the master
  outside the reviewed paths (§1); paste the `ALERT_WEBHOOK` URL into
  issues, logs, or tickets (repo secret only).
