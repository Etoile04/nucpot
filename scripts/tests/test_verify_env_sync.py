"""Tests for scripts/verify-env-sync.sh — pre-deploy env key drift detector.

Context: NFM-2221 (parent NFM-2213). The MINERU_API_KEY was present in the root
.env.prod but missing from docker/.env.prod for weeks, so the container silently
fell back to PyMuPDF. This script is the guard; these tests are the guard's guard.

The script compares *key names only* — never values — so an accidental secret
leak into CI logs is itself a regression these tests check for.
"""

import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "verify-env-sync.sh"


def run_script(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the script and capture its exit code and streams."""
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def env_pair(tmp_path: Path):
    """Build a root/docker .env.prod pair and return (root_path, docker_path)."""

    def _make(root_content: str, docker_content: str) -> tuple[Path, Path]:
        root_env = tmp_path / ".env.prod"
        docker_dir = tmp_path / "docker"
        docker_dir.mkdir(exist_ok=True)
        docker_env = docker_dir / ".env.prod"
        root_env.write_text(root_content)
        docker_env.write_text(docker_content)
        return root_env, docker_env

    return _make


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"{SCRIPT} does not exist"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"{SCRIPT} is not executable by owner"


# ---------------------------------------------------------------------------
# Core drift detection
# ---------------------------------------------------------------------------


def test_identical_key_sets_exit_zero(env_pair):
    root, docker = env_pair(
        "DATABASE_URL=postgres://root\nREDIS_URL=redis://root\n",
        "DATABASE_URL=postgres://docker\nREDIS_URL=redis://docker\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_key_missing_from_docker_exits_one_and_names_key(env_pair):
    root, docker = env_pair(
        "DATABASE_URL=postgres://root\nMINERU_API_KEY=secret-value-abc123\n",
        "DATABASE_URL=postgres://docker\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 1
    assert "MISSING KEYS in" in result.stderr
    assert "MINERU_API_KEY" in result.stderr


def test_multiple_missing_keys_all_reported(env_pair):
    root, docker = env_pair(
        "A_KEY=1\nB_KEY=2\nC_KEY=3\n",
        "A_KEY=1\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 1
    assert "B_KEY" in result.stderr
    assert "C_KEY" in result.stderr


def test_extra_keys_in_docker_are_allowed(env_pair):
    """Docker-only keys (e.g. container tuning) are not drift — direction matters."""
    root, docker = env_pair(
        "DATABASE_URL=x\n",
        "DATABASE_URL=y\nCONTAINER_ONLY_TUNABLE=z\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Parsing rules
# ---------------------------------------------------------------------------


def test_comments_and_blank_lines_are_ignored(env_pair):
    root, docker = env_pair(
        "# COMMENTED_KEY=should-be-skipped\n\nDATABASE_URL=x\n\n#ANOTHER_COMMENT=y\n",
        "DATABASE_URL=y\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_lowercase_and_indented_lines_are_ignored(env_pair):
    """Only uppercase env-var names anchored at column 0 count as keys."""
    root, docker = env_pair(
        "lowercase_key=x\n  INDENTED_KEY=y\nREAL_KEY=z\n",
        "REAL_KEY=z\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_export_prefixed_lines_do_not_crash(env_pair):
    """`export FOO=bar` is not matched as key FOO; it must not break the run."""
    root, docker = env_pair(
        "export SHELL_STYLE=1\nREAL_KEY=z\n",
        "REAL_KEY=z\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_single_character_key_is_not_silently_skipped(env_pair):
    """A one-char key must still be compared.

    The NFM-2221 sketch used `^[A-Z][A-Z0-9_]+`, which requires two or more
    characters and would drop a key like `X` from the comparison entirely —
    reintroducing the silent-drift bug the script exists to prevent.
    """
    root, docker = env_pair("X=1\nREAL_KEY=z\n", "REAL_KEY=z\n")
    result = run_script(str(root), str(docker))
    assert result.returncode == 1, "single-char key X was silently ignored"
    assert "X" in result.stderr


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_root_file_exits_one_with_clear_message(tmp_path: Path):
    docker_env = tmp_path / "docker.env"
    docker_env.write_text("A=1\n")
    result = run_script(str(tmp_path / "nope.env"), str(docker_env))
    assert result.returncode == 1
    assert "not found" in result.stderr
    assert "nope.env" in result.stderr


def test_missing_docker_file_exits_one_with_clear_message(tmp_path: Path):
    root_env = tmp_path / "root.env"
    root_env.write_text("A=1\n")
    result = run_script(str(root_env), str(tmp_path / "absent.env"))
    assert result.returncode == 1
    assert "not found" in result.stderr
    assert "absent.env" in result.stderr


def test_both_files_empty_exits_zero(env_pair):
    """An empty file yields zero keys — that is in-sync, not a crash."""
    root, docker = env_pair("", "")
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, f"empty files should be in sync; stderr={result.stderr!r}"


def test_empty_docker_file_with_populated_root_exits_one(env_pair):
    root, docker = env_pair("MINERU_API_KEY=abc\n", "")
    result = run_script(str(root), str(docker))
    assert result.returncode == 1
    assert "MINERU_API_KEY" in result.stderr


def test_comment_only_files_exit_zero(env_pair):
    root, docker = env_pair("# nothing here\n", "# nor here\n")
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Invocation contract
# ---------------------------------------------------------------------------


def test_defaults_to_project_root_paths(tmp_path: Path, env_pair):
    """Running `./scripts/verify-env-sync.sh` with no args uses the CWD defaults."""
    env_pair("DATABASE_URL=x\n", "DATABASE_URL=y\n")
    result = run_script(cwd=tmp_path)
    assert result.returncode == 0, result.stderr


def test_defaults_detect_drift_from_project_root(tmp_path: Path, env_pair):
    env_pair("DATABASE_URL=x\nMINERU_API_KEY=abc\n", "DATABASE_URL=y\n")
    result = run_script(cwd=tmp_path)
    assert result.returncode == 1
    assert "MINERU_API_KEY" in result.stderr


# ---------------------------------------------------------------------------
# Security: key names only, never values
# ---------------------------------------------------------------------------


def test_secret_values_never_appear_in_output(env_pair):
    secret = "sk-super-secret-do-not-log"
    root, docker = env_pair(
        f"MINERU_API_KEY={secret}\nDATABASE_URL=postgres://user:hunter2@host/db\n",
        "DATABASE_URL=postgres://other\n",
    )
    result = run_script(str(root), str(docker))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert secret not in combined
    assert "hunter2" not in combined


def test_no_hardcoded_secrets_in_script_source():
    """The executable logic compares names generically — no key allowlist.

    Comments may name MINERU_API_KEY to explain the incident that motivated
    the script; what must not exist is a specific key baked into the code.
    """
    code = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "MINERU_API_KEY" not in code, "script must not hardcode specific keys"
    assert "sk-" not in code


# ---------------------------------------------------------------------------
# Wiring: the deploy stack points at the script
# ---------------------------------------------------------------------------


def test_compose_prod_references_the_script():
    compose = SCRIPT.resolve().parents[1] / "docker-compose.prod.yml"
    assert compose.is_file(), f"{compose} not found"
    head = "".join(compose.read_text().splitlines(keepends=True)[:40])
    assert "./scripts/verify-env-sync.sh" in head, (
        "docker-compose.prod.yml must reference the pre-deploy check near the top"
    )
