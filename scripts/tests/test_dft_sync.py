"""Tests for scripts/dft_sync.sh — DFT result sync from Star-xingyi.

Context: NFM-1978. The script SCPs results.json from the xingyi supercomputer,
runs import_to_db.py to generate SQL, and loads it into the production DB.

All external commands (ssh, scp, python3, docker) are mocked via stub scripts
in a temporary PATH, so tests run fully offline.

Tests cover:
  - No-op when no new remote files exist
  - Happy path: single file pull + import
  - Idempotency: already-processed files are skipped
  - Pre-flight: missing prerequisites cause early exit
  - Import failure: SQL execution error does not record in manifest
  - Dry run mode: no actual commands executed
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "dft_sync.sh"


def _make_stub(tmp_path: Path, name: str, content: str) -> Path:
    """Create an executable stub script that replaces a real command."""
    stub = tmp_path / name
    stub.write_text(content)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _make_mocks(
    tmp_path: Path,
    ssh_output: str = "",
    scp_exit: int = 0,
    python_exit: int = 0,
    docker_exit: int = 0,
    docker_running: bool = True,
) -> dict[str, str]:
    """Create mock command stubs and return PATH override env dict."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # ssh: outputs the remote file listing
    _make_stub(
        bin_dir,
        "ssh",
        f"#!/bin/sh\necho '{ssh_output}'\n",
    )

    # scp: creates a dummy results.json at the target path
    _make_stub(
        bin_dir,
        "scp",
        f'#!/bin/sh\nif [ "$#" -ge 3 ]; then\n'
        f'  echo \'{{"calculation": "test"}}\' > "$3"\n'
        f'fi\nexit {scp_exit}\n',
    )

    # python3: creates a .sql file next to the input
    _make_stub(
        bin_dir,
        "python3",
        f'#!/bin/sh\n'
        f'INPUT="$2"\n'
        f'SQL="${{INPUT%.json}}.sql"\n'
        f'echo "INSERT INTO dft_results (source) VALUES (\'NFM-1540-PathB-Star-xingyi\');" > "$SQL"\n'
        f'exit {python_exit}\n',
    )

    # docker: mock inspect and exec
    inspect_exit = 0 if docker_running else 1
    _make_stub(
        bin_dir,
        "docker",
        f'#!/bin/sh\n'
        f'if [ "$1" = "inspect" ]; then\n'
        f'  exit {inspect_exit}\n'
        f'elif [ "$1" = "exec" ]; then\n'
        f'  exit {docker_exit}\n'
        f'fi\n',
    )

    # flock: always succeeds in single-process tests
    _make_stub(bin_dir, "flock", "#!/bin/sh\nexit 0\n")

    # date: portable timestamp
    _make_stub(bin_dir, "date", "#!/bin/sh\necho 2026-08-04T06:00:00Z\n")

    # Prepend mock bin to real PATH so bash/shell builtins still work
    return {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}



def run_script(
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke dft_sync.sh with mocked environment."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _common_env(
    tmp_path: Path,
    mocks: dict[str, str],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the full set of env overrides for a test."""
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir(exist_ok=True)

    # Create fake source file for scp mock to copy
    (sync_dir / "_fake_source.json").write_text('{"calculation": "test"}')

    processed = tmp_path / "processed.txt"
    processed.touch()

    import_script = tmp_path / "import_to_db.py"
    import_script.write_text("# fake import script\n")

    result = {
        **mocks,
        "DFT_SYNC_PROCESSED_FILE": str(processed),
        "DFT_SYNC_LOCK_FILE": str(tmp_path / "lock"),
        "DFT_SYNC_LOCAL_DIR": str(sync_dir),
        "DFT_SYNC_IMPORT_SCRIPT": str(import_script),
        "DFT_SYNC_DB_CONTAINER": "test-db",
        "DFT_SYNC_DB_USER": "nfm",
        "DFT_SYNC_DB_NAME": "nfm_db",
    }
    if extra:
        result.update(extra)
    return result


# ---------------------------------------------------------------------------
# Test: no-op when no new remote files
# ---------------------------------------------------------------------------


def test_noop_when_no_remote_files(tmp_path: Path) -> None:
    """Script exits 0 and logs no-op when ssh returns no files."""
    mocks = _make_mocks(tmp_path, ssh_output="")
    env = _common_env(tmp_path, mocks)

    result = run_script(env_overrides=env)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "no-op" in result.stdout.lower() or "no new" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test: happy path — single file pull and import
# ---------------------------------------------------------------------------


def test_happy_path_single_file(tmp_path: Path) -> None:
    """Script pulls one new file, generates SQL, and imports it."""
    remote_file = "~/dft_pipeline/scaleup/U-100/results.json"
    mocks = _make_mocks(tmp_path, ssh_output=remote_file)
    env = _common_env(tmp_path, mocks)

    result = run_script(env_overrides=env)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "imported" in result.stdout.lower()

    # Verify processed manifest was updated
    processed = tmp_path / "processed.txt"
    assert remote_file in processed.read_text()


# ---------------------------------------------------------------------------
# Test: idempotency — already-processed file is skipped
# ---------------------------------------------------------------------------


def test_idempotency_skips_processed_file(tmp_path: Path) -> None:
    """Script skips a file already in the processed manifest."""
    remote_file = "~/dft_pipeline/scaleup/U-100/results.json"
    mocks = _make_mocks(tmp_path, ssh_output=remote_file)
    env = _common_env(tmp_path, mocks)

    # Pre-populate the processed manifest
    processed = tmp_path / "processed.txt"
    processed.write_text(remote_file + "\n")

    result = run_script(env_overrides=env)

    assert result.returncode == 0
    assert (
        "no-op" in result.stdout.lower()
        or "no new" in result.stdout.lower()
        or "already processed" in result.stdout.lower()
    )


# ---------------------------------------------------------------------------
# Test: pre-flight failure — docker not running
# ---------------------------------------------------------------------------


def test_preflight_fails_when_docker_down(tmp_path: Path) -> None:
    """Script exits non-zero when the DB container is not running."""
    mocks = _make_mocks(tmp_path, docker_running=False)
    env = _common_env(tmp_path, mocks, {"DFT_SYNC_DB_CONTAINER": "missing-container"})

    result = run_script(env_overrides=env)

    assert result.returncode != 0
    assert (
        "prerequisite" in result.stderr.lower()
        or "not running" in result.stderr.lower()
    )


# ---------------------------------------------------------------------------
# Test: import failure — SQL execution error
# ---------------------------------------------------------------------------


def test_import_failure_records_nothing(tmp_path: Path) -> None:
    """On SQL execution failure, the file is NOT added to the processed manifest."""
    remote_file = "~/dft_pipeline/scaleup/U-200/results.json"
    mocks = _make_mocks(tmp_path, ssh_output=remote_file, docker_exit=1)
    env = _common_env(tmp_path, mocks)

    result = run_script(env_overrides=env)

    # Script should report failure
    assert "failed" in result.stdout.lower() or result.returncode != 0

    # File should NOT be in the processed manifest
    processed = tmp_path / "processed.txt"
    assert remote_file not in processed.read_text()


# ---------------------------------------------------------------------------
# Test: dry run mode
# ---------------------------------------------------------------------------


def test_dry_run_skips_actual_commands(tmp_path: Path) -> None:
    """In dry-run mode, no files are pulled or imported."""
    remote_file = "~/dft_pipeline/scaleup/U-300/results.json"
    mocks = _make_mocks(tmp_path, ssh_output=remote_file)
    env = _common_env(tmp_path, mocks, {"DFT_DRY_RUN": "1"})

    result = run_script(env_overrides=env)

    assert result.returncode == 0
    assert "DRY RUN" in result.stdout

    # Manifest should not be updated
    processed = tmp_path / "processed.txt"
    assert remote_file not in processed.read_text()
