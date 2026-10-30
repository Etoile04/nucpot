"""Bounded, retrying fetch guards for the self-hosted collector checkout steps.

NFM-5796 (SRE-WARNING). The ``Collect Production Deploy Events`` lane went
red 4/12 runs over 10-28 → 10-30, all inside the ``Checkout (direct git,
private per-run workdir)`` step. Root cause chain:

1. The host-global ``url.git@github.com:.insteadOf https://github.com/``
   rewrite sends this step's ``https://github.com/...`` fetch URL down the
   SSH-over-443 lane (``git@ssh.github.com:443``), so the ``http.*.extraheader``
   token is inert on this lane and the host SSH key is the real credential
   (GHA log, run 41071062728: ``git@ssh.github.com: Permission denied
   (publickey).`` from a step whose command line names an https URL).
2. On degraded direct-egress windows that lane trickles: depth-1 fetches ran
   12-25 minutes and died mid-transfer with ``fetch-pack: unexpected
   disconnect while reading sideband packet`` → exit 128 (runs 40704807547,
   40809071001). Nothing bounded a bad attempt, so each one burned a quarter
   hour of runner time before going red.
3. One failure (41071062728) presented as a fast (~13.5s) publickey denial on
   the same lane; the identical key/config succeeds on adjacent runs, so it
   is a transient of the degraded path, not a key or config defect.

The fix keeps the transport (no NFM-5548 direct-pin violation, no proxy) and
makes every attempt bounded and retried: outer ``timeout -k`` wrapper plus
ssh ``ConnectTimeout``/``ServerAlive`` hardening, then N attempts with a
short backoff. A fully degraded window costs minutes, not 25, and the next
scheduled fire catches up via the collector's 50-run walk-back. The same
unbounded fetch body ships in three workflows on this runner, so all three
get the identical block:

- ``collect-prod-deploy-events.yml`` (this issue)
- ``prod-deploy-event-collector.yml`` (same lane, same body)
- ``site-monitor.yml`` (same defect class, same runner)

The knobs are env-tunable so tests can shrink them without editing YAML:

- ``NFMD_FETCH_MAX_ATTEMPTS`` (default 3)
- ``NFMD_FETCH_PER_ATTEMPT_SECS`` (default 120)
- ``NFMD_FETCH_RETRY_SLEEP_SECS`` (default 10)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

# All workflows that carry the inline bounded-fetch checkout body.
WORKFLOWS = {
    "collect-prod-deploy-events": REPO_ROOT / ".github/workflows/collect-prod-deploy-events.yml",
    "prod-deploy-event-collector": REPO_ROOT / ".github/workflows/prod-deploy-event-collector.yml",
    "site-monitor": REPO_ROOT / ".github/workflows/site-monitor.yml",
}

FETCH_NEEDLE = "fetch --no-tags --depth=1"
FETCH_SIGNATURE = re.compile(r"fetch\s+--no-tags\s+--depth=1")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_PER_ATTEMPT_SECS = 120
DEFAULT_RETRY_SLEEP_SECS = 10


def _load_workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _checkout_run_body(workflow_name: str) -> str:
    """Return the ``run:`` body of the checkout step that performs the fetch."""
    doc = _load_workflow(WORKFLOWS[workflow_name])
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            body = step.get("run") or ""
            if FETCH_NEEDLE in body and "mktemp -d" in body:
                return body
    raise AssertionError(
        f"no checkout step performing '{FETCH_NEEDLE}' found in {workflow_name}; "
        "the bounded-fetch contract has nowhere to live"
    )


@pytest.fixture(scope="module", params=sorted(WORKFLOWS))
def checkout_body(request: pytest.FixtureRequest) -> str:
    return _checkout_run_body(request.param)


# ---------------------------------------------------------------------------
# Static contract: every attempt is bounded
# ---------------------------------------------------------------------------


def test_per_attempt_bound_defaults_are_sane(checkout_body: str) -> None:
    """Bound must exist, be ≥30s (healthy path finishes well under it) and
    ≤180s (a 5-min cadence lane cannot afford 25-minute attempts)."""
    match = re.search(r"NFMD_FETCH_PER_ATTEMPT_SECS:-(\d+)", checkout_body)
    assert match, (
        "no NFMD_FETCH_PER_ATTEMPT_SECS default knob; the per-attempt bound "
        "must be env-tunable so tests/triage can shrink it without editing YAML"
    )
    bound = int(match.group(1))
    assert 30 <= bound <= 180, (
        f"per-attempt bound {bound}s outside [30, 180]: too small fails healthy "
        "fetches, too large re-creates the NFM-5796 25-minute burn"
    )


# ---------------------------------------------------------------------------
# Static contract: transient failures are retried
# ---------------------------------------------------------------------------


def test_fetch_is_retried(checkout_body: str) -> None:
    """At least 2 attempts by default — successes interleave with failures,
    so a single transient drop must not red the run."""
    match = re.search(r"NFMD_FETCH_MAX_ATTEMPTS:-(\d+)", checkout_body)
    assert match, "no NFMD_FETCH_MAX_ATTEMPTS default knob"
    attempts = int(match.group(1))
    assert 2 <= attempts <= 5, (
        f"max attempts {attempts} outside [2, 5]: 1 re-creates NFM-5796, "
        "more than 5 turns a dead window into a 20+ minute occupation of "
        "the serialized concurrency slot"
    )


def test_retry_backoff_is_bounded(checkout_body: str) -> None:
    match = re.search(r"NFMD_FETCH_RETRY_SLEEP_SECS:-(\d+)", checkout_body)
    assert match, "no NFMD_FETCH_RETRY_SLEEP_SECS default knob"
    assert int(match.group(1)) <= 60, "retry sleep must stay small vs the 5-min cadence"


# ---------------------------------------------------------------------------
# Static contract: nothing else regressed
# ---------------------------------------------------------------------------


def test_private_per_run_workdir_preserved(checkout_body: str) -> None:
    """NFM-5556 contract: the fetch happens in a private mktemp workdir under
    RUNNER_TEMP, exported as NFMD_CHECKOUT_DIR — never the shared workspace."""
    assert 'mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/nucpot-checkout.XXXXXX"' in checkout_body
    assert 'echo "NFMD_CHECKOUT_DIR=' in checkout_body


def test_fetchhead_checkout_preserved(checkout_body: str) -> None:
    assert "git checkout -f --progress FETCH_HEAD" in checkout_body, (
        "the FETCH_HEAD materialization is required after a successful fetch"
    )


# ---------------------------------------------------------------------------
# Cross-workflow consistency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_name", sorted(WORKFLOWS))
def test_knob_defaults_identical_across_workflows(workflow_name: str) -> None:
    """All three carriers must ship the same production defaults — a silent
    divergence would make one lane behave differently for no stated reason."""
    body = _checkout_run_body(workflow_name)
    assert f"NFMD_FETCH_MAX_ATTEMPTS:-{DEFAULT_MAX_ATTEMPTS}" in body
    assert f"NFMD_FETCH_PER_ATTEMPT_SECS:-{DEFAULT_PER_ATTEMPT_SECS}" in body
    assert f"NFMD_FETCH_RETRY_SLEEP_SECS:-{DEFAULT_RETRY_SLEEP_SECS}" in body


# ---------------------------------------------------------------------------
# PATH-shim integration: the extracted body behaves as contracted
# ---------------------------------------------------------------------------


def _bound_binary_available() -> bool:
    """The checkout body resolves ``timeout`` or (macOS coreutils) ``gtimeout``."""
    return shutil.which("timeout") is not None or shutil.which("gtimeout") is not None


# Host utilities the checkout body needs besides git and the bound wrapper;
# symlinked into a controlled PATH so tests can include/exclude binaries.
_SANDBOX_UTILITIES = ("bash", "mktemp", "tr", "base64", "cat", "cut", "sleep")

# Minimal timeout(1) stand-in: strip `-k <grace> <secs>` and exec the command.
# Kill-bound behavior is exercised separately against the real binary.
_TIMEOUT_STANDIN = textwrap.dedent(
    """\
    #!/bin/bash
    shift 3
    exec "$@"
    """
)


def _make_sandbox(tmp_path: Path) -> Path:
    sandbox = tmp_path / "sandbox-bin"
    sandbox.mkdir(exist_ok=True)
    for name in _SANDBOX_UTILITIES:
        resolved = shutil.which(name)
        assert resolved, f"sandbox needs {name!r} on the host PATH"
        os.symlink(resolved, sandbox / name)
    return sandbox


def _extract_body_to_script(workflow_name: str, dest: Path) -> Path:
    body = _checkout_run_body(workflow_name)
    script = dest / "checkout_body.sh"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


class _FakeGit:
    """A ``git`` shim driven by a plan string (comma-separated, one token per
    fetch invocation): ``ok`` → exit 0, ``fail`` → exit 128, ``sleep`` →
    ``sleep 300`` (to exercise the timeout kill). init/checkout always succeed
    and every invocation is appended to a log."""

    def __init__(self, bin_dir: Path, plan: str, log: Path) -> None:
        self.log = log
        # The plan lives in a state file: one token per fetch invocation,
        # consumed left-to-right across separate shim processes.
        (bin_dir / "plan.txt").write_text(plan + ",", encoding="utf-8")
        shim = bin_dir / "git"
        shim.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/bash
                echo "git $*" >> "{log}"
                # Strip global `-c <key>=<value>` options before dispatch.
                while [ "${1:-}" = "-c" ]; do shift 2; done
                case "${1:-}" in
                  init|checkout) exit 0 ;;
                  fetch)
                    echo "ssh_cmd=${{GIT_SSH_COMMAND:-}}" >> "{log}"
                    plan="$(cat "{bin_dir}/plan.txt")"
                    token="$(printf '%s' "$plan" | cut -d, -f1)"
                    printf '%s' "$plan" | cut -d, -f2- > "{bin_dir}/plan.txt"
                    case "$token" in
                      ok) exit 0 ;;
                      sleep) exec sleep 300 ;;
                      *) exit 128 ;;
                    esac ;;
                  *) echo "fake git: unexpected subcommand $1" >&2; exit 64 ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        shim.chmod(0o755)


def _run_checkout_body(
    tmp_path: Path,
    workflow_name: str,
    plan: str,
    extra_env: dict[str, str] | None = None,
    path_dirs: list[Path] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the extracted checkout body against the fake git.

    With ``path_dirs`` the PATH is REPLACED by ``bin_dir`` + ``path_dirs``
    (a closed sandbox), so binary resolution — e.g. the timeout/gtimeout
    guard — is fully under the test's control. Without it the host PATH is
    merely prefixed, as before.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "git_calls.log"
    log.write_text("", encoding="utf-8")
    _FakeGit(bin_dir, plan, log)

    script = _extract_body_to_script(workflow_name, tmp_path)
    env = os.environ.copy()
    if path_dirs:
        path_value = os.pathsep.join(str(d) for d in [bin_dir, *path_dirs])
    else:
        path_value = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env.update(
        {
            "PATH": path_value,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_ENV": str(tmp_path / "github_env"),
            "GITHUB_REPOSITORY": "Etoile04/nucpot",
            "GITHUB_SHA": "4e4a18ce6c6acad9169d460d03699943bfda5f57",
            "GITHUB_TOKEN": "fake-token-for-shim",
        }
    )
    env.update(extra_env or {})
    tmp_path.joinpath("github_env").touch()
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(tmp_path),
    )


def _fetch_call_count(log: Path) -> int:
    return sum(
        1 for line in log.read_text().splitlines() if " fetch " in line or line.endswith(" fetch")
    )


@pytest.mark.skipif(not _bound_binary_available(), reason="no timeout(1)/gtimeout(1) available")
@pytest.mark.parametrize("workflow_name", ["collect-prod-deploy-events"])
def test_retry_succeeds_after_transient_failures(tmp_path: Path, workflow_name: str) -> None:
    """Two transient drops then success → step exits 0 after exactly 3 fetch
    attempts. This is the 10-30 publickey-denial shape (fast per-attempt
    failure on a lane that succeeds on retry)."""
    result = _run_checkout_body(
        tmp_path,
        workflow_name,
        plan="fail,fail,ok",
        extra_env={
            "NFMD_FETCH_MAX_ATTEMPTS": "3",
            "NFMD_FETCH_PER_ATTEMPT_SECS": "60",
            "NFMD_FETCH_RETRY_SLEEP_SECS": "0",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert _fetch_call_count(tmp_path / "git_calls.log") == 3
    assert "fetch OK (attempt 3/3" in result.stdout
    # A successful run must still export the workdir for downstream steps.
    github_env = tmp_path / "github_env"
    assert "NFMD_CHECKOUT_DIR=" in github_env.read_text()


@pytest.mark.skipif(not _bound_binary_available(), reason="no timeout(1)/gtimeout(1) available")
@pytest.mark.parametrize("workflow_name", ["collect-prod-deploy-events"])
def test_exhausted_retries_propagate_failure(tmp_path: Path, workflow_name: str) -> None:
    """All attempts fail fast → step exits non-zero, exactly max_attempts
    fetch invocations, no silent success. Degraded-window shape."""
    result = _run_checkout_body(
        tmp_path,
        workflow_name,
        plan="fail,fail,fail",
        extra_env={
            "NFMD_FETCH_MAX_ATTEMPTS": "3",
            "NFMD_FETCH_PER_ATTEMPT_SECS": "60",
            "NFMD_FETCH_RETRY_SLEEP_SECS": "0",
        },
    )
    assert result.returncode != 0
    assert _fetch_call_count(tmp_path / "git_calls.log") == 3
    assert "fetch attempt 3/3 failed" in result.stdout
    # And the downstream materialization must NOT have run after failure.
    assert "checkout -f" not in (tmp_path / "git_calls.log").read_text()


@pytest.mark.skipif(not _bound_binary_available(), reason="no timeout(1)/gtimeout(1) available")
@pytest.mark.parametrize("workflow_name", ["collect-prod-deploy-events"])
def test_trickling_fetch_is_killed_at_bound(tmp_path: Path, workflow_name: str) -> None:
    """A fetch that never finishes (``sleep 300`` inside ``timeout``) must be
    killed per attempt and retried — total runtime ≈ attempts x bound, not
    the NFM-5796 25-minute burn."""
    started = time.monotonic()
    result = _run_checkout_body(
        tmp_path,
        workflow_name,
        plan="sleep,sleep",
        extra_env={
            "NFMD_FETCH_MAX_ATTEMPTS": "2",
            "NFMD_FETCH_PER_ATTEMPT_SECS": "2",
            "NFMD_FETCH_RETRY_SLEEP_SECS": "0",
        },
    )
    elapsed = time.monotonic() - started
    assert result.returncode != 0
    assert _fetch_call_count(tmp_path / "git_calls.log") == 2
    assert elapsed < 60, (
        f"bounded attempts took {elapsed:.0f}s; the timeout wrapper is not "
        "killing trickling fetches (NFM-5796 regression)"
    )


@pytest.mark.parametrize("workflow_name", sorted(WORKFLOWS))
def test_missing_timeout_binary_fails_loud(tmp_path: Path, workflow_name: str) -> None:
    """With neither timeout(1) nor gtimeout(1) on PATH the step must exit 1
    with a loud ::error:: annotation BEFORE any fetch — never silently fall
    back to an unbounded fetch (the exact NFM-5796 failure mode)."""
    sandbox = _make_sandbox(tmp_path)
    result = _run_checkout_body(
        tmp_path,
        workflow_name,
        plan="ok",
        path_dirs=[sandbox],
    )
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "::error::" in result.stdout
    assert _fetch_call_count(tmp_path / "git_calls.log") == 0


@pytest.mark.parametrize("workflow_name", sorted(WORKFLOWS))
def test_gtimeout_substitutes_when_timeout_missing(tmp_path: Path, workflow_name: str) -> None:
    """The Mac Studio runner is macOS, where coreutils ships timeout(1) as
    gtimeout(1). When `timeout` is absent the bound must ride `gtimeout`
    (same GNU `-k <grace> <secs>` semantics), not go red."""
    sandbox = _make_sandbox(tmp_path)
    log = tmp_path / "git_calls.log"
    gtimeout = sandbox / "gtimeout"
    gtimeout.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            echo "gtimeout $*" >> "{log}"
            shift 3
            exec "$@"
            """
        ),
        encoding="utf-8",
    )
    gtimeout.chmod(0o755)
    result = _run_checkout_body(
        tmp_path,
        workflow_name,
        plan="ok",
        path_dirs=[sandbox],
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    log_text = log.read_text()
    assert "gtimeout -k" in log_text, f"gtimeout(1) was not used to bound the fetch:\n{log_text}"
    git_fetches = [
        line for line in log_text.splitlines() if line.startswith("git ") and " fetch " in line
    ]
    assert len(git_fetches) == 1, f"expected exactly one fetch:\n{log_text}"


@pytest.mark.parametrize("workflow_name", sorted(WORKFLOWS))
def test_ssh_lane_is_hardened(tmp_path: Path, workflow_name: str) -> None:
    """The fetch child must actually inherit GIT_SSH_COMMAND with a bounded
    connect phase (ConnectTimeout) and dead-peer detection mid-transfer
    (ServerAlive probes) — the host insteadOf rewrite sends the https URL
    down the SSH lane, so this is the real transport."""
    sandbox = _make_sandbox(tmp_path)
    timeout_shim = sandbox / "timeout"
    timeout_shim.write_text(_TIMEOUT_STANDIN, encoding="utf-8")
    timeout_shim.chmod(0o755)
    result = _run_checkout_body(
        tmp_path,
        workflow_name,
        plan="ok",
        path_dirs=[sandbox],
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ssh_cmds = [
        line
        for line in (tmp_path / "git_calls.log").read_text().splitlines()
        if line.startswith("ssh_cmd=")
    ]
    assert ssh_cmds, "the fetch child never saw GIT_SSH_COMMAND in its environment"
    ssh_cmd = ssh_cmds[0]
    assert "ConnectTimeout=15" in ssh_cmd, f"connect phase unbounded: {ssh_cmd}"
    assert "ServerAliveInterval=" in ssh_cmd and "ServerAliveCountMax=" in ssh_cmd, (
        f"no dead-peer detection mid-transfer: {ssh_cmd}"
    )
