"""Unit tests for tools/post-deploy-cutover-watchdog/container_sanity_check.sh — NFM-5269/NFM-5270.

Exercises the container sanity check with a fake `docker` shim on PATH so the
script can be tested without Docker. Bind-mount sources point at real temp
directories the test creates, so the emptiness assertion is exercised against
the actual filesystem.

Test groups:

  1. USAGE          --help works, unknown args exit 2
  2. CLEAN          healthy containers + non-empty bind sources -> exit 0
  3. RESTART LOOP   State.Restarting=true -> violation (NFM-5269 signature)
  4. RESTART COUNT  RestartCount >= threshold -> violation
  5. BIND EMPTY     bind source exists but empty -> violation (husk class)
  6. BIND MISSING   bind source absent -> violation
  7. ALLOWLIST      allowlisted empty dir -> no violation
  8. NO DOCKER      docker missing from PATH -> operational skip, exit 0
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
SANITY_SCRIPT = SCRIPT_DIR / "container_sanity_check.sh"

FAKE_WEBHOOK = "https://example.invalid/hook"


def _write_shim(bin_dir: Path, body: str, name: str = "docker") -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / name
    shim.write_text("#!/usr/bin/env bash\n" + body + "\n")
    shim.chmod(0o755)
    return bin_dir


def _run_sanity(
    args: list[str],
    bin_dir: Path | None = None,
    env_extra: dict | None = None,
) -> tuple[subprocess.CompletedProcess, Path | None]:
    """Run the script. Returns (result, alert_capture_path|None)."""
    env = os.environ.copy()
    capture: Path | None = None
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    if env_extra and env_extra.get("ALERT_WEBHOOK") == FAKE_WEBHOOK:
        capture = bin_dir / "last_alert.json" if bin_dir else None
    return (
        subprocess.run(
            ["bash", str(SANITY_SCRIPT)] + args,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        ),
        capture,
    )


STALE_STARTED_AT = "2026-09-01T00:00:00.000000000Z"


def _fake_docker(
    containers: list[str],
    restarting: dict[str, str] | None = None,
    restart_count: dict[str, str] | None = None,
    status: dict[str, str] | None = None,
    exit_code: dict[str, str] | None = None,
    started_at: dict[str, str] | None = None,
    mounts: dict[str, list[tuple[str, str, str]]] | None = None,
) -> str:
    """Build a fake docker shim answering the inspect formats the script uses.

    `started_at` values: "now" (emit the current UTC time) or a literal
    timestamp (use STALE_STARTED_AT for long-stable containers).
    """
    restarting = restarting or {}
    restart_count = restart_count or {}
    status = status or {}
    exit_code = exit_code or {}
    started_at = started_at or {}
    mounts = mounts or {}

    names_block = "\n".join(containers)

    def case(mapping: dict[str, str], default: str) -> str:
        lines = ""
        for name, value in mapping.items():
            lines += f'        {name}) echo "{value}" ;;\n'
        return lines + f'        *) echo "{default}" ;;\n'

    sa_lines = ""
    for name, value in started_at.items():
        if value == "now":
            sa_lines += f'        {name}) echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" ;;\n'
        else:
            sa_lines += f'        {name}) echo "{value}" ;;\n'
    sa_lines += '        *) echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" ;;\n'

    mount_lines = ""
    for name, mlist in mounts.items():
        rendered = "".join(
            f'        echo "{mtype}|{src}|{dst}";\n' for (mtype, src, dst) in mlist
        )
        mount_lines += f'      {name})\n{rendered}        ;;\n'
    mount_lines += "      *) echo '' ;;\n"

    return f"""\
if [[ "$1" == "ps" ]]; then
  cat <<'NAMES'
{names_block}
NAMES
  exit 0
fi

if [[ "$1" == "inspect" && "$2" == "--format" ]]; then
  fmt="$3"; name="$4"
  case "$fmt" in
    *Restarting*)
      case "$name" in
{case(restarting, "false")}      esac
      ;;
    *RestartCount*)
      case "$name" in
{case(restart_count, "0")}      esac
      ;;
    *.State.Status*)
      case "$name" in
{case(status, "running")}      esac
      ;;
    *.State.ExitCode*)
      case "$name" in
{case(exit_code, "0")}      esac
      ;;
    *StartedAt*)
      case "$name" in
{sa_lines}      esac
      ;;
    *Mounts*)
      case "$name" in
{mount_lines}      esac
      ;;
    *) echo "" ;;
  esac
  exit 0
fi

exit 0
"""


def _fake_curl(bin_dir: Path) -> None:
    """Fake curl: records the `-d` payload next to the shim, exits 0."""
    _write_shim(
        bin_dir,
        f'prev=""\n'
        f'for a in "$@"; do\n'
        f'  if [ "$prev" = "-d" ]; then printf \'%s\' "$a" > "{bin_dir}/last_alert.json"; fi\n'
        f'  prev="$a"\n'
        f'done\n'
        "exit 0\n",
        name="curl",
    )


# ---------------------------------------------------------------------------
# 1. Usage
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result, _ = _run_sanity(["--help"])
    assert result.returncode == 0
    assert "NFM-5269" in result.stdout


def test_unknown_arg_exits_two():
    result, _ = _run_sanity(["--bogus"])
    assert result.returncode == 2
    assert "Unknown arg" in result.stderr


# ---------------------------------------------------------------------------
# 2. Clean run
# ---------------------------------------------------------------------------


def test_clean_containers_exit_zero(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('ok')\n")
    bin_dir = tmp_path / "bin"
    # Realistic form: Docker Desktop reports the VM view with /host_mnt.
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-prod-api", "nucpot-autovc-repo-api-1"],
            mounts={
                "nucpot-autovc-repo-api-1": [("bind", f"/host_mnt{src}", "/app/src")],
            },
        ),
    )
    _fake_curl(bin_dir)
    result, _ = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": FAKE_WEBHOOK})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "No restart loops" in result.stdout


# ---------------------------------------------------------------------------
# 3. Restart loop (the NFM-5269 signature)
# ---------------------------------------------------------------------------


def test_restarting_container_alerts(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-autovc-repo-api-1"],
            restarting={"nucpot-autovc-repo-api-1": "true"},
            restart_count={"nucpot-autovc-repo-api-1": "116"},
            exit_code={"nucpot-autovc-repo-api-1": "1"},
        ),
    )
    _fake_curl(bin_dir)
    result, capture = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": FAKE_WEBHOOK})
    assert result.returncode == 81, (result.stdout, result.stderr)
    assert "RESTART LOOP" in result.stderr
    assert "nucpot-autovc-repo-api-1" in result.stderr
    assert "116" in result.stderr
    assert capture is not None and capture.exists()
    card = json.loads(capture.read_text())
    assert card["card"]["header"]["template"] == "red"
    assert "restart-loop" in card["card"]["elements"][0]["content"]
    assert "NFM-5269" in card["card"]["elements"][0]["content"]


def test_dry_run_restarting_exits_zero(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-autovc-repo-api-1"],
            restarting={"nucpot-autovc-repo-api-1": "true"},
            restart_count={"nucpot-autovc-repo-api-1": "116"},
        ),
    )
    result, _ = _run_sanity(["--dry-run"], bin_dir=bin_dir)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "dry-run" in result.stdout.lower()
    assert "NFM-5269 SANITY" in result.stdout


# ---------------------------------------------------------------------------
# 4. Restart count threshold + StartedAt churning window
# ---------------------------------------------------------------------------


def test_restart_count_churning_alerts(tmp_path):
    """Count above threshold AND last start seconds ago -> active loop."""
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-prod-worker"],
            restart_count={"nucpot-prod-worker": "12"},
            started_at={"nucpot-prod-worker": "now"},
        ),
    )
    _fake_curl(bin_dir)
    result, _ = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": FAKE_WEBHOOK})
    assert result.returncode == 81
    assert "RESTART LOOP" in result.stderr
    assert "12" in result.stderr


def test_restart_count_stable_is_informational(tmp_path):
    """The post-NFM-5269 state: RestartCount=125 on a restored, stable
    container must NOT page — INFO-only with the recreate hint."""
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-autovc-repo-api-1"],
            restart_count={"nucpot-autovc-repo-api-1": "125"},
            started_at={"nucpot-autovc-repo-api-1": STALE_STARTED_AT},
        ),
    )
    _fake_curl(bin_dir)
    result, capture = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": FAKE_WEBHOOK})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "INFO" in result.stdout
    assert "125" in result.stdout
    assert "recreate" in result.stdout
    assert capture is not None and not capture.exists()  # no alert card sent


def test_restart_count_below_threshold_clean(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-prod-worker"],
            restart_count={"nucpot-prod-worker": "2"},
        ),
    )
    result, _ = _run_sanity([], bin_dir=bin_dir)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_custom_threshold_respected(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-prod-worker"],
            restart_count={"nucpot-prod-worker": "3"},
            started_at={"nucpot-prod-worker": "now"},
        ),
    )
    # Threshold 2 < RestartCount 3 with a fresh start -> violation; no
    # webhook configured -> print to stderr and exit 0.
    result, _ = _run_sanity(
        ["--restart-threshold", "2"],
        bin_dir=bin_dir,
    )
    assert "RESTART LOOP" in result.stderr
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# 5. Empty bind source (the NFM-5269 husk)
# ---------------------------------------------------------------------------


def test_empty_bind_source_alerts(tmp_path):
    husk = tmp_path / "husk-src"
    husk.mkdir()  # exists, but zero files — the exact incident signature
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-autovc-repo-worker-1"],
            mounts={
                "nucpot-autovc-repo-worker-1": [("bind", f"/host_mnt{husk}", "/app/src")],
            },
        ),
    )
    _fake_curl(bin_dir)
    result, _ = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": FAKE_WEBHOOK})
    assert result.returncode == 81, (result.stdout, result.stderr)
    assert "BIND SOURCE EMPTY" in result.stderr
    assert str(husk) in result.stderr


# ---------------------------------------------------------------------------
# 6. Missing bind source
# ---------------------------------------------------------------------------


def test_missing_bind_source_alerts(tmp_path):
    gone = tmp_path / "deleted-checkout"
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-autovc-repo-api-1"],
            mounts={
                "nucpot-autovc-repo-api-1": [("bind", str(gone), "/app/src")],
            },
        ),
    )
    _fake_curl(bin_dir)
    result, _ = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": FAKE_WEBHOOK})
    assert result.returncode == 81
    assert "BIND SOURCE MISSING" in result.stderr
    assert str(gone) in result.stderr


# ---------------------------------------------------------------------------
# 7. Allowlist
# ---------------------------------------------------------------------------


def test_allowlisted_empty_source_is_clean(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-prod-api"],
            mounts={"nucpot-prod-api": [("bind", str(logs), "/var/log/nucpot")]},
        ),
    )
    result, _ = _run_sanity(
        [],
        bin_dir=bin_dir,
        env_extra={"BIND_EMPTY_ALLOWLIST": f"{logs}"},
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "allowlisted" in result.stdout


# ---------------------------------------------------------------------------
# 8. No docker on PATH -> operational skip
# ---------------------------------------------------------------------------


def test_no_docker_exits_zero(tmp_path):
    # Override PATH entirely: on the prod host docker lives in /usr/local/bin,
    # so /bin:/usr/bin hides it (command -v fails -> operational skip).
    # On GH ubuntu runners docker IS in /usr/bin, but no `name=nucpot`
    # containers exist there, so the empty-scope branch also exits 0.
    result, _ = _run_sanity([], env_extra={"PATH": "/bin:/usr/bin"})
    assert result.returncode == 0
    assert "nothing to check" in result.stdout


# ---------------------------------------------------------------------------
# 9. No webhook configured
# ---------------------------------------------------------------------------


def test_no_webhook_prints_to_stderr_exits_zero(tmp_path):
    husk = tmp_path / "husk-src"
    husk.mkdir()
    bin_dir = tmp_path / "bin"
    _write_shim(
        bin_dir,
        _fake_docker(
            containers=["nucpot-autovc-repo-api-1"],
            mounts={"nucpot-autovc-repo-api-1": [("bind", str(husk), "/app/src")]},
        ),
    )
    result, _ = _run_sanity([], bin_dir=bin_dir, env_extra={"ALERT_WEBHOOK": ""})
    assert result.returncode == 0
    assert "ALERT_WEBHOOK not set" in result.stderr
    assert "BIND SOURCE EMPTY" in result.stderr
