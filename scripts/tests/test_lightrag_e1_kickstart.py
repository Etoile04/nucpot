"""Tests for the NFM-5555 E-1 burst-window kickstart helper.

Incident (2026-10-16 03:48-03:56Z): com.nfm.g2.lightrag-watchdog has no
RunAtLoad/KeepAlive (boot-race convention) and StartInterval=300, so a
host_setup.sh §6 bootout/bootstrap leaves the lane blind until the next
interval fire — and inside the E-1 burst window (03:25-05:00Z) the bootout
also kills the wrapper holding the 60s tick cadence, exactly where wedge
risk concentrates (chronic 03:30Z wedge hour, NFM-4887).

The fix: scripts/host-prod-gate/lightrag-e1-kickstart.sh, invoked by
host_setup.sh right after the §6 bootstrap loop. Inside the window it
`launchctl kickstart`s the label so the wrapper re-enters within seconds;
outside it no-ops. Fail-open by design (a failed kickstart only degrades
to the pre-fix next-interval fire).

Hermetic: fake `date` (padded %H/%M) + fake `launchctl` (records argv)
are wired via the helper's NFM_G2_E1_KICKSTART_* env hooks.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
HELPER = GATE_DIR / "lightrag-e1-kickstart.sh"
WRAPPER = GATE_DIR / "entries" / "start-lightrag-watchdog.sh"
HOST_SETUP = GATE_DIR / "host_setup.sh"

LABEL = "system/com.nfm.g2.lightrag-watchdog"


# ---- syntax ---------------------------------------------------------------------


def test_bash_syntax():
    result = subprocess.run(["/bin/bash", "-n", str(HELPER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_helper_is_executable_in_repo():
    assert HELPER.stat().st_mode & 0o111, "lightrag-e1-kickstart.sh lost its exec bit"


# ---- harness --------------------------------------------------------------------


class KickstartHarness:
    """Runs the helper with a fake date (padded %H/%M) + fake launchctl."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin_dir = tmp_path / "fakebin"
        self.bin_dir.mkdir()
        self.calls = tmp_path / "launchctl-calls.txt"
        self._write(
            "date",
            'case "$*" in\n'
            '  *%H*) printf "%s\\n" "$FAKE_H" ;;\n'
            '  *%M*) printf "%s\\n" "$FAKE_M" ;;\n'
            "esac\n",
        )
        self.launchctl_rc = 0
        self._write_launchctl()

    def _write(self, name: str, body: str) -> None:
        target = self.bin_dir / name
        target.write_text(f"#!/bin/bash\n{body}\n")
        target.chmod(0o755)

    def _write_launchctl(self) -> None:
        self._write(
            "launchctl",
            f'printf \'%s\\n\' "$*" >> {self.calls}\nexit {self.launchctl_rc}\n',
        )

    def run(self, hour: str, minute: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["FAKE_H"] = hour
        env["FAKE_M"] = minute
        env["NFM_G2_E1_KICKSTART_DATE"] = str(self.bin_dir / "date")
        env["NFM_G2_E1_KICKSTART_LAUNCHCTL"] = str(self.bin_dir / "launchctl")
        return subprocess.run(
            ["/bin/bash", str(HELPER)],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )

    def launchctl_argv(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [line.split() for line in self.calls.read_text().splitlines()]


@pytest.fixture()
def ks(tmp_path):
    return KickstartHarness(tmp_path)


# ---- in-window behavior -----------------------------------------------------------


def test_inside_window_kickstarts_label(ks):
    """The incident minute itself (03:48Z, mod=228): the wrapper must be
    kickstarted immediately — not one 300s interval later."""
    result = ks.run("03", "48")
    assert result.returncode == 0, result.stderr
    assert ks.launchctl_argv() == [["kickstart", LABEL]]
    assert "kickstart" in result.stdout


def test_window_lower_bound_inclusive(ks):
    result = ks.run("03", "25")  # mod=205 — window entry
    assert result.returncode == 0, result.stderr
    assert ks.launchctl_argv() == [["kickstart", LABEL]]


def test_window_upper_bound_exclusive(ks):
    """mod=300 (05:00Z) is window EXIT — no kickstart, the wrapper would
    immediately exit anyway."""
    result = ks.run("05", "00")
    assert result.returncode == 0, result.stderr
    assert ks.launchctl_argv() == []


def test_last_in_window_minute_kickstarts(ks):
    result = ks.run("04", "59")  # mod=299 — still in window
    assert result.returncode == 0, result.stderr
    assert ks.launchctl_argv() == [["kickstart", LABEL]]


@pytest.mark.parametrize("hour,minute", [("00", "00"), ("03", "24"), ("12", "34"), ("23", "59")])
def test_outside_window_never_touches_launchctl(ks, hour, minute):
    """Outside 03:25-05:00Z the 5-min first-interval gap equals the
    steady-state cadence — the helper must be a complete no-op."""
    result = ks.run(hour, minute)
    assert result.returncode == 0, result.stderr
    assert ks.launchctl_argv() == []


def test_octal_hours_do_not_abort(ks):
    """NFM-5122 class guard: %H/%M are zero-padded and bash 3.2 treats
    leading zeros as octal — 08/09 must not abort the helper (the
    pre-fix wrapper hit exactly this with `value too great for base`)."""
    result = ks.run("09", "05")
    assert result.returncode == 0, result.stderr
    assert "base" not in result.stderr
    assert ks.launchctl_argv() == []


# ---- fail-open contract -----------------------------------------------------------


def test_kickstart_failure_fails_open(ks):
    """A failed kickstart must not fail the host_setup run — it only
    degrades to the pre-NFM-5555 behavior (next interval fire)."""
    ks.launchctl_rc = 1
    ks._write_launchctl()
    result = ks.run("03", "48")
    assert result.returncode == 0, f"helper must fail open, rc={result.returncode}: {result.stderr}"
    assert "WARNING" in result.stdout


# ---- bounds stay in lockstep with the wrapper (drift guard) ------------------------


def _wrapper_default(var: str) -> str:
    text = WRAPPER.read_text(encoding="utf-8")
    match = re.search(
        rf'^{var}="\$\{{NFM_LIGHTRAG_WATCHDOG_{var}:-([0-9]+)\}}"$', text, re.MULTILINE
    )
    assert match, f"could not read {var} default from the wrapper — reformatted?"
    return match.group(1)


def _helper_bound(var: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    match = re.search(rf'^{var}=([0-9]+)\b', text, re.MULTILINE)
    assert match, f"could not read {var} from the helper"
    return match.group(1)


def test_bounds_match_wrapper_e1_defaults():
    """The helper's window must be the wrapper's E1_START_MIN/E1_END_MIN
    defaults (03:25Z=205, 05:00Z=300 minutes-of-day UTC). A retune of the
    wrapper bounds without the helper (or vice versa) recreates a blind or
    premature kickstart window."""
    assert _helper_bound("START_MIN") == _wrapper_default("E1_START_MIN")
    assert _helper_bound("END_MIN") == _wrapper_default("E1_END_MIN")


# ---- host_setup wiring ------------------------------------------------------------


def test_host_setup_invokes_helper_after_bootstrap_loop():
    """Source inspection (host_setup needs root to execute): the kickstart
    must run right after the §6 bootstrap/enable loop, guarded so a helper
    crash cannot kill a sanctioned host_setup run mid-flow."""
    text = HOST_SETUP.read_text(encoding="utf-8")
    invoke = re.search(r'^bash "\$\{SRC\}/lightrag-e1-kickstart\.sh"', text, re.MULTILINE)
    assert invoke, "host_setup.sh does not invoke lightrag-e1-kickstart.sh"
    line = text[invoke.start() : text.index("\n", invoke.start())]
    assert "||" in line, "invocation must be fail-open guarded (|| log …)"
    bootstrap_at = text.index("launchctl bootstrap system")
    assert invoke.start() > bootstrap_at, "kickstart must follow the §6 bootstrap loop"
