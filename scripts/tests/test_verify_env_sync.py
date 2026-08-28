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


@pytest.fixture
def prod_workflow() -> str:
    """The production deployment workflow, as text."""
    workflow = SCRIPT.resolve().parents[1] / ".github" / "workflows" / "production-deployment.yml"
    assert workflow.is_file(), f"{workflow} not found"
    return workflow.read_text()


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
        "DATABASE_URL=postgres://docker\nREDIS_URL=redis://docker\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_key_missing_from_docker_exits_one_and_names_key(env_pair):
    root, docker = env_pair(
        "DATABASE_URL=postgres://root\nMINERU_API_KEY=secret-value-abc123\n",
        "DATABASE_URL=postgres://docker\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 1
    assert "MISSING KEYS in" in result.stderr
    assert "MINERU_API_KEY" in result.stderr


def test_multiple_missing_keys_all_reported(env_pair):
    root, docker = env_pair(
        "A_KEY=1\nB_KEY=2\nC_KEY=3\n",
        "A_KEY=1\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 1
    assert "B_KEY" in result.stderr
    assert "C_KEY" in result.stderr


def test_extra_keys_in_docker_are_allowed(env_pair):
    """Docker-only keys (e.g. container tuning) are not drift — direction matters."""
    root, docker = env_pair(
        "DATABASE_URL=x\n",
        "DATABASE_URL=y\nCONTAINER_ONLY_TUNABLE=z\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Parsing rules
# ---------------------------------------------------------------------------


def test_comments_and_blank_lines_are_ignored(env_pair):
    root, docker = env_pair(
        "# COMMENTED_KEY=should-be-skipped\n\nDATABASE_URL=x\n\n#ANOTHER_COMMENT=y\n",
        "DATABASE_URL=y\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_lowercase_and_indented_lines_are_ignored(env_pair):
    """Only uppercase env-var names anchored at column 0 count as keys."""
    root, docker = env_pair(
        "lowercase_key=x\n  INDENTED_KEY=y\nREAL_KEY=z\n",
        "REAL_KEY=z\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_export_prefixed_lines_do_not_crash(env_pair):
    """`export FOO=bar` is not matched as key FOO; it must not break the run."""
    root, docker = env_pair(
        "export SHELL_STYLE=1\nREAL_KEY=z\n",
        "REAL_KEY=z\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


def test_single_character_key_is_not_silently_skipped(env_pair):
    """A one-char key must still be compared.

    The NFM-2221 sketch used `^[A-Z][A-Z0-9_]+`, which requires two or more
    characters and would drop a key like `X` from the comparison entirely —
    reintroducing the silent-drift bug the script exists to prevent.
    """
    root, docker = env_pair("X=1\nREAL_KEY=z\n", "REAL_KEY=z\nPAPERCLIP_BOARD_API_KEY=dummy\n")
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
    """Root-empty, docker-only-mandatory: zero drift from root, passes sync.

    NFM-3726 added a mandatory docker-only key (PAPERCLIP_BOARD_API_KEY),
    so a truly empty docker env is no longer valid. This test validates
    that root-empty + docker-mandatory-only is still zero-drift."""
    root, docker = env_pair("", "PAPERCLIP_BOARD_API_KEY=dummy\n")
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, f"empty files should be in sync; stderr={result.stderr!r}"


def test_empty_docker_file_with_populated_root_exits_one(env_pair):
    root, docker = env_pair("MINERU_API_KEY=abc\n", "PAPERCLIP_BOARD_API_KEY=dummy\n")
    result = run_script(str(root), str(docker))
    assert result.returncode == 1
    assert "MINERU_API_KEY" in result.stderr


def test_comment_only_files_exit_zero(env_pair):
    root, docker = env_pair("# nothing here\n", "# nor here\nPAPERCLIP_BOARD_API_KEY=dummy\n")
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Invocation contract
# ---------------------------------------------------------------------------


def test_defaults_to_project_root_paths(tmp_path: Path, env_pair):
    """Running `./scripts/verify-env-sync.sh` with no args uses the CWD defaults."""
    env_pair("DATABASE_URL=x\n", "DATABASE_URL=y\nPAPERCLIP_BOARD_API_KEY=dummy\n")
    result = run_script(cwd=tmp_path)
    assert result.returncode == 0, result.stderr


def test_defaults_detect_drift_from_project_root(tmp_path: Path, env_pair):
    env_pair("DATABASE_URL=x\nMINERU_API_KEY=abc\n", "DATABASE_URL=y\nPAPERCLIP_BOARD_API_KEY=dummy\n")
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
        "DATABASE_URL=postgres://other\nPAPERCLIP_BOARD_API_KEY=dummy\n",
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


def test_docker_only_keys_are_reported_but_do_not_fail(env_pair):
    """AC2 asks for drift in *either* direction to be reported.

    NFM-2221 deliberately allows docker-only keys (the container may declare
    tuning knobs the host has no use for), so this stays advisory: named in
    the output, but never a non-zero exit.
    """
    root, docker = env_pair(
        "DATABASE_URL=postgres://root\n",
        "DATABASE_URL=postgres://docker\nDOCKER_ONLY_KNOB=1\nPAPERCLIP_BOARD_API_KEY=dummy\n",
    )
    result = run_script(str(root), str(docker))
    assert result.returncode == 0, result.stderr
    assert "DOCKER_ONLY_KNOB" in result.stdout + result.stderr, (
        "docker-only keys must still be surfaced, even though they pass"
    )


def test_compose_prod_references_the_script():
    compose = SCRIPT.resolve().parents[1] / "docker-compose.prod.yml"
    assert compose.is_file(), f"{compose} not found"
    head = "".join(compose.read_text().splitlines(keepends=True)[:40])
    assert "./scripts/verify-env-sync.sh" in head, (
        "docker-compose.prod.yml must reference the pre-deploy check near the top"
    )


def test_production_workflow_runs_the_check(prod_workflow):
    """A comment in docker-compose is documentation, not enforcement.

    The compose reference above only tells a human to run the check. CI has to
    actually invoke it, or drift reaches production exactly as it did in the
    MINERU_API_KEY incident.
    """
    assert "verify-env-sync.sh" in prod_workflow, (
        "production-deployment.yml must invoke scripts/verify-env-sync.sh"
    )


def test_env_check_gates_the_deploy(prod_workflow):
    """Running the check after deploy-prod would report drift too late."""
    assert prod_workflow.index("verify-env-sync.sh") < prod_workflow.index("  deploy-prod:"), (
        "the env sync check must run before the deploy-prod job"
    )


def test_env_check_runs_on_the_host_holding_the_env_files(prod_workflow):
    """`.env.prod` is gitignored, so it exists only on the production host.

    A fresh ubuntu-latest checkout has neither env file, so the script would
    exit 1 on "file not found" and fail every deploy. The check therefore has
    to run inside the self-hosted runner's job.
    """
    job = prod_workflow[
        prod_workflow.index("  pre-deploy-assert:") : prod_workflow.index(
            "  pre-deploy-assert-smoke:"
        )
    ]
    assert "verify-env-sync.sh" in job, (
        "the env check belongs in the pre-deploy-assert job (self-hosted, production)"
    )
    assert "runs-on: [self-hosted, production]" in job
