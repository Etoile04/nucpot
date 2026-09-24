"""Behavioral guards on the prod post-cutover health gate (NFM-5208 AC1/AC3).

PR #1416 (NFM-5203) replaced the gate's single ``curl -f`` with a bounded
12x5s retry loop, piping every poll to ``/dev/null 2>&1``. When the gate
exhausted, the deploy log lost the final poll's error class entirely: the
NFM-5203 warmup-500 incident and run 35991349533 (a different flake class,
``pip ReadTimeoutError`` downloading pycurl) both reduced to "not healthy
after 12 polls" with nothing to triage on.

``health_first_poll`` executes inside the workflow's SSH heredoc, so there
is no importable module: these tests extract the shipped function body from
``scripts/deploy_prod.sh`` and drive it with a stub ``curl`` that scripts
``rc|stderr|body`` per poll, pinning:

* exhaustion surfaces the LAST attempt's curl exit code, error message and
  response body on stderr — earlier attempts' diagnostics are overwritten,
  so a warmup 5xx storm cannot bury the real final error;
* diagnostic output is bounded (512 B stderr / 1 KiB body) so a runaway
  HTML error page cannot flood the deploy log;
* a retried success returns 0 with the ``health OK on poll N/M`` line and
  no FAILED noise;
* curl without ``--fail-with-body`` degrades to status-only diagnostics and
  the gate still passes — a curl capability gap must cost diagnostics,
  never the gate itself;
* no sleep is burned after the final poll.

The marker-semantics guard (NFM-5208 AC2) also lives here because it pins
deploy_prod.sh's own comment: the ``nfmd_prod_health_passed`` marker means
"passed within budget", not "passed on the literal first attempt".
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PROD = REPO_ROOT / "scripts" / "deploy_prod.sh"

GATE_URL = "http://gate.test/api/v1/health"

# Behavior plan: one "rc|stderr|body" line per poll invocation; polls past
# the last line repeat it. NFM5208_PROBE_RC controls the exit code of the
# ``curl --fail-with-body --version`` capability probe (0 = modern curl,
# 2 = old curl that rejects the flag).
STUB_CURL = """#!/usr/bin/env bash
if [ "$1" = "--fail-with-body" ] && [ "$2" = "--version" ]; then
  exit "${NFM5208_PROBE_RC:-0}"
fi
out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift 2; continue; fi
  shift
done
n="$(cat "${NFM5208_COUNT}")"
printf '%s' "$((n + 1))" > "${NFM5208_COUNT}"
plan="$(sed -n "$((n + 1))p" "${NFM5208_PLAN}")"
if [ -z "$plan" ]; then plan="$(tail -n 1 "${NFM5208_PLAN}")"; fi
rc="${plan%%|*}"
rest="${plan#*|}"
err="${rest%%|*}"
body="${rest#*|}"
if [ -n "$out" ]; then printf '%s' "$body" > "$out"; fi
printf '%s' "$err" >&2
exit "$rc"
"""


def _extract_function() -> str:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    match = re.search(r"^health_first_poll\(\) \{.*?\n\}", text, re.MULTILINE | re.DOTALL)
    assert match, (
        "health_first_poll() is missing from scripts/deploy_prod.sh — the "
        "prod post-cutover gate cannot have been deleted or renamed without "
        "updating these guards (NFM-5208)."
    )
    return match.group(0)


def _run_gate(
    tmp_path: Path,
    plan_lines: list[str],
    *,
    attempts: int = 3,
    delay: str = "0",
    probe_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run the extracted health_first_poll against a stub curl.

    Returns the subprocess result; the stub's per-poll plan lives in the
    tmp_path so tests are fully isolated from each other's poll counters.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "curl"
    stub.write_text(STUB_CURL, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    plan = tmp_path / "plan"
    plan.write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
    count = tmp_path / "count"
    count.write_text("0", encoding="utf-8")
    diag = tmp_path / "diag"
    diag.mkdir(exist_ok=True)

    fn_file = tmp_path / "fn.sh"
    fn_file.write_text(_extract_function() + "\n", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "NFM5208_PLAN": str(plan),
        "NFM5208_COUNT": str(count),
        "NFM5208_PROBE_RC": str(probe_rc),
        "TMPDIR": str(diag),
    }
    script = f'. "{fn_file}"; health_first_poll "{GATE_URL}" {attempts} {delay}'
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _diagnostic_files(diag_dir: Path) -> list[str]:
    return sorted(p.name for p in diag_dir.iterdir()) if diag_dir.exists() else []


# --------------------------------------------------------------------------
# AC1 — exhaustion path surfaces the last attempt's diagnostics
# --------------------------------------------------------------------------


def test_exhaustion_surfaces_last_attempt_diagnostics(tmp_path: Path) -> None:
    result = _run_gate(
        tmp_path,
        [
            "28|stub: early Operation timed out|<early-timeout/>",
            '22|curl: (22) The requested URL returned error: 500|{"detail":"warmup 500"}',
        ],
        attempts=2,
    )
    assert result.returncode == 1
    err = result.stderr
    assert f"health gate FAILED: {GATE_URL} not healthy after 2 polls" in err
    assert "last poll 2/2 curl stderr: curl: (22) The requested URL returned error: 500" in err
    assert '{"detail":"warmup 500"}' in err
    # Only the LAST attempt's diagnostics: the earlier poll's output was
    # overwritten, so a transient warmup failure cannot bury the final one.
    assert "early Operation timed out" not in err
    assert "<early-timeout/>" not in err


def test_each_poll_failure_line_carries_its_exit_code(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, ["7|x|b", "22|x|b"], attempts=2)
    assert result.returncode == 1
    assert f"health poll 1/2 failed for {GATE_URL} (curl exit 7); retrying in 0s" in result.stdout
    assert f"health poll 2/2 failed for {GATE_URL} (curl exit 22)" in result.stdout
    # The final poll must not promise a retry that never happens.
    assert "retrying" not in result.stdout.splitlines()[-1]


def test_diagnostics_are_bounded(tmp_path: Path) -> None:
    result = _run_gate(
        tmp_path,
        [f"22|{'E' * 5000}|{'B' * 5000}"],
        attempts=1,
    )
    assert result.returncode == 1
    err_lines = result.stderr.splitlines()
    err_line = next(line for line in err_lines if "curl stderr:" in line)
    body_line = next(line for line in err_lines if "response body" in line)
    # Count past the ": " separator — the labels themselves contain "B" (KiB).
    assert err_line.split(": ", 1)[1].count("E") == 512, "stderr diagnostic must cap at 512 bytes"
    assert body_line.split(": ", 1)[1].count("B") == 1024, "body diagnostic must cap at 1 KiB"


def test_no_sleep_after_final_poll(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run_gate(tmp_path, ["22|e|b"], attempts=3, delay="1")
    elapsed = time.monotonic() - started
    assert result.returncode == 1
    # 3 attempts with sleeps BETWEEN polls = 2 sleeps. A trailing sleep
    # after the final failure wastes delay_s on an already-doomed gate and
    # prints a "retrying" promise that never runs.
    assert elapsed < 3.0, f"gate took {elapsed:.1f}s for 3 attempts/1s delay — sleeping after final poll?"


# --------------------------------------------------------------------------
# Success paths stay quiet (PR #1416 behavior preserved)
# --------------------------------------------------------------------------


def test_retried_success_returns_zero_and_reports_poll_number(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, ["22|e|b", "0||ok"], attempts=3)
    assert result.returncode == 0
    assert result.stderr == ""
    assert f"health OK on poll 2/3 for {GATE_URL} (after 1 failure(s))" in result.stdout
    assert "FAILED" not in result.stdout


def test_first_attempt_success_is_fully_quiet(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, ["0||ok"], attempts=3)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_diagnostic_files_cleaned_on_success_and_exhaustion(tmp_path: Path) -> None:
    # _run_gate points TMPDIR at tmp_path/"diag"; both terminal paths must
    # remove the last-poll body/err files rather than leak them in $TMPDIR
    # (the marker-poisoning history of /tmp makes leftover state suspect).
    for plan in (["0||ok"], ["22|e|b"]):
        result = _run_gate(tmp_path, plan, attempts=1)
        assert result.returncode == (1 if plan[0].startswith("22") else 0)
        assert _diagnostic_files(tmp_path / "diag") == [], (
            "health-gate diagnostic files must not leak in $TMPDIR"
        )


# --------------------------------------------------------------------------
# curl capability probe — a missing --fail-with-body must never fail the gate
# --------------------------------------------------------------------------


def test_old_curl_without_fail_with_body_still_passes(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, ["0||ok"], attempts=2, probe_rc=2)
    assert result.returncode == 0
    assert result.stdout == ""


def test_old_curl_fallback_still_surfaces_status_error(tmp_path: Path) -> None:
    # Degrading to plain -f loses the error body but keeps the status line —
    # the exhaustion path must still name the class.
    result = _run_gate(
        tmp_path,
        ["22|curl: (22) The requested URL returned error: 503|"],
        attempts=1,
        probe_rc=2,
    )
    assert result.returncode == 1
    assert "error: 503" in result.stderr


def test_probe_uses_fail_with_body_when_supported(tmp_path: Path) -> None:
    # The stub accepts --fail-with-body; if the gate stopped probing (or
    # always used -f), the body capture assert in the exhaustion test above
    # would still pass via the stub. Pin the probe explicitly: the wrapper
    # must invoke curl WITH --fail-with-body when the probe succeeds.
    text = _extract_function()
    assert "--fail-with-body" in text, (
        "the capability probe for --fail-with-body is gone from "
        "health_first_poll — an old curl on the deploy host would then fail "
        "every poll with 'option is unknown' and flip every deploy red"
    )


# --------------------------------------------------------------------------
# AC2 — marker semantics documentation lives next to the marker contract
# --------------------------------------------------------------------------


def test_marker_semantics_documented_in_deploy_script() -> None:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    # The comment block above health_first_poll must carry the
    # passed-within-budget clarification (NFM-5208) so nobody reading the
    # marker contract mistakes it for staging's literal first-probe gate.
    assert "WITHIN BUDGET" in text, (
        "the nfmd_prod_health_passed marker semantics comment (passed within "
        "budget, not literal first attempt) is missing from deploy_prod.sh"
    )
    assert re.search(r"health OK on poll \$i/\$attempts", text), (
        "the operator-facing per-poll diagnostic line must stay — it is what "
        "distinguishes a first-attempt pass from a retried pass now that the "
        "fragment field means 'within budget'"
    )
