"""Unit tests for tools/post-deploy-cutover-assert/assert.sh — NFM-3320.

Exercises the post-deploy cutover assertion with a fake `docker` shim on
PATH so the script can be tested on ubuntu-latest runners without Docker.
The fake shim returns canned responses keyed on the command line.

The tests cover six groups, mirroring the failure modes in assert.sh:

  1. USAGE / BEFORE  — --help works, unknown args exit 2, --phase before
                       exits 0 and writes the snapshot.
  2. SUCCESS PATH    — every container's image ID matches the expected
                       SHA-tagged image, created timestamps moved
                       forward. Exit 0 + ASSERT_OK banner.
  3. CUTOVER_FAIL    — the NFM-3320 condition. After up -d the running
                       containers are still on the OLD image IDs from
                       the BEFORE snapshot. The script MUST exit 71
                       and log the OLD image IDs so the next incident
                       is debuggable from the workflow log alone.
  4. MISSING_TAG     — the expected SHA tag is not in the local daemon
                       (build failed silently upstream). Exit 73.
  5. NO_RECREATE     — image IDs match but Created timestamps did not
                       move forward (compose renamed in place). Exit 72.
  6. SERVICE_GONE    — a service container is missing after up -d.
                       Exit 74. (Covered indirectly via MISSING checks
                       in the fake-docker helper.)

Companion to assert.sh. The live-Docker integration test lives in
smoke.sh.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
ASSERT_SCRIPT = SCRIPT_DIR / "assert.sh"

EXPECTED_SHA = "abcdef1234567890abcdef1234567890abcdef12"

# Image IDs used across the tests. We reserve sha-prefixed values so
# sub-IDs (first-12-char) are visually distinct and ordering does not
# accidentally collide.
OLD_API_IMAGE = "oldapi000000000000000000000000000000000000000000000000000000000a"
NEW_API_IMAGE = "newapi11111111111111111111111111111111111111111111111111111111111b"
OLD_WEB_IMAGE = "oldweb000000000000000000000000000000000000000000000000000000000c"
NEW_WEB_IMAGE = "newweb222222222222222222222222222222222222222222222222222222222d"
OLD_LR_IMAGE  = "oldlight00000000000000000000000000000000000000000000000000000000e"
NEW_LR_IMAGE  = "newlight33333333333333333333333333333333333333333333333333333333f"
# worker shares the api image (one Dockerfile, two services per compose.yml:227)
NEW_WORKER_IMAGE = NEW_API_IMAGE


# ---------------------------------------------------------------------------
# Fake docker harness — same shape as tools/pre-deploy-assert-smoke.
# ---------------------------------------------------------------------------


def _write_fake_docker(bin_dir: Path, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "docker"
    shim.write_text("#!/usr/bin/env bash\n" + body + "\n")
    shim.chmod(0o755)
    return bin_dir


def _run_assert(args: list[str], bin_dir: Path | None = None, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(ASSERT_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _fake_docker_for_snapshot(services_present: dict[str, str | None], times: dict[str, str] | None = None) -> str:
    """Return a fake docker shim body that returns canned state for each
    service. Times defaults to a single ISO timestamp for every service;
    pass `times={name: iso}` to override per service.
    """
    if times is None:
        times = {n: "2026-08-18T14:00:00.000Z" for n in services_present if services_present[n] is not None}

    missing = [name for name, image in services_present.items() if image is None]
    missing_check = " || ".join(f'[[ "${{1:-}}" == "{n}" ]]' for n in missing) or "false"

    # The bash fake's inspect echo prefixes the value with "sha256:" (a
    # single prefix — that's what real `docker inspect` does). We store
    # the bare hex here so the echo produces "sha256:<hex>", not
    # "sha256:sha256:<hex>" (which would break the case-glob match
    # in the script's assertion loop).
    image_lines = " ; ".join(
        f'__IMAGE["{name}"]="{image}"' for name, image in services_present.items() if image is not None
    )
    created_lines = " ; ".join(
        f'__CREATED["{name}"]="{times.get(name, "2026-08-18T14:00:00.000Z")}"' for name in services_present if services_present[name] is not None
    )

    return f"""\
declare -A __IMAGE
declare -A __CREATED
{image_lines}
{created_lines}

if [[ "$1" == "images" ]]; then
  # `docker images --format '{{.ID}}' repo:tag`
  for arg in "$@"; do
    case "$arg" in
      nucpot-prod-*:{EXPECTED_SHA})
        case "$arg" in
          nucpot-prod-api*) echo "sha256:{NEW_API_IMAGE}" ;;
          nucpot-prod-lightrag*) echo "sha256:{NEW_LR_IMAGE}" ;;
          nucpot-prod-web*) echo "sha256:{NEW_WEB_IMAGE}" ;;
          *) echo "" ;;
        esac
        exit 0 ;;
      *:latest)
        # the 2026-08-18 incident path — only :latest is present
        case "$arg" in
          nucpot-prod-api*) echo "sha256:{OLD_API_IMAGE}" ;;
          nucpot-prod-lightrag*) echo "sha256:{OLD_LR_IMAGE}" ;;
          nucpot-prod-web*) echo "sha256:{OLD_WEB_IMAGE}" ;;
          *) echo "" ;;
        esac
        exit 0 ;;
    esac
  done
  exit 0
fi

if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  # `docker image inspect --format '{{.Id}}' repo:tag` — full sha256:<64hex>,
  # the format assert.sh resolves declared image IDs with (must match the
  # {{.Image}} format written into snapshots).
  for arg in "$@"; do
    case "$arg" in
      nucpot-prod-*:{EXPECTED_SHA})
        case "$arg" in
          nucpot-prod-api*) echo "sha256:{NEW_API_IMAGE}" ;;
          nucpot-prod-lightrag*) echo "sha256:{NEW_LR_IMAGE}" ;;
          nucpot-prod-web*) echo "sha256:{NEW_WEB_IMAGE}" ;;
          *) echo "" ;;
        esac
        exit 0 ;;
    esac
  done
  exit 0
fi

if [[ "$1" == "inspect" ]]; then
  if [[ "$*" != *"--format"* ]]; then
    if {missing_check}; then
      echo "Error: No such object: $2" >&2
      exit 1
    fi
    echo '[{{"Image":"${{__IMAGE[$2]:-}}","Created":"${{__CREATED[$2]:-}}"}}]'
    exit 0
  fi
  fmt="${{2:-}}"
  shift 2
  name="${{1:-}}"
  if {missing_check}; then
    echo "Error: No such object: $name" >&2
    exit 1
  fi
  case "$fmt" in
    *Image*) echo "sha256:${{__IMAGE[$name]:-}}" ;;
    *Created*) echo "${{__CREATED[$name]:-}}" ;;
    *) echo "" ;;
  esac
  exit 0
fi

# ps and anything else: succeed empty
exit 0
"""


# ---------------------------------------------------------------------------
# Usage / argument validation
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-co-help"), "exit 0\n")
    result = _run_assert(["--help"], bin_dir=bin_dir)
    assert result.returncode == 0
    assert "NFM-3320" in result.stdout


def test_missing_phase_exits_two():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-co-no-phase"), "exit 0\n")
    result = _run_assert([], bin_dir=bin_dir)
    assert result.returncode == 2
    assert "--phase" in result.stderr


def test_after_without_expected_tag_exits_two():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-co-no-tag"), "exit 0\n")
    result = _run_assert(["--phase", "after"], bin_dir=bin_dir)
    assert result.returncode == 2
    assert "--expected-tag" in result.stderr


def test_unknown_arg_exits_two():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-co-bogus"), "exit 0\n")
    result = _run_assert(["--phase", "before", "--bogus-flag"], bin_dir=bin_dir)
    assert result.returncode == 2
    assert "Unknown arg" in result.stderr


# ---------------------------------------------------------------------------
# PHASE: before — captures state, exits 0, leaves snapshot behind
# ---------------------------------------------------------------------------


def test_phase_before_writes_snapshot_and_exits_zero(tmp_path):
    snapshot_dir = tmp_path / "snap"
    services = {
        "nucpot-prod-api": OLD_API_IMAGE,
        "nucpot-prod-web": OLD_WEB_IMAGE,
        "nucpot-prod-lightrag": OLD_LR_IMAGE,
        "nucpot-prod-worker": OLD_API_IMAGE,
    }
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-co-before"),
        _fake_docker_for_snapshot(services),
    )
    result = _run_assert(
        ["--phase", "before", "--snapshot-dir", str(snapshot_dir)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "BEFORE snapshot written" in result.stdout
    assert (snapshot_dir / "before.txt").exists()


# ---------------------------------------------------------------------------
# PHASE: after — SUCCESS path. Every container's image ID matches the
# SHA-tagged image that the deploy step just built.
# ---------------------------------------------------------------------------


def test_phase_after_success_when_all_containers_recreated(tmp_path):
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    before_lines = [
        f"nucpot-prod-api|sha256:{OLD_API_IMAGE}|2026-08-18T13:00:00.000Z",
        f"nucpot-prod-web|sha256:{OLD_WEB_IMAGE}|2026-08-18T13:00:00.000Z",
        f"nucpot-prod-lightrag|sha256:{OLD_LR_IMAGE}|2026-08-18T13:00:00.000Z",
        f"nucpot-prod-worker|sha256:{OLD_API_IMAGE}|2026-08-18T13:00:00.000Z",
    ]
    (snapshot_dir / "before.txt").write_text("\n".join(before_lines) + "\n")

    services = {
        "nucpot-prod-api": NEW_API_IMAGE,
        "nucpot-prod-web": NEW_WEB_IMAGE,
        "nucpot-prod-lightrag": NEW_LR_IMAGE,
        "nucpot-prod-worker": NEW_WORKER_IMAGE,
    }
    # AFTER snapshot uses a Created timestamp that is strictly later than
    # the BEFORE one — this satisfies the recreate-detection branch.
    times = {n: "2026-08-18T14:30:00.000Z" for n in services}
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-co-success"),
        _fake_docker_for_snapshot(services, times=times),
    )

    result = _run_assert(
        [
            "--phase", "after",
            "--expected-tag", EXPECTED_SHA,
            "--snapshot-dir", str(snapshot_dir),
        ],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "ASSERT_OK" in result.stdout
    assert EXPECTED_SHA in result.stdout


# ---------------------------------------------------------------------------
# NFM-3320 AC-4 — the regression case the test asserts against.
#
# This is the literal 2026-08-18 incident: the BEFORE snapshot captures
# the old image IDs. After `docker compose up -d`, the running containers
# are STILL on those old IDs (compose skipped reconcile). The script must
# exit 71, log the old image IDs (so the next incident is debuggable
# from the workflow log alone), and explicitly blame "deploy reported
# success but the old containers are still serving traffic."
# ---------------------------------------------------------------------------


def test_phase_after_fails_when_running_image_unchanged_from_before(tmp_path):
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    before_lines = [
        f"nucpot-prod-api|sha256:{OLD_API_IMAGE}|2026-08-18T13:00:00.000Z",
        f"nucpot-prod-web|sha256:{OLD_WEB_IMAGE}|2026-08-18T13:00:00.000Z",
        f"nucpot-prod-lightrag|sha256:{OLD_LR_IMAGE}|2026-08-18T13:00:00.000Z",
        f"nucpot-prod-worker|sha256:{OLD_API_IMAGE}|2026-08-18T13:00:00.000Z",
    ]
    (snapshot_dir / "before.txt").write_text("\n".join(before_lines) + "\n")

    # Fake docker returns the SAME image IDs as BEFORE — the failure mode.
    services = {
        "nucpot-prod-api": OLD_API_IMAGE,
        "nucpot-prod-web": OLD_WEB_IMAGE,
        "nucpot-prod-lightrag": OLD_LR_IMAGE,
        "nucpot-prod-worker": OLD_API_IMAGE,
    }
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-co-no-cutover"),
        _fake_docker_for_snapshot(services),
    )

    result = _run_assert(
        [
            "--phase", "after",
            "--expected-tag", EXPECTED_SHA,
            "--snapshot-dir", str(snapshot_dir),
        ],
        bin_dir=bin_dir,
    )
    assert result.returncode == 71, (
        f"expected 71 (CUTOVER_FAIL), got {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "nucpot-prod-api" in result.stderr
    assert "ASSERT_FAIL" in result.stderr
    assert "NFM-3320" in result.stderr
    # Full-length image sha must surface in the log so the next incident
    # is debuggable from the workflow log alone (NFM-3320 AC-2).
    assert OLD_API_IMAGE in result.stderr


# ---------------------------------------------------------------------------
# NFM-3320 AC-4 edge case — the build step failed silently and the
# expected SHA tag is not in the local daemon. Hard-fail (exit 73),
# do NOT silently degrade to "service is healthy".
# ---------------------------------------------------------------------------


def test_phase_after_fails_when_expected_tag_missing_from_daemon(tmp_path):
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    (snapshot_dir / "before.txt").write_text(
        f"nucpot-prod-api|sha256:{OLD_API_IMAGE}|2026-08-18T13:00:00.000Z\n"
        f"nucpot-prod-web|sha256:{OLD_WEB_IMAGE}|2026-08-18T13:00:00.000Z\n"
        f"nucpot-prod-lightrag|sha256:{OLD_LR_IMAGE}|2026-08-18T13:00:00.000Z\n"
        f"nucpot-prod-worker|sha256:{OLD_API_IMAGE}|2026-08-18T13:00:00.000Z\n"
    )

    # `docker images nucpot-prod-*:SHA` returns nothing (build failed).
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-co-no-tag"),
        """\
if [[ "$1" == "images" ]]; then
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  if [[ "$*" != *"--format"* ]]; then
    echo '[]'
    exit 0
  fi
  shift 2
  case "$2" in
    *) echo "sha256:placeholder" ;;
  esac
  exit 0
fi
exit 0
""",
    )

    result = _run_assert(
        [
            "--phase", "after",
            "--expected-tag", EXPECTED_SHA,
            "--snapshot-dir", str(snapshot_dir),
        ],
        bin_dir=bin_dir,
    )
    assert result.returncode == 73, (result.stdout, result.stderr)
    assert "MISSING_TAG" in result.stderr or "not found in local daemon" in result.stderr
    assert EXPECTED_SHA in result.stderr


# ---------------------------------------------------------------------------
# NO_RECREATE — image IDs match the SHA, but Created timestamps are
# unchanged (compose renamed the container in place rather than spinning
# up a new one). Exit 72.
# ---------------------------------------------------------------------------


def test_phase_after_fails_when_created_timestamp_did_not_move(tmp_path):
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    static_ts = "2026-08-18T13:00:00.000Z"
    before_lines = [
        f"nucpot-prod-api|sha256:{OLD_API_IMAGE}|{static_ts}",
        f"nucpot-prod-web|sha256:{OLD_WEB_IMAGE}|{static_ts}",
        f"nucpot-prod-lightrag|sha256:{OLD_LR_IMAGE}|{static_ts}",
        f"nucpot-prod-worker|sha256:{OLD_API_IMAGE}|{static_ts}",
    ]
    (snapshot_dir / "before.txt").write_text("\n".join(before_lines) + "\n")

    # AFTER: NEW image IDs reported by `docker images`, but the
    # Container's Created timestamp is the SAME as BEFORE (compose renamed
    # in place rather than starting a new container). The fake honors the
    # per-service `times` map for Created timestamps.
    services = {
        "nucpot-prod-api": NEW_API_IMAGE,
        "nucpot-prod-web": NEW_WEB_IMAGE,
        "nucpot-prod-lightrag": NEW_LR_IMAGE,
        "nucpot-prod-worker": NEW_WORKER_IMAGE,
    }
    # CRITICAL: every AFTER Created timestamp equals the BEFORE one.
    times = {n: static_ts for n in services}
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-co-no-recreate"),
        _fake_docker_for_snapshot(services, times=times),
    )

    result = _run_assert(
        [
            "--phase", "after",
            "--expected-tag", EXPECTED_SHA,
            "--snapshot-dir", str(snapshot_dir),
        ],
        bin_dir=bin_dir,
    )
    assert result.returncode == 72, (result.stdout, result.stderr)
    assert "NO_RECREATE" in result.stderr or "did not move forward" in result.stderr


# ---------------------------------------------------------------------------
# MISSING BEFORE-SNAPSHOT
# ---------------------------------------------------------------------------


def test_phase_after_fails_when_before_snapshot_missing(tmp_path):
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    # Intentionally do NOT write before.txt.
    services = {
        "nucpot-prod-api": NEW_API_IMAGE,
        "nucpot-prod-web": NEW_WEB_IMAGE,
        "nucpot-prod-lightrag": NEW_LR_IMAGE,
        "nucpot-prod-worker": NEW_WORKER_IMAGE,
    }
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-co-no-before"),
        _fake_docker_for_snapshot(services),
    )
    result = _run_assert(
        [
            "--phase", "after",
            "--expected-tag", EXPECTED_SHA,
            "--snapshot-dir", str(snapshot_dir),
        ],
        bin_dir=bin_dir,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "BEFORE snapshot not found" in result.stderr
