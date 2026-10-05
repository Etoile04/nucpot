"""Tests for scripts/site_monitor_cadence_check.py (NFM-5309).

Context: GitHub-side scheduled-event starvation (NFM-5226 root cause, W39;
regression confirmed by the NFM-5309 W40 census) delivers only ~8 events/day
to `site-monitor.yml` regardless of the nominal cron frequency — 7.9/day for
the `*/10` monitor entry (5.5% of 144/day nominal) and ~0.36/day for the
`17 */6` watchdog entry (~9% of 4/day nominal). Until this detector existed,
under-firing was only caught by SRE's weekly KR-SRE-5 manual census, i.e.
worst case 7 days of detection latency on the monitoring system itself.

These tests pin the evaluation logic (windowing, thresholds, silence
detection, exit-code mapping) so the standing host-side detector cannot
silently regress. I/O is isolated: `fetch_runs` is monkeypatched everywhere
`main` is exercised, so the suite runs without network or `gh`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import site_monitor_cadence_check as smcc  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def _run(minutes_ago: float, conclusion: str = "success") -> dict:
    """Build a gh-shaped run record at NOW - minutes_ago."""
    stamp = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"createdAt": stamp, "conclusion": conclusion}


def _spread(count: int, over_hours: float, conclusion: str = "success") -> list[dict]:
    """Spread `count` runs evenly across the `over_hours` before NOW."""
    if count == 0:
        return []
    step = over_hours * 60 / count
    return [
        _run(step * (i + 1) - step / 2, conclusion) for i in range(count)
    ]


def test_baseline_cadence_is_ok() -> None:
    """~8 delivered runs over 24h (the census baseline) must read OK."""
    result = smcc.evaluate_cadence(
        _spread(8, 24), now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["verdict"] == "OK"
    assert result["delivered"] == 8


def test_below_warn_threshold_warns() -> None:
    """3 runs/24h (half the baseline) is a WARN, not yet CRITICAL."""
    result = smcc.evaluate_cadence(
        _spread(3, 24), now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["verdict"] == "WARN"
    assert result["delivered"] == 3


def test_zero_runs_in_window_is_critical() -> None:
    """Total silence inside the window pages (CRITICAL), even though older
    runs exist outside it — silence must not be diluted by stale history."""
    runs = [_run(48 * 60 + k * 90) for k in range(4)]  # 48-52.5h ago only
    result = smcc.evaluate_cadence(
        runs, now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["verdict"] == "CRITICAL"
    assert result["delivered"] == 0


def test_silence_trips_critical_even_with_healthy_window_count() -> None:
    """A >max-silence gap since the newest run is CRITICAL even when the
    delivered count alone looks healthy — one long blackout is the
    page-worthy signal the W39 filing demonstrated (5h+ silence)."""
    # Six runs 20-25h ago: count (6) passes warn_below=4 over a 48h window,
    # but the newest run is 20h old > max_silence_hours=18.
    runs = [_run(20 * 60 + k * 60) for k in range(6)]
    result = smcc.evaluate_cadence(
        runs, now=NOW, window_hours=48, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["delivered"] == 6
    assert result["hours_since_last_run"] > 18
    assert result["verdict"] == "CRITICAL"


def test_window_excludes_runs_older_than_window() -> None:
    """Runs older than the window must not inflate the delivered count."""
    # 3 fresh runs (2-22h ago) + 20 stale runs all 30-86h ago.
    runs = _spread(3, 24) + [_run(30 * 60 + k * 180) for k in range(20)]
    result = smcc.evaluate_cadence(
        runs, now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["delivered"] == 3


def test_all_conclusions_count_as_delivery() -> None:
    """A failed run still proves the scheduled event fired — job failures
    are health_check's lane, not a cadence signal. They must count."""
    runs = _spread(4, 24, conclusion="failure") + _spread(4, 24)
    result = smcc.evaluate_cadence(
        runs, now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["delivered"] == 8
    assert result["verdict"] == "OK"


def test_evaluate_cadence_does_not_mutate_input() -> None:
    """The evaluator must stay pure — sorted/copied, never reordering or
    editing the caller's list in place."""
    runs = [_run(60), _run(10), _run(600)]
    snapshot = [dict(r) for r in runs]
    smcc.evaluate_cadence(
        runs, now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert runs == snapshot


def test_empty_run_list_is_critical_not_crash() -> None:
    """No runs fetched at all (fresh workflow, purged history) must map to
    CRITICAL with an unknown-since marker, never raise."""
    result = smcc.evaluate_cadence(
        [], now=NOW, window_hours=24, warn_below=4, crit_below=1,
        max_silence_hours=18,
    )
    assert result["verdict"] == "CRITICAL"
    assert result["hours_since_last_run"] is None


def test_parse_iso_accepts_z_suffix_and_offset() -> None:
    """gh emits `...Z`; fromisoformat on older pythons rejects it — the
    parser must normalise both Z and explicit offsets."""
    parsed = smcc.parse_iso("2026-10-05T12:00:00Z")
    assert parsed == NOW
    parsed_offset = smcc.parse_iso("2026-10-05T14:00:00+02:00")
    assert parsed_offset == NOW


def test_main_exit_codes_and_json(monkeypatch, capsys) -> None:
    """main() maps verdicts to exit codes (0/2/3), and --json emits a
    machine-readable census record for SRE §3 reuse."""
    monkeypatch.setattr(smcc, "fetch_runs", lambda **kw: _spread(3, 24))
    rc_warn = smcc.main(["--json"], now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert rc_warn == 2
    assert out["verdict"] == "WARN" and out["delivered"] == 3

    monkeypatch.setattr(smcc, "fetch_runs", lambda **kw: _spread(9, 24))
    rc_ok = smcc.main(["--json"], now=NOW)
    assert rc_ok == 0

    monkeypatch.setattr(smcc, "fetch_runs", lambda **kw: [])
    rc_crit = smcc.main(["--json"], now=NOW)
    assert rc_crit == 3


def test_main_returns_1_on_gh_failure(monkeypatch) -> None:
    """A `gh` execution failure is an operational error (exit 1), distinct
    from any cadence verdict — the detector must not page on its own I/O
    failure, nor report OK."""

    def boom(**kwargs):
        raise smcc.GhError("gh: command not found")

    monkeypatch.setattr(smcc, "fetch_runs", boom)
    rc = smcc.main(["--json"])
    assert rc == 1


def test_fetch_runs_never_uses_single_date_created_filter(monkeypatch) -> None:
    """Regression pin: a bare `--created YYYY-MM-DD` is an EXACT-day filter
    in gh, not "since". The first implementation used it and fabricated a
    0-run/24h CRITICAL on a day the workflow actually delivered 9 runs.
    The gh command must rely on --limit + in-process windowing instead."""

    captured: dict = {}

    class Proc:
        returncode = 0
        stdout = json.dumps([{"createdAt": "2026-10-05T00:06:06Z",
                              "conclusion": "success"}])
        stderr = ""

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return Proc()

    monkeypatch.setattr(smcc.subprocess, "run", fake_run)
    runs = smcc.fetch_runs(workflow="site-monitor.yml", limit=400)
    assert runs[0]["createdAt"] == "2026-10-05T00:06:06Z"
    assert "--created" not in captured["cmd"], (
        "single-date --created truncates the census to one day; "
        "windowing must happen in evaluate_cadence"
    )
    assert "--limit" in captured["cmd"] and "400" in captured["cmd"]


def test_fetch_runs_raises_gherror_on_failure(monkeypatch) -> None:
    """Non-zero gh exit must raise GhError carrying stderr, never return
    partial/empty output as a fake verdict."""

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "gh: no default remote"

    monkeypatch.setattr(
        smcc.subprocess, "run", lambda *a, **kw: Proc()
    )
    with pytest.raises(smcc.GhError, match="no default remote"):
        smcc.fetch_runs(workflow="site-monitor.yml", limit=10)
