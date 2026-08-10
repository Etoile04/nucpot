"""Unit tests for tools/migration-on-main-assert/assert.sh.

Exercises the bash assert script with a fake `docker` shim on PATH so the
script can be tested on ubuntu-latest runners without Docker. The fake shim
returns canned responses keyed on the command line, letting each test set up
its own scenario (heads-on-main, heads-not-on-main, override, missing file,
bad git ref).

Companion to smoke.sh (Docker integration). Together they form the
NFM-2141 regression test for ADR-NFM-2139 §5 D4.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
ASSERT_SCRIPT = SCRIPT_DIR / "assert.sh"


# ---------------------------------------------------------------------------
# Fake-docker shim helpers
# ---------------------------------------------------------------------------


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


DOCKER_SHIM_TEMPLATE = r"""#!/usr/bin/env bash
set -e
case "$1" in
  run)
    # Inspect every arg to decide what to return.
    found_alembic=0
    found_ls=0
    rev=""
    for arg in "$@"; do
      case "$arg" in
        *alembic*heads*)
          found_alembic=1
          ;;
        */app/migrations/versions/*)
          found_ls=1
          # Extract revision ID (hex chars before the first non-hex).
          rev="$(printf '%s' "$arg" | sed -E 's|.*/versions/([0-9a-f]+).*|\1|')"
          ;;
      esac
    done
    if [ "$found_alembic" = "1" ]; then
      # One head line per entry in the block (already newline-terminated by the
      # Python substitution). assert.sh captures stdout into a command
      # substitution which strips trailing newlines.
      printf '__HEADS_BLOCK__'
      exit 0
    fi
    if [ "$found_ls" = "1" ] && [ -n "$rev" ]; then
      case "$rev" in
__REV_CASES__
        *) printf '/app/migrations/versions/%s.py' "$rev" ;;
      esac
      exit 0
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


def _docker_shim_for_heads(head_lines: list[str], file_per_head: dict[str, str] | None = None) -> str:
    """Return a fake-docker shim body that yields the given alembic-head lines
    and looks up the corresponding image-file path for each revision.

    ``file_per_head`` maps revision -> image-file path (e.g.
    ``"/app/migrations/versions/054b39a26310_add_source.py"``). When omitted,
    the shim returns ``/app/migrations/versions/<rev>.py`` (the merge-style
    file layout).
    """
    per_head = file_per_head or {}
    rev_cases = "\n".join(
        f"        {rev}) printf '{path}' ;;"
        for rev, path in per_head.items()
    )
    heads_block = "\\n".join(head_lines)
    body = DOCKER_SHIM_TEMPLATE.replace("__HEADS_BLOCK__", heads_block)
    body = body.replace("__REV_CASES__", rev_cases)
    return body


def _run_assert(args: list[str], bin_dir: Path | None = None, env_extra: dict | None = None,
                cwd: Path | None = None) -> subprocess.CompletedProcess:
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
        cwd=str(cwd) if cwd else None,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Usage / argument validation
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result = _run_assert(["--help"])
    assert result.returncode == 0
    assert "NFM-2141" in result.stdout or "alembic" in result.stdout


def test_missing_image_arg_exits_72():
    result = _run_assert([])
    assert result.returncode == 72
    assert "--image is required" in result.stderr


def test_unknown_arg_exits_72():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-noop"), "exit 0\n")
    result = _run_assert(["--image", "fake", "--bogus-flag"], bin_dir=bin_dir)
    assert result.returncode == 72
    assert "Unknown arg" in result.stderr


def test_invalid_base_ref_exits_72(tmp_path):
    """A non-existent --base-ref must be rejected with exit 72 (USAGE)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-badref"), "exit 0\n")
    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "refs/heads/does-not-exist",
         "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 72
    assert "not a valid ref" in result.stderr


# ---------------------------------------------------------------------------
# Success path: every alembic head's file-commit is on origin/main
# ---------------------------------------------------------------------------


def test_all_heads_on_main_exits_0(tmp_path):
    """Synthetic migration on a branch that IS merged — gate passes (exit 0).

    Mirrors NFM-2141 AC: "a merged-to-main revision passes".
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    (repo / "apps" / "api" / "migrations" / "versions").mkdir(parents=True)
    mig = repo / "apps" / "api" / "migrations" / "versions" / "001abc01dead.py"
    mig.write_text("\"\"\"init\"\"\"\n")
    subprocess.run(["git", "-C", str(repo), "add", "apps/api/migrations/versions/001abc01dead.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "add migration"], check=True)
    heads = ["001abc01dead (head)"]
    body = _docker_shim_for_heads(heads, file_per_head={"001abc01dead": "/app/migrations/versions/001abc01dead.py"})
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-pass"), body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (
        f"expected exit 0 (all heads on main); got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "ASSERT_OK" in result.stdout


# ---------------------------------------------------------------------------
# Failure path: head's file-commit is NOT on origin/main
# ---------------------------------------------------------------------------


def test_head_not_on_main_exits_70(tmp_path):
    """Synthetic migration on a throwaway branch — gate blocks (exit 70).

    Mirrors NFM-2141 AC: "a synthetic migration on a throwaway branch fails
    the gate with a deterministic error".
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "NFM-9999-feature"], check=True)
    (repo / "apps" / "api" / "migrations" / "versions").mkdir(parents=True)
    mig = repo / "apps" / "api" / "migrations" / "versions" / "034_unmerged.py"
    mig.write_text("\"\"\"hotfix migration\"\"\"\n")
    subprocess.run(["git", "-C", str(repo), "add", "apps/api/migrations/versions/034_unmerged.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "NFM-9999 add hotfix migration"], check=True)
    heads = ["034abcdef0123 (head)"]
    body = _docker_shim_for_heads(heads, file_per_head={"034abcdef0123": "/app/migrations/versions/034_unmerged.py"})
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-fail"), body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "main", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 70, (
        f"expected exit 70 (HEAD_NOT_ON_REF); got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "ASSERT_FAIL" in result.stderr
    assert "NFM-2136" in result.stderr
    assert "NOT on main" in result.stderr


# ---------------------------------------------------------------------------
# Override path
# ---------------------------------------------------------------------------


def test_override_rationale_exits_71_and_writes_audit_log(tmp_path):
    """Override with non-empty rationale -> exit 71, audit log row appended."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "NFM-9999-feature"], check=True)
    (repo / "apps" / "api" / "migrations" / "versions").mkdir(parents=True)
    mig = repo / "apps" / "api" / "migrations" / "versions" / "034_unmerged.py"
    mig.write_text("\"\"\"hotfix migration\"\"\"\n")
    subprocess.run(["git", "-C", str(repo), "add", "apps/api/migrations/versions/034_unmerged.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "NFM-9999 add hotfix migration"], check=True)

    audit_log = tmp_path / "audit.jsonl"
    heads = ["034abcdef0123 (head)"]
    body = _docker_shim_for_heads(heads, file_per_head={"034abcdef0123": "/app/migrations/versions/034_unmerged.py"})
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-override"), body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "main", "--repo-root", str(repo),
         "--override-rationale", "NFM-9999 hotfix — branch will be merged to main within 30 min",
         "--audit-log", str(audit_log)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 71, (
        f"expected exit 71 (OVERRIDE_APPLIED); got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert audit_log.exists(), "override must append an audit row"
    rows = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    assert len(rows) == 1, f"expected exactly one audit row, got {len(rows)}"
    row = rows[0]
    for key in ("ts", "image", "base_ref", "not_on_ref", "failure_fingerprint", "rationale"):
        assert key in row, f"audit row missing key '{key}'"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", row["ts"]), \
        f"ts must be ISO 8601 UTC, got {row['ts']!r}"
    assert row["image"] == "fake:latest"
    assert row["base_ref"] == "main"
    assert row["not_on_ref"] == "034abcdef0123"
    assert "NFM-9999" in row["rationale"]


# ---------------------------------------------------------------------------
# Failure path: file present in image but not in host working tree
# ---------------------------------------------------------------------------


def test_head_file_missing_in_host_exits_73(tmp_path):
    """Image has the revision file but the host working tree does not — exit 73."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    heads = ["034abcdef0123 (head)"]
    body = _docker_shim_for_heads(
        heads,
        file_per_head={"034abcdef0123": "/app/migrations/versions/034_missing_in_host.py"},
    )
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-missing"), body)
    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 73, (
        f"expected exit 73 (HEAD_FILE_NOT_FOUND); got {result.returncode}\n"
        f"stderr={result.stderr}"
    )
    assert "HEAD_FILE_NOT_FOUND" in result.stderr


# ---------------------------------------------------------------------------
# Failure path: alembic heads output is empty
# ---------------------------------------------------------------------------


def test_empty_alembic_heads_exits_73(tmp_path):
    """Image returns empty alembic heads -> exit 73 (HEAD_READ_FAIL)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    body = """
case "$1" in
  run) exit 0 ;;
  *) exit 0 ;;
esac
"""
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-moma-empty"), body)
    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 73
    assert "HEAD_READ_FAIL" in result.stderr or "HEAD_PARSE_FAIL" in result.stderr