# Phantom-pass audit cron — runbook (NFM-3831 / ADR-010 Phase 1)

The phantom-pass audit is a **daily 07:00 UTC** cron that scans Paperclip
issues closed in the last 7 days for two failure modes:

* **D1 — Numeric-AC itemized-table check.** A close comment that claims a
  numeric AC ratio (e.g. `AC-2: 6/8 PASS`) must include a markdown
  itemized scoring-card table with at least 8 body rows. Missing or
  undersized table → `[PHANTOM-PASS]` + reopen.
* **D2 — Verifier cross-check.** A close comment that claims
  `verified by <agent>` or `verified via NFM-XXXX` must be backed by an
  attribution comment from the named verifier (em-dash, double-dash, or
  bracket-wrapped role name). Missing attribution →
  `[PHANTOM-VERIFICATION]` + reopen.

This is the **sibling** of the phantom-done-not-on-main rule
(NFM-3166, paperclip-issue-hygiene skill). The two failure modes are
distinct:

| Rule | When `status=done` is wrong | Detector |
|------|------------------------------|----------|
| phantom-done | close comment missing entirely, branch tip not on `origin/main` | `phantom-done-detector.sh` (bash) |
| phantom-pass | close comment present but lacks scoring-card / verifier attribution | `phantom_pass_detector.py` (this cron) |

Reference cases that motivated the rule:

* **NFM-3824 / NFM-3424** — AC-2 claimed `6/8 PASS` with a 2-row
  scoring card and `verified by CTO via NFM-3754` where NFM-3754 had
  only a stale-run cleanup comment from NDE. Both checks fire here.
* **NFM-3396** — canonical 8-row scoring card; the operational baseline
  for the row-count threshold.

## Install

The detector ships in the nucpot repo at
`apps/api/scripts/phantom_pass_detector.py`. The unit tests live at
`apps/api/scripts/tests/test_phantom_pass_detector.py` (37 tests, RED →
GREEN → REFACTOR covered).

The wrapper is at `~/.hermes/scripts/phantom-pass-audit-daily.sh`. The
cron registration is in `~/.hermes/cron/jobs.json`:

```json
{
  "id": "nfm3831-phantom-pass-daily",
  "name": "Phantom-pass audit (NFMD, daily)",
  "script": "phantom-pass-audit-daily.sh",
  "schedule": { "kind": "cron", "expr": "0 7 * * *" },
  "no_agent": true,
  "enabled": true
}
```

No manual install step beyond ensuring `phantom_pass_detector.py` lives
under one of `$HOME/Projects/nucpot`, `$HOME/nucpot`, or the cron
workdir. The wrapper walks these paths on every run.

## Dry-run invocation

```bash
cd ~/Projects/nucpot
python3 -m phantom_pass_detector --help
python3 -m phantom_pass_detector --lookback-days 7    # silent on success
```

A successful scan prints **nothing** to stdout. Findings print one line
per issue:

```text
PHANTOM-PASS: NFM-XXXX | reason=close comment claims AC-2:6/8 but no itemized scoring-card table with >= 8 body rows
PHANTOM-VERIFICATION: NFM-XXXX | verifier=agent:CTO | reason=close comment claims verifier 'CTO' (agent) but no attribution comment found
```

The wrapper (`phantom-pass-audit-daily.sh`) prefixes these with a header
line and a remediation hint, and stays silent when the detector output
is empty.

## Act-on-findings mode

The detector supports `--apply` to post the `[PHANTOM-PASS]` /
`[PHANTOM-VERIFICATION]` marker comment and reopen the issue via the
Paperclip API. The default mode is **dry-run** so the audit pass does
not collide with the LE / RE hand-off workflow (a reopen mid-handoff
can wedge the `in_progress` → `done` state machine).

```bash
PAPERCLIP_BOARD_API_KEY=… python3 -m phantom_pass_detector \
    --apply --lookback-days 7
```

Production rollout should run `--apply` from a separate cron (planned
under NFM-3840 / ADR-010 Phase 2) so the audit and the action remain
decoupled.

## Idempotency

The detector skips issues that:

1. **Already carry a marker.** Any prior comment containing
   `[PHANTOM-PASS]` or `[PHANTOM-VERIFICATION]` excludes the issue from
   future audit passes.
2. **Were reopened.** A `status` of `todo`, `in_progress`, or `blocked`
   excludes the issue from the audit. The audit re-engages only when
   the assignee re-closes the issue.

This means the cron is safe to run more than once per day and safe to
re-run after a false-positive reopen.

## Threshold tuning

The 8-row scoring-card floor (`D1_MIN_SCORING_ROWS` in the detector)
mirrors the NFM-3396 canonical. If a future sprint requires a
different baseline, change the constant in
`apps/api/scripts/phantom_pass_detector.py` and update this runbook in
the same commit.

The 7-day lookback window is the cron default
(`PHANTOM_PASS_LOOKBACK_DAYS` env var or `--lookback-days` flag). A
wider window (14d / 30d) is safe but slows the scan linearly with the
number of `done` issues in the cohort.

## Diagnostics

When the cron reports `FATAL: phantom_pass_detector.py not found`:

1. Confirm the file is at one of the searched paths:
   ```bash
   ls ~/Projects/nucpot/apps/api/scripts/phantom_pass_detector.py
   ```
2. If the repo lives elsewhere, export `PHANTOM_PASS_REPO_ROOT=/path/to/nucpot`
   before running the wrapper.
3. If the cron agent cannot import the module, check that the
   detector's directory is on `sys.path` (the wrapper `cd`s into the
   repo root before calling `python3 -m phantom_pass_detector`, so this
   should be implicit).

When the detector prints `FATAL: psql query failed`:

1. Confirm the DB is reachable: `PGHOST=127.0.0.1 PGPORT=54329 psql -U paperclip -d paperclip -c 'select 1'`
2. Confirm `PGPASSWORD` is set (default `paperclip`, overridable via env).
3. Confirm the `issue_comments` table has the expected columns (the
   detector hard-codes `body`, `author_agent_id`, `created_at`,
   `deleted_at`).

## Change log

* **2026-08-30 (NFM-3831)** — initial Phase 1 cron. D1 numeric-AC +
  D2 verifier cross-check. 7d lookback. 8-row scoring-card floor.
  Sibling of NFM-3166 phantom-done rule.