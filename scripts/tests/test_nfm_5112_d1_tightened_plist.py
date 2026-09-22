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
    NFM-5080 label ``local.nfm.runner-maxlifetime``) so launchd can
    manage each agent's load state independently. Deploy RETIRES the
    old agent first (bootout + disable its plist) per CR R2 F1: the
    union cadence has a 3-min minimum gap (11:52 tightened -> 11:55
    old) and the wrapper kills unconditionally per fire, so
    coexistence prematurely kills runners.
  - ProgramArguments invoke the SAME chokepoint script as NFM-5080 D1
    so the byte-identical chokepoint sha 302257a27ff5 is preserved.
  - Rollback commands match the AC3 spec verbatim and additionally
    restore the retired NFM-5080 agent (amended AC3, CR R2 F1).
"""

from __future__ import annotations

import hashlib
import plistlib
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

WRAPPER_SCRIPT = "/Users/lwj04/.local/nfm/runner-maxlifetime.sh"
# Amended AC3 pin #2 (CPO NFM-5121, confirmed by CR R2): the wrapper the
# tightened plist invokes must also be byte-identical post-deploy.
WRAPPER_SHA_HEX_PREFIX = "888cd361cd1faaef"
# The retired NFM-5080 D1 agent (25min cadence) — deploy must boot it out
# and disable its plist before bootstrapping the tightened agent (CR R2 F1).
OLD_NFM_5080_LABEL = "local.nfm.runner-maxlifetime"
DISABLED_AGENTS_DIR = "~/Library/LaunchAgents.disabled"


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


def _header_text() -> str:
    """The plist's embedded runbook: everything before the <plist element."""
    return _plist_path().read_text().split("<plist", 1)[0]


def _section(header: str, heading: str) -> str:
    """One runbook section: the heading line containing ``heading`` plus
    the following non-blank lines (sections are blank-line separated)."""
    lines = header.splitlines()
    for idx, line in enumerate(lines):
        if heading in line:
            block = [line]
            for nxt in lines[idx + 1 :]:
                if nxt.strip() == "":
                    break
                block.append(nxt)
            return "\n".join(block)
    raise AssertionError(f"runbook section {heading!r} not found in plist header")


_RUNBOOK_VERBS = ("launchctl", "mv", "mkdir", "plutil", "grep", "shasum")


def _commands(section: str) -> list[str]:
    """Executable lines of a runbook section: blank/comment/heading lines
    dropped, backslash continuations joined."""
    commands: list[str] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if commands and commands[-1].endswith("\\"):
            commands[-1] = commands[-1].rstrip("\\").rstrip() + " " + line
            continue
        if line.startswith(_RUNBOOK_VERBS):
            commands.append(line)
    return commands


def _expected_deploy_commands() -> list[str]:
    old_plist = f"~/Library/LaunchAgents/{OLD_NFM_5080_LABEL}.plist"
    new_plist = f"~/Library/LaunchAgents/{EXPECTED_LABEL}.plist"
    return [
        f"launchctl bootout gui/$UID/{OLD_NFM_5080_LABEL}",
        f"mkdir -p {DISABLED_AGENTS_DIR}",
        f"mv {old_plist} {DISABLED_AGENTS_DIR}/",
        f"launchctl bootstrap gui/$UID {new_plist}",
    ]


def _expected_rollback_commands() -> list[str]:
    old_plist = f"~/Library/LaunchAgents/{OLD_NFM_5080_LABEL}.plist"
    new_plist = f"~/Library/LaunchAgents/{EXPECTED_LABEL}.plist"
    return [
        f"launchctl bootout gui/$UID/{EXPECTED_LABEL}",
        f"launchctl bootout gui/$UID {new_plist}",
        f"mv {DISABLED_AGENTS_DIR}/{OLD_NFM_5080_LABEL}.plist ~/Library/LaunchAgents/",
        f"launchctl bootstrap gui/$UID {old_plist}",
    ]


# --- AC1: plist loaded (label + chokepoint) -----------------------------


def test_label_matches_nfm_5112(plist_data: dict) -> None:
    """Label MUST be ``-tightened`` (its own launchd identity, distinct
    from NFM-5080's) so Deploy/Rollback can boot each agent in or out
    independently; Deploy retires the old agent first (CR R2 F1)."""
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


# --- AC3 (amended) + CR R2 F1: deploy/rollback runbook -------------------


def test_deploy_retires_old_agent_before_bootstrap() -> None:
    """CR R2 F1 (HIGH): deploy is NOT bootstrap-only. The old NFM-5080
    25min agent must be booted out and its plist moved out of
    ~/Library/LaunchAgents (so the next login does not reload BOTH
    agents) BEFORE the tightened agent is bootstrapped. Union cadence
    min gap is 3 min (11:52 tightened -> 11:55 old) and the wrapper
    kills unconditionally per fire — coexistence prematurely kills
    runners and corrupts the AC4 soak from night one."""
    section = _section(_header_text(), "Deploy (")
    assert _commands(section) == _expected_deploy_commands(), (
        "deploy runbook must retire the old agent (bootout, then disable "
        "its plist) before bootstrapping the tightened agent, in order"
    )


def test_rollback_disables_tightened_and_restores_old_agent() -> None:
    """AC3 + CR R2 F1: rollback must (1) disable the tightened agent via
    BOTH forms — the label form, then the deployed absolute-path form
    (a bare filename resolves under ~ and the bootout silently fails) —
    and (2) restore the agent Deploy retired (mv its plist back from
    the disabled dir, then bootstrap it)."""
    section = _section(_header_text(), "Rollback (")
    assert _commands(section) == _expected_rollback_commands(), (
        "rollback runbook must boot out the tightened agent (label form, "
        "then deployed-path form) and then restore the NFM-5080 agent"
    )


def test_header_documents_coexistence_as_unsafe() -> None:
    """CR R2 F1: the old A/B-coexistence note ("either plist can be
    independently disabled... CR/RE can A/B the two cadences") is
    forbidden guidance. The header must state coexistence is UNSAFE and
    must not advertise A/B-ing the cadences."""
    header = _header_text()
    assert "Coexistence is UNSAFE" in header, (
        "header must state that running both agents is unsafe (union "
        "cadence -> 3-min min gap -> premature kills)"
    )
    assert "A/B" not in header, "header must not advertise A/B-ing the cadences"


def test_wrapper_sha_pinned_in_header() -> None:
    """Amended AC3 (CPO NFM-5121, confirmed CR R2): the header must pin
    the wrapper script's sha256 too — post-deploy BOTH the chokepoint
    and the wrapper must be byte-identical."""
    assert WRAPPER_SHA_HEX_PREFIX in _header_text(), (
        f"header must pin the wrapper sha ({WRAPPER_SHA_HEX_PREFIX}…) "
        "alongside the chokepoint sha"
    )


def test_wrapper_sha_preserved_on_disk() -> None:
    """Behavioral amended-AC3 check: the wrapper the tightened plist
    invokes must hash to the pinned value (the same check RE runs
    post-deploy) rather than trusting the header text."""
    result = subprocess.run(
        ["shasum", "-a", "256", WRAPPER_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"cannot verify wrapper: shasum exited {result.returncode}: "
        f"{result.stderr.strip()}"
    )
    actual_sha = result.stdout.split()[0]
    assert actual_sha.startswith(WRAPPER_SHA_HEX_PREFIX), (
        f"wrapper {WRAPPER_SCRIPT} hashes to {actual_sha}; pinned prefix "
        f"{WRAPPER_SHA_HEX_PREFIX} no longer matches — the wrapper "
        "drifted (amended AC3, NFM-5121)"
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
