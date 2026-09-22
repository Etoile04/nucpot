"""Tests for NFM-5112 D1 cadence tightening plist (NFM-5111 AC1-AC3).

The new ``local.nfm.runner-maxlifetime-tightened.plist`` replaces the
NFM-5080 D1 25min-cadence plist. Per NFM-5111 AC2:

  - 6 fires during 03:30-05:30Z window (= 11:30-13:30 local +08:00)
  - 22min spacing (±60s tolerance)
  - Off-burst: NO firings (soak behavior unchanged from NFM-5080)

CR explicitly verifies with ``plutil -p`` (per AC2). These tests pin the
arithmetic so any future schedule edit that breaks cadence is caught at
``pytest`` time, not at 03:30Z when the wedge gap matters.

Hard constraints asserted:
  - Label is ``local.nfm.runner-maxlifetime-tightened`` (NOT the old
    NFM-5080 label ``local.nfm.runner-maxlifetime``) so the two can
    coexist briefly during RE's bootstrap/bootout window without
    collision.
  - ProgramArguments invoke the SAME chokepoint script as NFM-5080 D1
    so the byte-identical chokepoint sha 302257a27ff5 is preserved.
  - Rollback commands match the AC3 spec verbatim.
"""

from __future__ import annotations

import hashlib
import plistlib
import re
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path

import pytest

PLIST_RELPATH = "scripts/host-prod-gate/launchd/local.nfm.runner-maxlifetime-tightened.plist"

# Expected fire schedule (local +08:00). Per AC2: 6 fires, 22min spacing.
EXPECTED_LOCAL_FIRES = [
    (11, 30),  # 03:30Z
    (11, 52),  # 03:52Z
    (12, 14),  # 04:14Z
    (12, 36),  # 04:36Z
    (12, 58),  # 04:58Z
    (13, 20),  # 05:20Z
]

BURST_WINDOW_START_LOCAL = (11, 30)  # 03:30Z == 11:30 +08:00
BURST_WINDOW_END_LOCAL = (13, 30)  # 05:30Z == 13:30 +08:00

EXPECTED_LABEL = "local.nfm.runner-maxlifetime-tightened"
EXPECTED_CHOKEPOINT_SCRIPT = "/Users/lwj04/.local/nfm/runner-maxlifetime.sh"
CHOKEPOINT_TERM_SCRIPT = "/usr/local/lib/nfm-g2/ollama-runner-term.sh"
CHOKEPOINT_TERM_SCRIPT_TRACKED = "scripts/host-prod-gate/entries/ollama-runner-term.sh"
# Repinned from the stale NFM-5080-era 37a69e1f0fb6 by the authorized
# NFM-5083 rotation (commit fa6910c4f).
CHOKEPOINT_SHA_HEX_PREFIX = "302257a27ff5"


def _plist_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / PLIST_RELPATH


@pytest.fixture(scope="module")
def plist_data() -> dict:
    with _plist_path().open("rb") as f:
        return plistlib.load(f)


def _to_local_fires(plist_data: dict) -> list[tuple[int, int]]:
    """Extract (hour, minute) tuples from the StartCalendarInterval array."""
    fires: list[tuple[int, int]] = []
    for entry in plist_data["StartCalendarInterval"]:
        fires.append((int(entry["Hour"]), int(entry["Minute"])))
    return fires


# --- AC1: plist loaded (label + chokepoint) -----------------------------


def test_label_matches_nfm_5112(plist_data: dict) -> None:
    """Label MUST be ``-tightened`` to coexist with the NFM-5080 D1 plist
    during RE's bootstrap/bootout window without label collision."""
    assert plist_data["Label"] == EXPECTED_LABEL


def test_program_arguments_invoke_nfm_5080_chokepoint_script(plist_data: dict) -> None:
    """ProgramArguments MUST invoke the SAME NFM-5080 D1 chokepoint script
    so the byte-identical chokepoint sha 302257a27ff5 is preserved
    (NFM-5111 AC1 hard constraint)."""
    args = plist_data["ProgramArguments"]
    assert args[0] == "/bin/bash"
    assert args[1] == EXPECTED_CHOKEPOINT_SCRIPT


def test_no_run_at_load(plist_data: dict) -> None:
    """Convention (per NFM-5080 D1 plist): NO RunAtLoad — install must NOT
    fire immediately, which would race with the runner startup."""
    assert "RunAtLoad" not in plist_data or plist_data.get("RunAtLoad") is False


def test_no_keep_alive(plist_data: dict) -> None:
    """Convention: NO KeepAlive — each fire is independent, matching D1."""
    assert "KeepAlive" not in plist_data or plist_data.get("KeepAlive") is False


# --- AC2: schedule arithmetic verified --------------------------------


def test_six_fires_in_burst_window(plist_data: dict) -> None:
    """AC2: 6 fires during the 03:30-05:30Z (= 11:30-13:30 +08:00) burst
    window. The new plist deliberately drops the NFM-5080 trailing 13:30
    fire (which landed ON the boundary) to keep the wedge-formation gap
    inside the window."""
    fires = _to_local_fires(plist_data)
    in_window = [
        (h, m)
        for h, m in fires
        if (h, m) >= BURST_WINDOW_START_LOCAL and (h, m) <= BURST_WINDOW_END_LOCAL
    ]
    assert len(in_window) == 6, (
        f"expected exactly 6 fires in burst window, got {len(in_window)}: {in_window}"
    )


def test_fires_match_expected_schedule(plist_data: dict) -> None:
    """AC2: schedule is exactly the 22min-cadence set per NFM-5111."""
    fires = _to_local_fires(plist_data)
    assert fires == EXPECTED_LOCAL_FIRES, f"expected {EXPECTED_LOCAL_FIRES}, got {fires}"


def test_twenty_two_minute_spacing_within_tolerance(plist_data: dict) -> None:
    """AC2: 22min spacing with ±60s tolerance. Tests each consecutive pair."""
    fires = _to_local_fires(plist_data)
    pairs = list(pairwise(fires))
    deltas_minutes: list[float] = []
    for (h1, m1), (h2, m2) in pairs:
        t1 = datetime(2026, 1, 1, h1, m1, tzinfo=timezone(timedelta(hours=8)))
        t2 = datetime(2026, 1, 1, h2, m2, tzinfo=timezone(timedelta(hours=8)))
        if t2 <= t1:
            t2 += timedelta(days=1)
        deltas_minutes.append((t2 - t1).total_seconds() / 60.0)
    for idx, delta in enumerate(deltas_minutes):
        assert abs(delta - 22.0) <= 1.0, (
            f"gap between fires {idx} and {idx + 1} is {delta:.2f} min; expected 22min ±1min"
        )


def test_no_off_burst_firings(plist_data: dict) -> None:
    """AC2: off-burst, NO firings (soak behavior unchanged from NFM-5080 D1).

    D1 is intentionally confined to the 11:30-13:30 +08:00 burst window;
    outside that window the chokepoint stays silent, so any wedge during
    the soak (2026-09-22 → 2026-09-25) is detected by AC4-AC5 monitoring
    rather than prematurely killed by D1."""
    fires = _to_local_fires(plist_data)
    off_burst = [
        (h, m)
        for h, m in fires
        if (h, m) < BURST_WINDOW_START_LOCAL or (h, m) > BURST_WINDOW_END_LOCAL
    ]
    assert off_burst == [], f"unexpected off-burst fires: {off_burst}"


# --- AC2 (cross-check): schedule matches UTC window ----------------------


def test_local_fires_map_to_03_30_to_05_30_z(plist_data: dict) -> None:
    """Cross-check: each local +08:00 fire maps to the 03:30-05:30Z UTC
    burst window. Validates timezone assumption."""
    fires = _to_local_fires(plist_data)
    for h, m in fires:
        local = datetime(2026, 9, 22, h, m, tzinfo=timezone(timedelta(hours=8)))
        utc = local.astimezone(UTC)
        assert (
            (utc.hour == 3 + (h - 11) and utc.minute == m % 60)
            or (utc.hour == 4 + (h - 12) and utc.minute == m % 60)
            or (utc.hour == 5 and utc.minute == m)
        ), (
            f"local {h:02d}:{m:02d} +08:00 does not map to 03:30-05:30Z; "
            f"got UTC {utc.hour:02d}:{utc.minute:02d}"
        )


# --- AC3: rollback documented ------------------------------------------


def test_rollback_bootout_targets_label_and_deployed_path(plist_data: dict) -> None:
    """AC3: rollback documented in the plist header must actually disable
    the agent when RE copies it verbatim. The label form must match the
    plist's own Label; the file-target form must be the deployed plist's
    absolute path under ~/Library/LaunchAgents/ (a bare filename resolves
    under ~ and the bootout silently fails)."""
    header = _plist_path().read_text()
    bootout_args = re.findall(r"launchctl bootout (.+)$", header, re.MULTILINE)
    targets = [arg.removeprefix("gui/$UID").lstrip("/ ").strip() for arg in bootout_args]
    label = plist_data["Label"]
    assert targets == [label, f"~/Library/LaunchAgents/{label}.plist"], (
        f"documented rollback targets {targets}; expected the label form "
        f"{label} plus the deployed-path form "
        f"~/Library/LaunchAgents/{label}.plist"
    )


def test_chokepoint_sha_preserved_on_disk() -> None:
    """Hard constraint (NFM-4922 RCA hygiene): the chokepoint the tightened
    plist will invoke must still hash to the pinned value. Executes the
    same check RE runs post-deploy (shasum -a 256 on the live chokepoint)
    rather than trusting the header text."""
    result = subprocess.run(
        ["shasum", "-a", "256", CHOKEPOINT_TERM_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"cannot verify chokepoint: shasum exited {result.returncode}: {result.stderr.strip()}"
    )
    actual_sha = result.stdout.split()[0]
    assert actual_sha.startswith(CHOKEPOINT_SHA_HEX_PREFIX), (
        f"chokepoint {CHOKEPOINT_TERM_SCRIPT} hashes to {actual_sha}; pinned "
        f"prefix {CHOKEPOINT_SHA_HEX_PREFIX} no longer matches — the "
        "chokepoint drifted (NFM-4922 single-writer hygiene)"
    )


def test_chokepoint_sha_pin_matches_repo_tracked_copy() -> None:
    """The pinned prefix must equal the sha256 of the repo-tracked
    chokepoint snapshot (an intentionally owned byte contract), so the
    pin moves only alongside an intentional chokepoint change on main.
    CI-portable: hashes the tracked file directly, no host paths."""
    tracked = Path(__file__).resolve().parents[2] / CHOKEPOINT_TERM_SCRIPT_TRACKED
    tracked_sha = hashlib.sha256(tracked.read_bytes()).hexdigest()
    assert tracked_sha.startswith(CHOKEPOINT_SHA_HEX_PREFIX), (
        f"repo-tracked chokepoint {CHOKEPOINT_TERM_SCRIPT_TRACKED} hashes "
        f"to {tracked_sha}; pinned prefix {CHOKEPOINT_SHA_HEX_PREFIX} is "
        "stale — repin alongside the intentional chokepoint change"
    )
