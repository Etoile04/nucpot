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
          # Echo back the glob PREFIX the caller asked for: everything between
          # "/versions/" and the "*" wildcard. NFM-4125: this must not assume
          # the revision id is hex — NFMD revision ids are slug-style
          # (071_f4_uuid_titled_source_guard), and a hex-only extraction here
          # silently reproduced the very truncation bug under test.
          rev="$(printf '%s' "$arg" | sed -E 's|.*/versions/([^*]*)\*.*|\1|')"
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


# ---------------------------------------------------------------------------
# NFM-4125: slug-style revision ids (the format NFMD actually uses)
# ---------------------------------------------------------------------------
# Every fixture above uses alembic's DEFAULT 12-char hex revision id
# (``001abc01dead``). No NFMD migration has ever used that format — all of
# them are slug-style (``071_f4_uuid_titled_source_guard``). assert.sh parsed
# heads with ``grep -oE '^[0-9a-f]+'``, truncating at the first non-hex char,
# so production deploy 33570937619 reported a bare ``071`` and the real
# revision never appeared in the diagnostic.


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Create a git repo with one commit and return its path."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


def _commit_migration(repo: Path, filename: str) -> None:
    """Add and commit a migration file under apps/api/migrations/versions/."""
    versions = repo / "apps" / "api" / "migrations" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / filename).write_text('"""migration"""\n')
    rel = f"apps/api/migrations/versions/{filename}"
    subprocess.run(["git", "-C", str(repo), "add", rel], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", f"add {filename}"], check=True)


def test_slug_revision_id_on_main_exits_0(tmp_path):
    """A slug-style head that IS on the base ref must pass (exit 0).

    Regression for NFM-4125: the hex-only head parser truncated
    ``071_f4_uuid_titled_source_guard`` to ``071``, so the resolved host path
    became ``.../071.py`` — a file that does not exist — and the gate tripped
    HEAD_FILE_NOT_FOUND on a revision that was present and merged.
    """
    rev = "071_f4_uuid_titled_source_guard"
    repo = _init_repo(tmp_path)
    _commit_migration(repo, f"{rev}.py")

    body = _docker_shim_for_heads(
        [f"{rev} (head)"],
        file_per_head={rev: f"/app/migrations/versions/{rev}.py"},
    )
    bin_dir = _write_fake_docker(tmp_path / "bin-slug-pass", body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (
        f"expected exit 0 for slug revision id; got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "ASSERT_OK" in result.stdout


def test_slug_revision_id_reported_untruncated_in_diagnostic(tmp_path):
    """A genuinely missing slug head must name the FULL revision id.

    The production failure printed ``- 071``, which read as a numbering/race
    problem and sent triage down the wrong path. The diagnostic must carry
    the whole revision so the real cause (image built from a different tree)
    is legible.
    """
    rev = "071_f4_uuid_titled_source_guard"
    repo = _init_repo(tmp_path)  # migration deliberately NOT committed

    body = _docker_shim_for_heads(
        [f"{rev} (head)"],
        file_per_head={rev: f"/app/migrations/versions/{rev}.py"},
    )
    bin_dir = _write_fake_docker(tmp_path / "bin-slug-missing", body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 73, (
        f"expected exit 73; got {result.returncode}\nstderr={result.stderr}"
    )
    assert rev in result.stderr, (
        "diagnostic must name the full revision id, not a truncated prefix.\n"
        f"stderr={result.stderr}"
    )


def test_numeric_prefix_collision_binds_correct_file(tmp_path):
    """Truncation let ``head -1`` bind the WRONG file on a shared prefix.

    With heads parsed as bare ``071``, the in-image glob ``071*.py`` matches
    both ``071_...py`` and ``0710_...py``; ``head -1`` then picks whichever
    sorts first, so the gate can assert ancestry against a migration that is
    not the head at all. The full revision id makes the glob unambiguous.
    """
    rev = "0710_later_migration"
    repo = _init_repo(tmp_path)
    # Only the decoy is committed; the real head's file is absent from the
    # host tree, so a correct gate must FAIL rather than silently bind
    # the decoy and report success.
    _commit_migration(repo, "071_f4_uuid_titled_source_guard.py")

    body = _docker_shim_for_heads(
        [f"{rev} (head)"],
        file_per_head={rev: f"/app/migrations/versions/{rev}.py"},
    )
    bin_dir = _write_fake_docker(tmp_path / "bin-prefix-collision", body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 73, (
        "gate must not bind a prefix-colliding decoy file and pass; "
        f"got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert rev in result.stderr


def test_hex_revision_id_still_supported(tmp_path):
    """Widening the charset must not regress alembic's default hex ids."""
    rev = "054b39a26310"
    repo = _init_repo(tmp_path)
    _commit_migration(repo, f"{rev}_add_source.py")

    body = _docker_shim_for_heads(
        [f"{rev} (head)"],
        file_per_head={rev: f"/app/migrations/versions/{rev}_add_source.py"},
    )
    bin_dir = _write_fake_docker(tmp_path / "bin-hex-pass", body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, (
        f"expected exit 0 for hex revision id; got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_head_revision_with_shell_metacharacters_is_rejected(tmp_path):
    """``rev`` is interpolated into ``sh -c "ls ..."`` — reject metacharacters.

    The old hex-only charset made injection impossible by construction.
    Widening it for slugs must keep that property: no shell or glob
    metacharacter may survive parsing, and a leading ``-`` must never reach
    ``ls`` as a flag.
    """
    repo = _init_repo(tmp_path)
    canary = repo / "pwned"
    body = _docker_shim_for_heads([f"x;touch {canary} (head)"])
    bin_dir = _write_fake_docker(tmp_path / "bin-inject", body)

    result = _run_assert(
        ["--image", "fake:latest", "--base-ref", "HEAD", "--repo-root", str(repo)],
        bin_dir=bin_dir,
    )
    assert not canary.exists(), "shell metacharacter in revision id was executed"
    assert result.returncode != 0
