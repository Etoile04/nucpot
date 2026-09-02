"""NFM-4197 — staging forwarding of ``NFMD_PREVIEW_DB_PASSWORD``.

Migration 073 (``create_nfm_preview_role``) hard-fails with a
``RuntimeError`` when ``NFMD_PREVIEW_DB_PASSWORD`` is unset, and the
staging api container receives its environment ONLY via the generated
``docker/.env.staging.api``. Before NFM-4197, ``write_api_env_file()``
in ``scripts/staging_deploy.sh`` forwarded a fixed key list that did
not include the password — wiring existed only for CI (``ci.yml``
fixture) and the prod deploy workflow (``docker/.env.prod``). Any fresh
staging DB (volume rebuild) at alembic < 073 crash-looped the api
container on boot and auto-rolled-back at the deploy health gate;
NFM-4190 worked around it with a one-time manual ``docker run``
pre-migration.

These tests pin the config half so that cannot recur:

* ``docker/.env.staging.example`` documents the key (placeholder value;
  staging may use an independent password from prod).
* ``write_api_env_file()`` forwards the value VERBATIM from
  ``docker/.env.staging`` using the same grep-raw pattern NFM-4077
  introduced for CORS (and NFM-4170 reused for the canonicals) —
  grepping the raw line, never the shell-sourced variable, so quotes /
  ``$`` / backslashes survive into the generated file.
* An empty default keeps deploys of staging DBs already past 073
  working — the migration itself is the single enforcement point.

NFM-4215 adds the mode half: the generated file also carries
``NFM_SECRET_KEY`` / ``NFM_DATABASE_URL`` / ``NFMD_PREVIEW_DB_PASSWORD``,
so it must land 0600 — enforced by an explicit ``chmod 600`` (a creation-
time umask cannot tighten a pre-existing 0644 file, which is what every
host deployed before NFM-4215 has on disk).

Mirrors ``test_nfm4170_canonical_config.py`` (PR #1115).
"""

from __future__ import annotations

import re
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

# Repo-rooted absolute paths so the test is independent of CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]
STAGING_DEPLOY_SH = REPO_ROOT / "scripts" / "staging_deploy.sh"
DOCKER_STAGING_ENV_EXAMPLE = REPO_ROOT / "docker" / ".env.staging.example"

ENV_VAR_NAME = "NFMD_PREVIEW_DB_PASSWORD"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ``KEY=VALUE`` shell-style, matching how Docker parses env files.
_ENV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _parse_env_text(text: str) -> dict[str, str]:
    """Return ``{KEY: raw_value}`` for every non-comment ``KEY=VALUE`` line."""
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _ENV_LINE_RE.match(raw)
        if match is None:
            continue
        parsed[match.group(1)] = match.group(2)
    return parsed


def _extract_write_api_env_file() -> str:
    """Extract the ``write_api_env_file`` function source from the deploy script.

    Sourcing the whole script is unsafe — it ends with ``main "$@"`` which
    dispatches on argv. The function is top-level and ends at the first
    column-0 ``}``, so an anchored non-greedy regex captures exactly it.
    """
    assert STAGING_DEPLOY_SH.exists(), f"{STAGING_DEPLOY_SH} missing"
    text = STAGING_DEPLOY_SH.read_text(encoding="utf-8")
    match = re.search(
        r"^write_api_env_file\(\) \{.*?^\}",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "write_api_env_file() not found in staging_deploy.sh"
    return match.group(0)


def _run_write_api_env_file(
    tmp_path: Path,
    env_file_lines: list[str],
    precreate_mode: int | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Execute the extracted function against a sandbox env file.

    Returns the bash process result and the content of the generated
    ``docker/.env.staging.api`` (empty string when the function died
    before writing it). ``precreate_mode`` optionally plants the
    generated file first with that mode — the on-disk state of every
    host deployed before NFM-4215 (``>`` truncates without chmod'ing).
    """
    project_root = tmp_path / "project"
    docker_dir = project_root / "docker"
    docker_dir.mkdir(parents=True)
    env_file = docker_dir / ".env.staging"
    env_file.write_text("\n".join(env_file_lines) + "\n", encoding="utf-8")
    if precreate_mode is not None:
        stale = docker_dir / ".env.staging.api"
        stale.write_text("STALE=1\n", encoding="utf-8")
        stale.chmod(precreate_mode)

    harness = "\n".join(
        [
            "set -euo pipefail",
            # Real deploys run under the typical daemon umask; pin it so a
            # developer running pytest under a stricter umask can't mask a
            # missing chmod in write_api_env_file() (NFM-4215).
            "umask 022",
            "log() { :; }",
            "warn() { :; }",
            "die() { printf 'DIE: %s\\n' \"$*\" >&2; exit 1; }",
            f"PROJECT_ROOT={shlex.quote(str(project_root))}",
            f"ENV_FILE={shlex.quote(str(env_file))}",
            "STAGING_DATABASE_URL=postgresql+asyncpg://nfm:test@db:5432/nfm_db",
            "STAGING_API_SECRET_KEY=test-secret",
            _extract_write_api_env_file(),
            "write_api_env_file",
        ]
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    generated = docker_dir / ".env.staging.api"
    content = generated.read_text(encoding="utf-8") if generated.exists() else ""
    return proc, content


# A password exercising the characters that quote-stripping / re-expansion
# would corrupt: double quotes, single quotes, ``$``, backslash, spaces.
_TRICKY_PASSWORD = 'p@ss "w0rd" \'$x\\ y!'


# ---------------------------------------------------------------------------
# Tests — env template
# ---------------------------------------------------------------------------


def test_staging_env_example_documents_preview_password() -> None:
    """The staging template MUST declare the key with a non-empty placeholder.

    Operators copy this file to ``docker/.env.staging``; an undocumented
    key is exactly how NFM-4190's manual workaround became necessary.
    """
    if not DOCKER_STAGING_ENV_EXAMPLE.exists():  # pragma: no cover
        pytest.skip(f"{DOCKER_STAGING_ENV_EXAMPLE} not present in this checkout")
    parsed = _parse_env_text(DOCKER_STAGING_ENV_EXAMPLE.read_text(encoding="utf-8"))
    assert ENV_VAR_NAME in parsed, (
        f"{DOCKER_STAGING_ENV_EXAMPLE.name}: missing {ENV_VAR_NAME}; without it "
        f"a fresh staging DB cannot run migration 073 (NFM-4197)."
    )
    assert parsed[ENV_VAR_NAME].strip() != "", (
        f"{DOCKER_STAGING_ENV_EXAMPLE.name}: {ENV_VAR_NAME} must carry a "
        f"placeholder value, not an empty default."
    )


# ---------------------------------------------------------------------------
# Tests — write_api_env_file() behavior
# ---------------------------------------------------------------------------


def test_write_api_env_file_forwards_password_verbatim(tmp_path: Path) -> None:
    """The generated api env file must carry the password byte-for-byte.

    The grep-raw pattern (NFM-4077) is load-bearing: sourcing the env
    file through bash would strip the inner quotes and re-expand ``$``
    sequences, corrupting the password before it reaches the container.
    """
    proc, content = _run_write_api_env_file(
        tmp_path,
        [f"{ENV_VAR_NAME}={_TRICKY_PASSWORD}"],
    )
    assert proc.returncode == 0, f"write_api_env_file died: {proc.stderr}"
    parsed = _parse_env_text(content)
    assert parsed.get(ENV_VAR_NAME) == _TRICKY_PASSWORD, (
        f"password not forwarded verbatim — expected {_TRICKY_PASSWORD!r}, "
        f"generated file contains {parsed.get(ENV_VAR_NAME)!r}:\n{content}"
    )


def test_write_api_env_file_empty_default_when_key_absent(tmp_path: Path) -> None:
    """No key in docker/.env.staging => empty value, function still succeeds.

    A staging DB already past migration 073 never needs the password, so
    the forwarding layer must not hard-fail; migration 073 itself raises
    the loud RuntimeError when the var is genuinely required.
    """
    proc, content = _run_write_api_env_file(tmp_path, ["STAGING_DEBUG=false"])
    assert proc.returncode == 0, (
        f"write_api_env_file must not die when {ENV_VAR_NAME} is absent "
        f"(DBs past 073 don't need it): {proc.stderr}"
    )
    parsed = _parse_env_text(content)
    assert parsed.get(ENV_VAR_NAME) == "", (
        f"expected empty default, got {parsed.get(ENV_VAR_NAME)!r}:\n{content}"
    )


def test_write_api_env_file_preserves_existing_keys(tmp_path: Path) -> None:
    """Adding the new key must not disturb the pre-existing forwarding.

    Regression guard: the heredoc is the single env surface for the api
    container; losing any previously-forwarded key would break unrelated
    behavior (CORS parsing, canonicals filter).
    """
    proc, content = _run_write_api_env_file(
        tmp_path,
        [
            f"{ENV_VAR_NAME}=some-password",
            'STAGING_CORS_ORIGINS=["https://staging.example.org"]',
            "STAGING_NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS=id1,id2",
        ],
    )
    assert proc.returncode == 0, f"write_api_env_file died: {proc.stderr}"
    parsed = _parse_env_text(content)
    assert parsed["NFM_DATABASE_URL"] == "postgresql+asyncpg://nfm:test@db:5432/nfm_db"
    assert parsed["NFM_SECRET_KEY"] == "test-secret"
    assert parsed["NFM_CORS_ORIGINS"] == '["https://staging.example.org"]'
    assert parsed["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] == "id1,id2", (
        "NFM-4170 canonicals forwarding regressed"
    )
    assert parsed[ENV_VAR_NAME] == "some-password"


# ---------------------------------------------------------------------------
# Tests — NFM-4215: generated file mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "precreate_mode",
    [None, 0o644],
    ids=["fresh-create", "preexisting-0644"],
)
def test_write_api_env_file_enforces_mode_0600(
    tmp_path: Path, precreate_mode: int | None
) -> None:
    """NFM-4215 — the generated api env file must land 0600.

    The file holds ``NFM_SECRET_KEY``, ``NFM_DATABASE_URL`` and (since
    NFM-4197) ``NFMD_PREVIEW_DB_PASSWORD``; a default-umask heredoc write
    leaves it 0644. The ``preexisting-0644`` case is the discriminator:
    ``cat >`` truncates an existing file without touching its mode, so
    only an explicit ``chmod 600`` tightens hosts deployed before this
    change — a creation-time ``umask`` fix would silently pass
    ``fresh-create`` and fail there. Matches the 0600 already enforced
    on the parent ``docker/.env.staging`` (NFM-4190).
    """
    proc, _content = _run_write_api_env_file(
        tmp_path,
        [f"{ENV_VAR_NAME}=some-password"],
        precreate_mode=precreate_mode,
    )
    assert proc.returncode == 0, f"write_api_env_file died: {proc.stderr}"
    generated = tmp_path / "project" / "docker" / ".env.staging.api"
    mode = stat.S_IMODE(generated.stat().st_mode)
    assert mode == 0o600, (
        f"docker/.env.staging.api is {oct(mode)}, expected 0o600 — secrets "
        "file must not be group/world-readable (NFM-4215)."
    )
