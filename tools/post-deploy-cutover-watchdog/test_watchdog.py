"""Unit tests for tools/post-deploy-cutover-watchdog/watchdog.sh — NFM-3337.

Exercises the stale-container watchdog with a fake `docker` shim and
fake JSONL file on PATH so the script can be tested without Docker
or a real deploy events file.

Test groups:

  1. USAGE          --help works, unknown args exit 2
  2. NO JSONL       missing/empty JSONL exits 0 (nothing to check)
  3. ALL MATCH      all containers match expected image -> exit 0
  4. STALE DETECTED image mismatch + container predates deploy -> exit 80
  5. FALSE-POSITIVE GUARD  image mismatch but container created AFTER
                       deploy -> exit 0 (silent OK, AC-3.4)
  6. DRY-RUN        --dry-run prints verdict, exits 0, no webhook call
  7. NO WEBHOOK     missing ALERT_WEBHOOK prints to stderr, exits 0
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
WATCHDOG_SCRIPT = SCRIPT_DIR / "watchdog.sh"

DEPLOY_SHA = "abcdef1234567890abcdef1234567890abcdef12"
DEPLOY_TS = "2026-08-18T14:00:00.000000000Z"

OLD_API_IMAGE = "oldapi000000000000000000000000000000000000000000000000000000a"
NEW_API_IMAGE = "newapi11111111111111111111111111111111111111111111111111111111111b"
OLD_WEB_IMAGE = "oldweb000000000000000000000000000000000000000000000000000000c"
NEW_WEB_IMAGE = "newweb222222222222222222222222222222222222222222222222222222d"
OLD_LR_IMAGE = "oldlight00000000000000000000000000000000000000000000000000000e"
NEW_LR_IMAGE = "newlight33333333333333333333333333333333333333333333333333333f"


def _write_fake_docker(bin_dir: Path, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "docker"
    shim.write_text("#!/usr/bin/env bash\n" + body + "\n")
    shim.chmod(0o755)
    return bin_dir


def _write_fake_jsonl(tmp_path: Path, lines: list[dict]) -> Path:
    jsonl = tmp_path / "deploy-events.jsonl"
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return jsonl


def _run_watchdog(
    args: list[str],
    bin_dir: Path | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(WATCHDOG_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _fake_docker(
    images: dict[str, str],
    created: dict[str, str],
) -> str:
    """Build a fake docker shim that returns canned inspect/images state.

    Uses case/esac instead of associative arrays for macOS bash 3.2 compat.
    """
    img_cases = ""
    for name, img_id in images.items():
        img_cases += f'        {name}) echo "sha256:{img_id}" ;;\n'
    cr_cases = ""
    for name, cr_val in created.items():
        cr_cases += f'        {name}) echo "{cr_val}" ;;\n'
    json_cases = ""
    for name in images:
        img_id = images[name]
        cr_val = created[name]
        json_cases += (
            f'      {name}) echo \'[{{"Image":"sha256:{img_id}",'
            f'"Created":"{cr_val}"}}]\' ;;\n'
        )
    return f"""\
if [[ "$1" == "images" ]]; then
  for arg in "$@"; do
    case "$arg" in
      nucpot-prod-api*:{DEPLOY_SHA}) echo "sha256:{NEW_API_IMAGE}"; exit 0 ;;
      nucpot-prod-lightrag*:{DEPLOY_SHA}) echo "sha256:{NEW_LR_IMAGE}"; exit 0 ;;
      nucpot-prod-web*:{DEPLOY_SHA}) echo "sha256:{NEW_WEB_IMAGE}"; exit 0 ;;
    esac
  done
  exit 0
fi

if [[ "$1" == "inspect" ]]; then
  name="${{2:-}}"
  if [[ "$*" == *"--format"* ]]; then
    fmt="$2"; shift 2; name="$1"
    case "$fmt" in
      *Image*)
        case "$name" in
{img_cases}          *) echo "" ;;
        esac
        ;;
      *Created*)
        case "$name" in
{cr_cases}          *) echo "" ;;
        esac
        ;;
      *) echo "" ;;
    esac
  else
    case "$name" in
{json_cases}      *) echo '' ;;
    esac
  fi
  exit 0
fi

exit 0
"""


def _docker_all_match() -> str:
    return _fake_docker(
        images={
            "nucpot-prod-api": NEW_API_IMAGE,
            "nucpot-prod-web": NEW_WEB_IMAGE,
            "nucpot-prod-lightrag": NEW_LR_IMAGE,
            "nucpot-prod-worker": NEW_API_IMAGE,
        },
        created={k: "2026-08-18T14:05:00.000000000Z" for k in
               ["nucpot-prod-api", "nucpot-prod-web", "nucpot-prod-lightrag", "nucpot-prod-worker"]},
    )


def _docker_stale() -> str:
    return _fake_docker(
        images={
            "nucpot-prod-api": OLD_API_IMAGE,
            "nucpot-prod-web": OLD_WEB_IMAGE,
            "nucpot-prod-lightrag": OLD_LR_IMAGE,
            "nucpot-prod-worker": OLD_API_IMAGE,
        },
        created={k: "2026-08-18T13:00:00.000000000Z" for k in
               ["nucpot-prod-api", "nucpot-prod-web", "nucpot-prod-lightrag", "nucpot-prod-worker"]},
    )


def _docker_false_positive() -> str:
    """Image mismatch but container created AFTER deploy_ts (AC-3.4)."""
    return _fake_docker(
        images={
            "nucpot-prod-api": OLD_API_IMAGE,
            "nucpot-prod-web": NEW_WEB_IMAGE,
            "nucpot-prod-lightrag": NEW_LR_IMAGE,
            "nucpot-prod-worker": NEW_API_IMAGE,
        },
        created={k: "2026-08-18T15:00:00.000000000Z" for k in
               ["nucpot-prod-api", "nucpot-prod-web", "nucpot-prod-lightrag", "nucpot-prod-worker"]},
    )


# ---------------------------------------------------------------------------
# 1. Usage
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-help"), "exit 0\n")
    result = _run_watchdog(["--help"], bin_dir=bin_dir)
    assert result.returncode == 0
    assert "NFM-3320" in result.stdout or "NFM-3337" in result.stdout


def test_unknown_arg_exits_two():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-bogus"), "exit 0\n")
    result = _run_watchdog(["--bogus"], bin_dir=bin_dir)
    assert result.returncode == 2
    assert "Unknown arg" in result.stderr


# ---------------------------------------------------------------------------
# 2. No JSONL
# ---------------------------------------------------------------------------


def test_missing_jsonl_exits_zero(tmp_path):
    jsonl = tmp_path / "nonexistent.jsonl"
    result = _run_watchdog(["--deploy-jsonl", str(jsonl)])
    assert result.returncode == 0
    assert "nothing to check" in result.stdout


def test_empty_jsonl_exits_zero(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [])
    result = _run_watchdog(["--deploy-jsonl", str(jsonl)])
    assert result.returncode == 0
    assert "empty" in result.stdout


# ---------------------------------------------------------------------------
# 3. All match -> exit 0
# ---------------------------------------------------------------------------


def test_all_match_exits_zero(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [
        {"commit_sha": DEPLOY_SHA, "ts": DEPLOY_TS},
    ])
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-ok"), _docker_all_match())
    result = _run_watchdog(
        ["--deploy-jsonl", str(jsonl)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "No stale" in result.stdout or "matches expected" in result.stdout


# ---------------------------------------------------------------------------
# 4. Stale detected
# ---------------------------------------------------------------------------


def test_stale_detected(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [
        {"commit_sha": DEPLOY_SHA, "ts": DEPLOY_TS},
    ])
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-stale"), _docker_stale())
    result = _run_watchdog(
        ["--deploy-jsonl", str(jsonl)],
        bin_dir=bin_dir,
        env_extra={"ALERT_WEBHOOK": ""},
    )
    assert "STALE" in result.stderr
    assert "nucpot-prod-api" in result.stderr
    assert OLD_API_IMAGE in result.stderr
    assert DEPLOY_SHA in result.stderr


def test_stale_alert_includes_ac33_fields(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [
        {"commit_sha": DEPLOY_SHA, "ts": DEPLOY_TS},
    ])
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-ac33"), _docker_stale())
    result = _run_watchdog(
        ["--deploy-jsonl", str(jsonl)],
        bin_dir=bin_dir,
        env_extra={"ALERT_WEBHOOK": ""},
    )
    # AC-3.3: full container name, running image ID, container Created,
    # expected SHA tag, expected Image ID.
    assert "nucpot-prod-api" in result.stderr
    assert f"sha256:{OLD_API_IMAGE}" in result.stderr
    assert "2026-08-18T13:00:00" in result.stderr
    assert DEPLOY_SHA in result.stderr
    assert NEW_API_IMAGE in result.stderr


# ---------------------------------------------------------------------------
# 5. False-positive guard (AC-3.4)
# ---------------------------------------------------------------------------


def test_false_positive_guard_exits_zero(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [
        {"commit_sha": DEPLOY_SHA, "ts": DEPLOY_TS},
    ])
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-fp"), _docker_false_positive())
    result = _run_watchdog(
        ["--deploy-jsonl", str(jsonl)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "false-positive" in result.stdout.lower() or "AFTER deploy" in result.stdout


# ---------------------------------------------------------------------------
# 6. --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_exits_zero_and_prints_verdict(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [
        {"commit_sha": DEPLOY_SHA, "ts": DEPLOY_TS},
    ])
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-dryrun"), _docker_stale())
    result = _run_watchdog(
        ["--dry-run", "--deploy-jsonl", str(jsonl)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "dry-run" in result.stdout.lower()
    assert "NFM-3320 WATCHDOG" in result.stdout
    assert DEPLOY_SHA in result.stdout


# ---------------------------------------------------------------------------
# 7. No webhook
# ---------------------------------------------------------------------------


def test_no_webhook_prints_to_stderr(tmp_path):
    jsonl = _write_fake_jsonl(tmp_path, [
        {"commit_sha": DEPLOY_SHA, "ts": DEPLOY_TS},
    ])
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-wd-nowh"), _docker_stale())
    result = _run_watchdog(
        ["--deploy-jsonl", str(jsonl)],
        bin_dir=bin_dir,
        env_extra={"ALERT_WEBHOOK": ""},
    )
    assert result.returncode == 0
    assert "ALERT_WEBHOOK not set" in result.stderr
