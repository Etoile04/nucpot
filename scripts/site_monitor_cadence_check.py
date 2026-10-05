"""Site-monitor scheduled-cadence watchdog (NFM-5309).

GitHub-side scheduled-event starvation (root-caused in NFM-5226, W39;
regression-confirmed by the NFM-5309 W40 census) delivers only ~8 events/day
to `site-monitor.yml` regardless of the nominal cron frequency:

    entry        nominal      delivered (census 09-28..10-05)
    monitor      */10  =144/d  ~7.9/day  (5.5%)
    watchdog     17 */6 = 4/d  ~0.36/day (~9%)

All delivered runs succeed — events are dropped upstream of run creation, so
nothing inside GitHub Actions can detect the under-firing. Until this script
existed, the only detector was SRE's weekly KR-SRE-5 manual census (up to 7
days of detection latency on the monitoring system itself).

This script is the standing detector. It counts delivered workflow runs in a
trailing window (via `gh run list`) and exits non-zero when cadence falls
below census-derived floors, so under-firing itself pages. It is designed to
run OFF GitHub Actions (host cron / launchd — see
scripts/cron/site-monitor-cadence.cron), because a starved scheduler cannot
be its own watchdog.

Alarm semantics (defaults derived from the W40 census baseline of ~8/day):
    WARN      delivered < --warn-below over the window (default 4/24h ≈ half
              the observed baseline)
    CRITICAL  delivered < --crit-below (default 1 → total silence) OR the
              newest run is older than --max-silence-hours (default 18h)

Job *conclusions* are irrelevant here: a failed run still proves the event
fired (failures are health_check's lane, NFM-5270). Only delivery counts.

Exit codes
----------
0 — cadence OK
1 — execution/config error (gh missing, bad JSON) — never pages as cadence
2 — WARN
3 — CRITICAL

Usage
-----
    python3 scripts/site_monitor_cadence_check.py            # human summary
    python3 scripts/site_monitor_cadence_check.py --json     # SRE §3 census
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_WARN = 2
EXIT_CRITICAL = 3

VERDICT_EXIT_CODES = {"OK": EXIT_OK, "WARN": EXIT_WARN, "CRITICAL": EXIT_CRITICAL}


class GhError(RuntimeError):
    """Raised when the `gh` census query itself fails (not a cadence verdict)."""


def parse_iso(stamp: str) -> datetime:
    """Parse an gh `createdAt` stamp into an aware UTC datetime.

    `gh --json createdAt` emits ISO-8601 with a literal ``Z`` suffix, which
    ``datetime.fromisoformat`` rejects on Python < 3.11. Normalise both the
    ``Z`` form and explicit offsets; treat naive stamps as UTC.
    """
    normalized = stamp.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def evaluate_cadence(
    runs: list[dict],
    *,
    now: datetime,
    window_hours: float,
    warn_below: int,
    crit_below: int,
    max_silence_hours: float,
) -> dict:
    """Evaluate delivered cadence from gh-shaped run records. Pure function.

    ``runs`` is a list of ``{"createdAt": "...", "conclusion": "..."}``
    records (extra keys ignored). Returns a NEW result dict; the input list
    is never mutated. Windowing counts runs with ``window_start <= t <=
    now``; silence is measured from the newest run *outside* the windowing
    too, so a long blackout is not diluted by stale history.
    """
    stamps = [parse_iso(r["createdAt"]) for r in runs]
    window_start = now - timedelta(hours=window_hours)
    delivered = sum(1 for t in stamps if window_start <= t <= now)
    last_at = max(stamps, default=None)
    hours_since_last_run = (
        (now - last_at).total_seconds() / 3600.0 if last_at is not None else None
    )

    silent = hours_since_last_run is None or hours_since_last_run > max_silence_hours
    if delivered < crit_below or silent:
        verdict = "CRITICAL"
    elif delivered < warn_below:
        verdict = "WARN"
    else:
        verdict = "OK"

    return {
        "verdict": verdict,
        "delivered": delivered,
        "window_hours": window_hours,
        "warn_below": warn_below,
        "crit_below": crit_below,
        "max_silence_hours": max_silence_hours,
        "last_run_at": last_at.astimezone(timezone.utc).isoformat()
        if last_at is not None
        else None,
        "hours_since_last_run": hours_since_last_run,
        "checked_at": now.astimezone(timezone.utc).isoformat(),
    }


def fetch_runs(*, workflow: str, limit: int) -> list[dict]:
    """Query delivered workflow runs via `gh run list`.

    Deliberately does NOT use ``--created``: a single-date value is an
    exact-day filter (not "since"), which silently truncates the fetch to
    one day and fabricates silence. Windowing belongs to
    ``evaluate_cadence``; ``limit`` must simply exceed the runs that can
    accumulate over the evaluation window (default 400 ≈ 14 days at the
    honest 28/day schedule, ≈ 2.8 days even at full legacy cadence).
    """
    cmd = [
        "gh", "run", "list",
        "--workflow", workflow,
        "--json", "createdAt,conclusion",
        "--limit", str(limit),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(
            f"gh run list failed (rc={proc.returncode}): {proc.stderr.strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GhError(f"gh emitted non-JSON output: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect site-monitor scheduled-event starvation (NFM-5309)."
    )
    parser.add_argument("--workflow", default="site-monitor.yml",
                        help="workflow file name (default: site-monitor.yml)")
    parser.add_argument("--window-hours", type=float, default=24.0,
                        help="trailing window for the delivered count (default 24)")
    parser.add_argument("--warn-below", type=int, default=4,
                        help="WARN when delivered runs < N over the window (default 4)")
    parser.add_argument("--crit-below", type=int, default=1,
                        help="CRITICAL when delivered runs < N (default 1 = silence)")
    parser.add_argument("--max-silence-hours", type=float, default=18.0,
                        help="CRITICAL when the newest run is older than this (default 18)")
    parser.add_argument("--limit", type=int, default=400,
                        help="max runs fetched from gh (default 400)")
    parser.add_argument("--json", action="store_true",
                        help="emit one machine-readable census record")
    return parser


def _print_human(result: dict) -> None:
    if result["hours_since_last_run"] is not None:
        print(
            f"site-monitor cadence: {result['verdict']} — {result['delivered']} runs / "
            f"{result['window_hours']:g}h (warn<{result['warn_below']}, "
            f"crit<{result['crit_below']}); last run "
            f"{result['hours_since_last_run']:.1f}h ago"
        )
    else:
        print(
            f"site-monitor cadence: {result['verdict']} — {result['delivered']} runs / "
            f"{result['window_hours']:g}h; no runs on record"
        )
    if result["verdict"] != "OK":
        print(
            "  → scheduled-event starvation / monitor blackout: see "
            "docs/runbooks/site-monitoring.md (NFM-5309)",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = now or datetime.now(timezone.utc)

    try:
        runs = fetch_runs(workflow=args.workflow, limit=args.limit)
    except GhError as exc:
        if args.json:
            print(json.dumps({"verdict": "ERROR", "error": str(exc)}))
        else:
            print(f"cadence-check: execution error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    result = {
        **evaluate_cadence(
            runs,
            now=now,
            window_hours=args.window_hours,
            warn_below=args.warn_below,
            crit_below=args.crit_below,
            max_silence_hours=args.max_silence_hours,
        ),
        "workflow": args.workflow,
    }
    if args.json:
        print(json.dumps(result))
    else:
        _print_human(result)
    return VERDICT_EXIT_CODES[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
