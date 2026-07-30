"""Unit tests for tools/pre-deploy-assert-smoke/assert.sh.

Exercises the bash assert script with a fake `docker` shim on PATH so the
script can be tested on ubuntu-latest runners without Docker. The fake shim
returns canned responses keyed on the command line, letting each test set up
its own scenario (missing-revision, multi-head, success, DB-down).

Companion to smoke.sh, which runs the script against real Docker + postgres.
Together they form the NFM-2149 regression test for ADR-NFM-2139 §5 D2.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
ASSERT_SCRIPT = SCRIPT_DIR / "assert.sh"


def _write_fake_docker(bin_dir: Path, body: str) -> Path:
    """Write a fake `docker` executable to ``bin_dir/docker`` and return the dir."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "docker"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"{body}\n"
    )
    shim.chmod(0o755)
    return bin_dir


def _run_assert(args: list[str], bin_dir: Path | None = None, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke assert.sh with an optional fake docker on PATH."""
    env = os.environ.copy()
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(ASSERT_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Usage / argument validation
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result = _run_assert(["--help"])
    assert result.returncode == 0
    assert "Pre-deploy" in result.stdout or "ADR" in result.stdout


def test_missing_image_arg_exits_nonzero():
    result = _run_assert([])
    assert result.returncode != 0
    assert "--image is required" in result.stderr


def test_unknown_arg_exits_two():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-test-noop"), "exit 0\n")
    result = _run_assert(["--image", "fake", "--bogus-flag"], bin_dir=bin_dir)
    assert result.returncode == 2
    assert "Unknown arg" in result.stderr


# ---------------------------------------------------------------------------
# Success path: DB has X, image has X, single head
# ---------------------------------------------------------------------------


def test_success_when_revision_present_and_single_head():
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-test-success"),
        """\
if [[ "$*" == *"psql"* ]]; then
    echo "abcd1234abcd"
elif [[ "$*" == *"alembic heads"* ]]; then
    echo "abcd1234abcd (head)"
elif [[ "$*" == *"migrations/versions/abcd1234"* ]]; then
    echo "/app/migrations/versions/abcd1234abcd_create_foo.py"
fi
""",
    )
    result = _run_assert(
        ["--image", "fake:ok", "--db-container", "pg"],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (
        f"expected 0, got {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "ASSERT_OK" in result.stdout
    assert "abcd1234abcd" in result.stdout


# ---------------------------------------------------------------------------
# NFM-2135 condition: DB has X, image lacks X → distinct exit + log names X
# ---------------------------------------------------------------------------


def test_missing_revision_returns_distinct_exit_and_names_revision():
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-test-missing"),
        """\
if [[ "$*" == *"psql"* ]]; then
    echo "9999999999_phantom"
elif [[ "$*" == *"alembic heads"* ]]; then
    echo "abcd1234abcd (head)"
# The ls of the missing-revision file returns nothing (success, empty).
fi
""",
    )
    result = _run_assert(
        [
            "--image", "fake:missing",
            "--db-container", "pg",
            "--distinct-exit", "42",
        ],
        bin_dir=bin_dir,
    )
    assert result.returncode == 42, (
        f"expected 42 (distinct), got {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # Error log must name the missing revision so operators can diagnose.
    assert "9999999999_phantom" in result.stderr
    assert "ASSERT_FAIL" in result.stderr
    assert "NFM-2135" in result.stderr


# ---------------------------------------------------------------------------
# DB unreachable → exit 66
# ---------------------------------------------------------------------------


def test_db_unreachable_returns_exit_66():
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-test-dbdown"),
        "exit 1\n",  # every docker call fails
    )
    result = _run_assert(
        ["--image", "fake:x", "--db-container", "pg"],
        bin_dir=bin_dir,
    )
    assert result.returncode == 66
    assert "DB_READ_FAIL" in result.stderr


# ---------------------------------------------------------------------------
# Multi-head → exit 65 (forked migration graph, NFM-167)
# ---------------------------------------------------------------------------


def test_multi_head_returns_exit_65():
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-test-multihead"),
        """\
if [[ "$*" == *"psql"* ]]; then
    echo "abcd1234abcd"
elif [[ "$*" == *"alembic heads"* ]]; then
    echo "head_a_aaaa (head)"
    echo "head_b_bbbb (head)"
elif [[ "$*" == *"migrations/versions/abcd1234"* ]]; then
    echo "/app/migrations/versions/abcd1234abcd_create_foo.py"
fi
""",
    )
    result = _run_assert(
        ["--image", "fake:multi", "--db-container", "pg"],
        bin_dir=bin_dir,
    )
    assert result.returncode == 65
    assert "forked migration graph" in result.stderr
    assert "2 head(s)" in result.stderr


def test_multi_head_with_no_strict_warns_but_exits_zero():
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-test-no-strict"),
        """\
if [[ "$*" == *"psql"* ]]; then
    echo "abcd1234abcd"
elif [[ "$*" == *"alembic heads"* ]]; then
    echo "head_a_aaaa (head)"
    echo "head_b_bbbb (head)"
elif [[ "$*" == *"migrations/versions/abcd1234"* ]]; then
    echo "/app/migrations/versions/abcd1234abcd_create_foo.py"
fi
""",
    )
    result = _run_assert(
        ["--image", "fake:multi", "--db-container", "pg", "--no-strict-heads"],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0
    assert "WARN" in result.stderr
    assert "2 head(s)" in result.stderr
