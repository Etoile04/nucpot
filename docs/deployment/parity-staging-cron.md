# Nightly V2 Parity Cron — Deployment Runbook

This runbook covers the **staging-host cron** that runs the V2 extraction
parity baseline harness (`tests/parity/test_parity.py`) every night against
the staging checkout. It is the option-(a) implementation of the
[CPO scope decision in NFM-2946](/NFM/issues/NFM-2946).

## What it does

`scripts/run-parity-cron.sh` (dispatched by `scripts/cron/parity-staging-cron.d/nucpot-parity`):

1. **Refreshes** the staging checkout to `origin/main` (only when on a
   detached HEAD with a clean working tree — refuses to clobber a named
   branch or local changes).
2. **Runs** the deterministic V2 parity harness over the curated reference
   baselines under `tests/fixtures/parity/baseline/`. The harness is
   unit-only — no LLM keys, no DB, no network — so it is safe to run on
   any host that has the repo and Python deps.
3. **Archives** per-fixture results to
   `$PARITY_ARTIFACTS/YYYY-MM-DD/{pytest-output.log,summary.json}`.
4. **Rotates** artifact directories older than `$PARITY_RETENTION` days
   (default 30).

The 7-day staging-green streak tracked in
[NFM-2924](/NFM/issues/NFM-2924) consumes `$PARITY_ARTIFACTS`, **not**
local re-runs — that is the whole point of this cron.

## Schedule

| Field   | Value           |
| ------- | --------------- |
| Cron    | `0 2 * * *`     |
| Time    | 02:00 UTC daily |
| User    | `root` (Linux) / invoking user (macOS per-user crontab) |
| Runtime | < 5 s typical   |

## Install

### Linux (`/etc/cron.d/`)

```bash
# From a repo checkout on the staging host (after this PR is merged):
sudo install -o root -g root -m 0644 \
    scripts/cron/parity-staging-cron.d/nucpot-parity \
    /etc/cron.d/nucpot-parity
sudo systemctl restart cron
sudo mkdir -p /var/log/nucpot/parity
sudo chown root:root /var/log/nucpot/parity
```

### macOS (per-user `crontab`)

```bash
# macOS does not honour /etc/cron.d/, but crontab(1) accepts a file:
crontab scripts/cron/parity-staging-cron.d/nucpot-parity
# Confirm:
crontab -l

# Create the artifact dir (your user must own it):
mkdir -p /var/log/nucpot/parity
```

> **macOS launchd alternative:** for tighter control over environment
> variables, write a LaunchAgent plist at
> `~/Library/LaunchAgents/com.nucpot.parity.plist` that wraps the same
> script. Not bundled here; ops can convert.

## Verify

After installing, force a run and confirm the artifact appears:

```bash
# Linux:
sudo run-parts --test /etc/cron.daily
# (or just wait for 02:00 UTC, then):
ls -la /var/log/nucpot/parity/

# Force a manual run (any host):
/Users/lwj04/Projects/nucpot/scripts/run-parity-cron.sh --dry-run
/Users/lwj04/Projects/nucpot/scripts/run-parity-cron.sh

# Inspect today's summary:
cat /var/log/nucpot/parity/$(date -u +%Y-%m-%d)/summary.json
```

A healthy run writes a `summary.json` with this shape (synthetic values,
not real production data):

```json
{
  "timestamp": "2026-08-12T18:14:02Z",
  "repo_sha": "b1026e03f450d39b3737d065e29aba1869ef0756",
  "total_fixtures": 4,
  "passed": 4,
  "failed": 0,
  "status": "PASS",
  "passed_fixtures": [
    "test_v2_output_matches_baseline[mox-thermal-conductivity]",
    "test_v2_output_matches_baseline[thoria-mixed-oxide]",
    "test_v2_output_matches_baseline[uo2-fcc-lattice]",
    "test_v2_output_matches_baseline[zircaloy-cladding-modulus]"
  ],
  "failed_fixtures": []
}
```

## Operational notes

- **Detached HEAD assumption.** The refresh step assumes the staging repo
  is on detached HEAD (which is how `staging_deploy.sh` leaves it). If
  someone hand-checks-out a feature branch on the staging host, the cron
  will refuse to advance and log a warning — run with `--skip-update` or
  detach HEAD to restore auto-refresh.
- **Environment overrides.** All paths and the retention window are
  configurable via `PARITY_*` env vars. The default `PARITY_PYTHON`
  points at `$REPO_DIR/apps/api/.venv/bin/python` — that venv is the
  one maintained by `uv sync --extra dev` and has every dep the parity
  harness needs (SQLAlchemy, pydantic, etc., via the import chain
  through `nfm_db.services`).
- **Failure mode.** If pytest exits non-zero, the script exits with that
  code, so cron(8) will mail the failure to the local mail spool (or
  silently on systems without local MTA — wire up a delivery channel if
  you need alerts). The artifact directory is still written, so the
  failure is recoverable for forensics.
- **Add fixtures safely.** New fixtures land under
  `tests/fixtures/parity/baseline/<name>/`; the cron will pick them up
  on the next run after they're merged to `main`. No cron changes are
  needed for fixture additions.

## References

- [NFM-2891](/NFM/issues/NFM-2891) — parity test + reference baseline (deterministic V2 check)
- [NFM-2922](/NFM/issues/NFM-2922) — parity harness shipped (54 fixtures, ADR-0007)
- [NFM-2923](/NFM/issues/NFM-2923) — PR-triggered observational CI (parallel to this cron, PR-only)
- [NFM-2924](/NFM/issues/NFM-2924) — 7-day staging green streak (this cron is its data source)
- [NFM-2946](/NFM/issues/NFM-2946) — this cron (option (a) implementation)
- ADR-0007 §3 — observational CI semantics
- ADR-0007 §4 — 7-day staging gate