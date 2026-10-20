"""NFM-5578 — post-deploy G5 trim hook (ADR-013 G5 amendment, NFM-5101 Option D).

The 2026-10-18 CTO ruling on NFM-5101 (Option D) requires every successful
prod deploy to fire the sanctioned G5 cleanup wrapper with args
byte-identical to the com.nfm.g2.cleanup-daily cron, without ever being able
to fail the deploy. These tests pin the four binding ruling constraints by
EXECUTING the real shell surfaces with stubbed sudo/docker (the
test_deploy_prod_throttle_gate.py pattern):

* constraint 4 / AC1 — the hook's arg set equals the installed cron's
  plist ProgramArguments as a set of option pairs, ``--until`` absent from
  both, and the deploy_prod.sh success tail (the block after the
  DEPLOY_SCRIPT_COMPLETED_OK anchor) actually invokes the helper;
* constraint 1 / AC1 — a trim failure (wrapper exit 42) leaves the deploy
  tail's exit code 0 and emits an SRE-visible ``WARNING (NFM-5578)``;
* constraint 2 / AC2 — the wrapper entry holds the single flock-class
  concurrency guard covering BOTH the cron path and the hook path: a second
  run while one holds the lock SKIPs (exit 0, no docker work), the lock is
  released on completion, and a stale lock (dead holder pid) is broken;
* constraint 3 / AC3 — the run is idempotent/re-runnable (post-lock run
  executes fully);
* deliverable 2 / AC3 — the ADR-013 G5 amendment note exists in the repo
  and carries the NFM-4224 / NFM-4270 cross-reference chain.
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PROD = REPO_ROOT / "scripts" / "deploy_prod.sh"
TRIM_HELPER = REPO_ROOT / "scripts" / "post_deploy_trim.sh"
CLEANUP_ENTRY = REPO_ROOT / "scripts" / "host-prod-gate" / "entries" / "run-cleanup.sh"
CRON_PLIST = REPO_ROOT / "scripts" / "host-prod-gate" / "launchd" / "com.nfm.g2.cleanup-daily.plist"
ADR_013 = REPO_ROOT / "docs" / "adr" / "ADR-013-NFM-4266-prod-mutation-guardrails.md"

COMPLETION_MARKER = "DEPLOY_SCRIPT_COMPLETED_OK"

# Records argv (one line, space-joined) then execs the target. $@ excludes
# the script's own name, so the wrapper path is argv 4 (after `-n -u USER`)
# — skip exactly three words.
STUB_SUDO = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${NFM5578_SUDO_CALLS}"
exec "${@:4}"
"""

# Records "<tag> <args>"; `docker system df` sleeps NFM5578_DOCKER_DF_SLEEP
# seconds (lock-holder slow-run) and prints a df line; everything exits 0.
STUB_DOCKER = """#!/usr/bin/env bash
printf '%s %s\\n' "${NFM5578_DOCKER_TAG:-?}" "$*" >> "${NFM5578_DOCKER_LOG:?}"
if [ "$1" = "system" ] && [ "$2" = "df" ]; then
  if [ "${NFM5578_DOCKER_DF_SLEEP:-0}" -gt 0 ]; then
    sleep "${NFM5578_DOCKER_DF_SLEEP}"
  fi
  printf '1.5GB\\n'
fi
exit 0
"""

# Records argv and exits NFM5578_WRAPPER_RC (default 0).
STUB_WRAPPER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${NFM5578_WRAPPER_CALLS:?}"
echo "stub-cleanup-run pid=$$"
exit "${NFM5578_WRAPPER_RC:-0}"
"""


def _write_stub(bin_dir: Path, name: str, text: str) -> None:
    stub = bin_dir / name
    stub.write_text(text, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _marker_env(
    tmp_path: Path,
    wrapper_rc: int = 0,
    docker_df_sleep: int = 0,
) -> dict[str, str]:
    """Env + stub bin dir shared by helper/fragment/entry runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_stub(bin_dir, "sudo", STUB_SUDO)
    _write_stub(bin_dir, "docker", STUB_DOCKER)

    fake_wrapper = tmp_path / "fake-run-cleanup.sh"
    _write_stub(tmp_path, "fake-run-cleanup.sh", STUB_WRAPPER)

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "DEPLOY_SHA": "deadbeefcafe0123456789abcdef0123456789",
            "NFM_G2_CLEANUP_WRAPPER": str(fake_wrapper),
            "NFM5578_SUDO_CALLS": str(tmp_path / "sudo_calls"),
            "NFM5578_DOCKER_LOG": str(tmp_path / "docker_calls"),
            "NFM5578_WRAPPER_CALLS": str(tmp_path / "wrapper_calls"),
            "NFM5578_WRAPPER_RC": str(wrapper_rc),
            "NFM5578_DOCKER_DF_SLEEP": str(docker_df_sleep),
        }
    )
    for log in ("sudo_calls", "docker_calls", "wrapper_calls"):
        (tmp_path / log).write_text("", encoding="utf-8")
    return env


def _read_log(tmp_path: Path, name: str) -> list[str]:
    return [
        line for line in (tmp_path / name).read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _extract_success_tail() -> str:
    """The deploy_prod.sh block from the completion anchor to EOF."""
    source = DEPLOY_PROD.read_text(encoding="utf-8")
    match = re.search(rf'^echo "{COMPLETION_MARKER}.*', source, re.MULTILINE)
    assert match, (
        f"{COMPLETION_MARKER} anchor missing from deploy_prod.sh — the "
        "NFM-3777 cutover anchor cannot have moved without updating these "
        "guards."
    )
    return source[match.start() :]


def _parse_option_pairs(argv: list[str]) -> dict[str, str]:
    """`--flag value` pairs from an argv vector (order-insensitive)."""
    pairs: dict[str, str] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            assert i + 1 < len(argv), f"option {token} has no value in {argv}"
            pairs[token] = argv[i + 1]
            i += 2
        else:
            i += 1
    return pairs


def _run_bash(
    script: Path, env: dict[str, str], cwd: Path = REPO_ROOT, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Constraint 4 / AC1 — hook args are byte-identical to the installed cron
# ---------------------------------------------------------------------------


def test_hook_args_match_installed_cron_plist() -> None:
    with CRON_PLIST.open("rb") as fh:
        plist = plistlib.load(fh)
    argv = [str(s) for s in plist["ProgramArguments"]]
    # The plist invokes `sudo -n -u nfmdeploy <wrapper> --keep-shas 5 ...`;
    # the option pairs after the wrapper path are the cron arg set.
    wrapper_index = next(i for i, tok in enumerate(argv) if tok.endswith("run-cleanup.sh"))
    cron_pairs = _parse_option_pairs(argv[wrapper_index + 1 :])
    assert cron_pairs == {"--keep-candidates": "2", "--keep-shas": "5"}

    helper_source = TRIM_HELPER.read_text(encoding="utf-8")
    literals = dict(re.findall(r"^(KEEP_CANDIDATES|KEEP_SHAS)=(\d+)", helper_source, re.MULTILINE))
    hook_pairs = {
        "--keep-candidates": literals.get("KEEP_CANDIDATES", ""),
        "--keep-shas": literals.get("KEEP_SHAS", ""),
    }
    assert hook_pairs == cron_pairs, (
        "post-deploy trim args drifted from the installed cron plist "
        f"(hook={hook_pairs}, cron={cron_pairs}) — ruling constraint 4 "
        "requires a single shared arg set"
    )
    assert re.search(
        r"--keep-candidates \"\$\{KEEP_CANDIDATES\}\" --keep-shas \"\$\{KEEP_SHAS\}\"",
        helper_source,
    ), "helper must pass the args as the pinned keep variables"
    code_only = "\n".join(
        ln for ln in helper_source.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "--until" not in code_only, (
        "--until must stay OFF in the hook's code (ruling: age-gated "
        "passes rejected, options B/C); comments may reference it"
    )


# ---------------------------------------------------------------------------
# AC1 — the success tail fires the helper with the exact cron args
# ---------------------------------------------------------------------------


def test_success_tail_fires_helper_with_cron_args(tmp_path: Path) -> None:
    tail = _extract_success_tail()
    assert "post_deploy_trim.sh" in tail, (
        "deploy_prod.sh success tail no longer invokes the NFM-5578 trim helper"
    )
    tail_script = tmp_path / "tail.sh"
    tail_script.write_text(tail, encoding="utf-8")
    env = _marker_env(tmp_path)

    result = _run_bash(tail_script, env)

    assert result.returncode == 0, result.stderr
    wrapper_calls = _read_log(tmp_path, "wrapper_calls")
    assert wrapper_calls == ["--keep-candidates 2 --keep-shas 5"], wrapper_calls
    # Non-nfmdeploy caller → sanctioned sudo path, wrapper argv passed whole.
    sudo_calls = _read_log(tmp_path, "sudo_calls")
    assert len(sudo_calls) == 1
    assert sudo_calls[0].endswith("--keep-candidates 2 --keep-shas 5")
    assert "--until" not in sudo_calls[0]


def test_trim_failure_does_not_fail_the_deploy(tmp_path: Path) -> None:
    tail = _extract_success_tail()
    tail_script = tmp_path / "tail.sh"
    tail_script.write_text(tail, encoding="utf-8")
    env = _marker_env(tmp_path, wrapper_rc=42)

    result = _run_bash(tail_script, env)

    assert result.returncode == 0, (
        "a trim failure must NEVER fail the deploy (ruling constraint 1): "
        f"rc={result.returncode}, stderr={result.stderr}"
    )
    assert "WARNING (NFM-5578)" in result.stdout, (
        "trim failure must emit the SRE-visible WARNING (NFM-5578) line, "
        f"got stdout={result.stdout!r}"
    )
    assert "cron backstop" in result.stdout


def test_helper_direct_identity_branch_skips_sudo(tmp_path: Path) -> None:
    import os

    env = _marker_env(tmp_path)
    env["NFM_G2_DEPLOY_USER"] = env.get("USER") or "nobody"

    result = _run_bash(TRIM_HELPER, env)

    assert result.returncode == 0, result.stderr
    assert _read_log(tmp_path, "wrapper_calls") == ["--keep-candidates 2 --keep-shas 5"]
    assert os.path.isfile(env["NFM5578_SUDO_CALLS"])
    assert _read_log(tmp_path, "sudo_calls") == [], (
        "deploy-identity branch must exec the wrapper directly "
        "(sudo-to-self is not granted on the host gate)"
    )


def test_helper_missing_wrapper_skips_gracefully(tmp_path: Path) -> None:
    env = _marker_env(tmp_path)
    env["NFM_G2_CLEANUP_WRAPPER"] = str(tmp_path / "not-installed" / "run-cleanup.sh")

    result = _run_bash(TRIM_HELPER, env)

    assert result.returncode == 0, result.stderr
    assert "NOTE (NFM-5578)" in result.stdout
    assert _read_log(tmp_path, "sudo_calls") == []


# ---------------------------------------------------------------------------
# Constraint 2 / AC2 — wrapper-level concurrency guard (cron + hook paths)
# ---------------------------------------------------------------------------


def _entry_env(tmp_path: Path, tag: str, df_sleep: int = 0) -> tuple[dict[str, str], Path]:
    env = _marker_env(tmp_path, docker_df_sleep=df_sleep)
    env.update(
        {
            "NFM_G2_DEPLOY_USER": env.get("USER") or "nobody",
            "NFM_G2_REPO": str(REPO_ROOT),
            "NFM_G2_VAR_DIR": str(tmp_path / "var"),
            "NFM5578_DOCKER_TAG": tag,
        }
    )
    # The entry under test is the real repo source; point the wrapper
    # override at it so the helper branch does not shadow the entry tests.
    env.pop("NFM_G2_CLEANUP_WRAPPER", None)
    return env, tmp_path / "var" / "cleanup.lock"


def test_cleanup_entry_lock_mutual_exclusion(tmp_path: Path) -> None:
    env_a, lock_file = _entry_env(tmp_path, tag="A", df_sleep=6)
    (tmp_path / "var").mkdir(parents=True, exist_ok=True)

    holder = subprocess.Popen(
        ["bash", str(CLEANUP_ENTRY)],
        env=env_a,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not lock_file.exists():
            assert time.monotonic() < deadline, "holder never acquired the cleanup lock"
            time.sleep(0.05)

        env_b, _ = _entry_env(tmp_path, tag="B")
        second = _run_bash(CLEANUP_ENTRY, env_b)

        assert second.returncode == 0, second.stderr
        assert "SKIP (NFM-5578)" in second.stdout, (
            f"busy lock must SKIP (NFM-5578), got stdout={second.stdout!r}"
        )
        b_calls = [line for line in _read_log(tmp_path, "docker_calls") if line.startswith("B ")]
        assert b_calls == [], (
            f"a second cleanup run must NOT touch docker while the lock is held: {b_calls}"
        )
    finally:
        holder.wait(timeout=60)

    assert holder.returncode == 0, holder.stderr

    # Lock released after the holder exits → a fresh run executes fully.
    env_c, _ = _entry_env(tmp_path, tag="C")
    third = _run_bash(CLEANUP_ENTRY, env_c)
    assert third.returncode == 0, third.stderr
    assert "SKIP (NFM-5578)" not in third.stdout
    c_calls = [line for line in _read_log(tmp_path, "docker_calls") if line.startswith("C ")]
    assert c_calls, "post-lock cleanup run must reach docker (idempotent re-run)"


def test_cleanup_entry_breaks_stale_lock(tmp_path: Path) -> None:
    var_dir = tmp_path / "var"
    var_dir.mkdir(parents=True, exist_ok=True)

    # A pid that is provably dead by the time the entry runs.
    dead = subprocess.run(["bash", "-c", "echo $$"], capture_output=True, text=True, check=True)
    dead_pid = dead.stdout.strip()
    time.sleep(0.2)

    (var_dir / "cleanup.lock").write_text(f"{dead_pid}\n", encoding="utf-8")

    env, _ = _entry_env(tmp_path, tag="S")
    result = _run_bash(CLEANUP_ENTRY, env)

    assert result.returncode == 0, result.stderr
    assert "SKIP (NFM-5578)" not in result.stdout, (
        f"a stale (dead-holder) lock must be broken, not skipped: {result.stdout!r}"
    )
    s_calls = [line for line in _read_log(tmp_path, "docker_calls") if line.startswith("S ")]
    assert s_calls, "stale-lock run must proceed to docker work"


# ---------------------------------------------------------------------------
# Deliverable 2 / AC3 — ADR-013 G5 amendment note with the reference chain
# ---------------------------------------------------------------------------


def test_adr013_g5_amendment_note_present() -> None:
    adr = ADR_013.read_text(encoding="utf-8")
    section = re.search(
        r"### g5.*?amendment.*?(?=^### |\Z)", adr, re.MULTILINE | re.DOTALL | re.IGNORECASE
    )
    assert section, (
        "ADR-013 must carry the G5 amendment note (NFM-5101 CTO Option D "
        "ruling) — deliverable 2 of NFM-5578"
    )
    body = section.group(0)
    for ref in ("NFM-5101", "NFM-4224", "NFM-4270", "NFM-5578"):
        assert ref in body, f"G5 amendment note must cross-link {ref}"
    for required in ("2.75", "keep-5", "Option D", "byte-identical"):
        assert required in body, f"G5 amendment note must carry the ruling's '{required}' content"
