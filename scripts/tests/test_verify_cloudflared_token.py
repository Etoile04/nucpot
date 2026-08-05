"""Tests for scripts/verify-cloudflared-token.sh.

Context: NFM-2509. The ``STAGING_CLOUDFLARE_TUNNEL_TOKEN`` in
``docker/.env.staging`` was set to the *production* tunnel token (id
``04b1e559-4547-4568-b77e-e018ca9fa6d6``). Because ``docker-compose.staging.yml``
hands the env var straight to a ``cloudflare/cloudflared`` container with
``restart: unless-stopped``, every redeploy silently registered a second
replica of the production tunnel whose Cloudflare-managed origin was
``localhost:3000`` on the host — but inside the container's network
namespace that origin is nothing. Cloudflare then load-balanced
``nucpot.dpdns.org`` between the host's four good connections and the
container's four broken ones, returning 502 ~10% of the time.

The guard: if ``STAGING_CLOUDFLARE_TUNNEL_TOKEN`` is present in the staging
env file, decode the JWT and assert the ``t`` claim is not equal to the
production tunnel id. The same protection should cover any future
*_CLOUDFLARE_TUNNEL_TOKEN that drifts toward the prod one, so the script
takes the key name as its input.

The script reads the token, decodes the payload, and prints the *id* on
mismatch. It never prints the raw token.
"""

import base64
import json
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT: Path = Path(__file__).resolve().parents[1] / "verify-cloudflared-token.sh"

PROD_TUNNEL_ID: str = "04b1e559-4547-4568-b77e-e018ca9fa6d6"


def _make_token(tunnel_id: str) -> str:
    """Build a fake cloudflared JWT whose payload decodes to *tunnel_id*.

    The real token signs the payload; the guard only needs to read the
    unverified ``t`` claim, so an unsigned payload is enough.
    """
    payload: str = json.dumps({"a": "fake-account", "t": tunnel_id, "s": "fake-secret"})
    b64: str = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    return f"fake-header.{b64}.fake-signature"


def _make_real_format_token(tunnel_id: str) -> str:
    """Build a single-segment base64url token in the format cloudflared
    actually ships from the Cloudflare Zero Trust dashboard today (one
    base64url-encoded JSON object with ``a``/``t``/``s`` fields).

    The guard must accept this in addition to the legacy 3-segment JWT
    shape — see the NFM-2507 staging tunnel restoration (2026-08-05).
    """
    payload: str = json.dumps({"a": "fake-account", "t": tunnel_id, "s": "fake-secret"})
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()


def _write_env(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} does not exist"
    mode: int = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"{SCRIPT} is not executable by owner"


# ---------------------------------------------------------------------------
# Default key: STAGING_CLOUDFLARE_TUNNEL_TOKEN
# ---------------------------------------------------------------------------


def test_prod_tunnel_id_in_staging_env_fails(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path / ".env.staging",
        f"STAGING_CLOUDFLARE_TUNNEL_TOKEN={_make_token(PROD_TUNNEL_ID)}\n",
    )
    result = run_script(str(env))
    assert result.returncode == 1, result.stderr
    assert "STAGING_CLOUDFLARE_TUNNEL_TOKEN" in result.stderr
    assert PROD_TUNNEL_ID in result.stderr


def test_distinct_tunnel_id_in_staging_env_passes(tmp_path: Path) -> None:
    distinct: str = "11111111-2222-3333-4444-555555555555"
    env = _write_env(
        tmp_path / ".env.staging",
        f"STAGING_CLOUDFLARE_TUNNEL_TOKEN={_make_token(distinct)}\n",
    )
    result = run_script(str(env))
    assert result.returncode == 0, result.stderr


def test_real_format_token_with_prod_id_fails(tmp_path: Path) -> None:
    """Single-segment base64url token (the real cloudflared format) carrying
    the prod tunnel id must still trip the denylist guard. Mirrors
    ``test_prod_tunnel_id_in_staging_env_fails`` for the JWT shape.
    """
    env = _write_env(
        tmp_path / ".env.staging",
        f"STAGING_CLOUDFLARE_TUNNEL_TOKEN={_make_real_format_token(PROD_TUNNEL_ID)}\n",
    )
    result = run_script(str(env))
    assert result.returncode == 1, result.stderr
    assert "STAGING_CLOUDFLARE_TUNNEL_TOKEN" in result.stderr
    assert PROD_TUNNEL_ID in result.stderr


def test_real_format_token_with_distinct_id_passes(tmp_path: Path) -> None:
    """Single-segment base64url token (the real cloudflared format) carrying
    a non-prod tunnel id must pass. Mirrors
    ``test_distinct_tunnel_id_in_staging_env_passes`` for the JWT shape.
    """
    distinct: str = "11111111-2222-3333-4444-555555555555"
    env = _write_env(
        tmp_path / ".env.staging",
        f"STAGING_CLOUDFLARE_TUNNEL_TOKEN={_make_real_format_token(distinct)}\n",
    )
    result = run_script(str(env))
    assert result.returncode == 0, result.stderr


def test_key_absent_passes_with_skip_message(tmp_path: Path) -> None:
    """An operator may legitimately not have set the staging tunnel yet."""
    env = _write_env(tmp_path / ".env.staging", "STAGING_DATABASE_URL=postgres://x\n")
    result = run_script(str(env))
    assert result.returncode == 0, result.stderr
    assert "skip" in (result.stdout + result.stderr).lower()


def test_empty_token_fails(tmp_path: Path) -> None:
    """An empty-but-set token is broken — fail instead of silently skipping.

    NFM-2509 review note: the original guard conflated an empty token with
    the ``change-me`` placeholder, so an operator who partially applied the
    fix (emptied the token value rather than removing the line) got a green
    guard. ``docker compose`` would then start the cloudflared container
    with an empty token and fail at runtime with an opaque error, which is
    exactly the partial-fix state the guard is supposed to surface.
    """
    env = _write_env(
        tmp_path / ".env.staging",
        "STAGING_CLOUDFLARE_TUNNEL_TOKEN=\n",
    )
    result = run_script(str(env))
    assert result.returncode == 1, result.stderr
    assert "empty" in result.stderr.lower()
    assert "STAGING_CLOUDFLARE_TUNNEL_TOKEN" in result.stderr


def test_quoted_empty_token_fails(tmp_path: Path) -> None:
    """A quoted empty string is still empty — same failure mode."""
    env = _write_env(
        tmp_path / ".env.staging",
        'STAGING_CLOUDFLARE_TUNNEL_TOKEN=""\n',
    )
    result = run_script(str(env))
    assert result.returncode == 1, result.stderr
    assert "empty" in result.stderr.lower()


def test_placeholder_still_skips(tmp_path: Path) -> None:
    """The documented ``change-me`` placeholder must still skip.

    Regression guard for the NFM-2509 refactor: the empty/placeholder split
    keeps the placeholder branch so the template env file does not turn
    red out of the box.
    """
    env = _write_env(
        tmp_path / ".env.staging",
        "STAGING_CLOUDFLARE_TUNNEL_TOKEN=change-me\n",
    )
    result = run_script(str(env))
    assert result.returncode == 0, result.stderr
    assert "skip" in (result.stdout + result.stderr).lower()
    assert "placeholder" in (result.stdout + result.stderr).lower()


def test_missing_env_file_exits_one(tmp_path: Path) -> None:
    result = run_script(str(tmp_path / "absent.env"))
    assert result.returncode == 1
    assert "not found" in result.stderr


# ---------------------------------------------------------------------------
# Custom key: same guard for any *_CLOUDFLARE_TUNNEL_TOKEN
# ---------------------------------------------------------------------------


def test_custom_key_with_prod_token_fails(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path / ".env.staging",
        f"FOO_CLOUDFLARE_TUNNEL_TOKEN={_make_token(PROD_TUNNEL_ID)}\n",
    )
    result = run_script(str(env), "FOO_CLOUDFLARE_TUNNEL_TOKEN")
    assert result.returncode == 1, result.stderr
    assert "FOO_CLOUDFLARE_TUNNEL_TOKEN" in result.stderr


def test_custom_key_with_distinct_token_passes(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path / ".env.staging",
        f"FOO_CLOUDFLARE_TUNNEL_TOKEN={_make_token('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')}\n",
    )
    result = run_script(str(env), "FOO_CLOUDFLARE_TUNNEL_TOKEN")
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Secret hygiene: the raw token must never appear in any output stream
# ---------------------------------------------------------------------------


def test_raw_token_value_never_appears_in_output(tmp_path: Path) -> None:
    env_text: str = f"STAGING_CLOUDFLARE_TUNNEL_TOKEN={_make_token(PROD_TUNNEL_ID)}\n"
    env = _write_env(tmp_path / ".env.staging", env_text)
    result = run_script(str(env))
    combined: str = result.stdout + result.stderr
    # The whole token literal is the secret; the tunnel id is not.
    for line in env_text.splitlines():
        if "STAGING_CLOUDFLARE_TUNNEL_TOKEN=" in line:
            raw_value: str = line.split("=", 1)[1].strip()
            assert raw_value not in combined, "raw token leaked into script output"


def test_no_hardcoded_prod_token_id_in_logic() -> None:
    """The prod id is allowed as a defaultable sentinel (variable assignment),
    but must not be baked into the comparison logic itself. A future prod
    tunnel change should be a config edit, not a code edit.
    """
    code_lines: list[str] = [
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    ]
    # Allow the id on a single `:-` default-assignment line; flag any other
    # non-comment occurrence as hardcoded.
    offending: list[str] = [
        line.strip() for line in code_lines
        if PROD_TUNNEL_ID in line and ":-" not in line
    ]
    assert not offending, (
        f"prod tunnel id must only appear in a defaultable variable assignment, "
        f"but found it on: {offending}"
    )


# ---------------------------------------------------------------------------
# Wiring: staging_deploy.sh and the env-sync script must invoke the guard
# ---------------------------------------------------------------------------


def test_staging_deploy_invokes_the_guard() -> None:
    repo_root: Path = SCRIPT.resolve().parents[1]
    deploy: Path = repo_root / "scripts" / "staging_deploy.sh"
    assert deploy.is_file(), f"{deploy} not found"
    text: str = deploy.read_text()
    assert "verify-cloudflared-token.sh" in text, (
        "staging_deploy.sh must invoke the cloudflared token guard"
    )
    # The check must run *after* the env file is loaded (so the env var is set)
    # and *before* the container is brought up — otherwise we are guarding
    # something already running.
    load_idx: int = text.index("load_env_file()")
    assert load_idx < text.index("verify-cloudflared-token.sh"), (
        "guard must be called after load_env_file()"
    )
    up_marker: str = 'compose up'  # the command that brings the cloudflared container up
    assert text.index("verify-cloudflared-token.sh") < text.index(up_marker), (
        "guard must run before 'compose up'"
    )


def test_staging_workflow_runs_the_guard() -> None:
    repo_root: Path = SCRIPT.resolve().parents[1]
    workflow: Path = repo_root / ".github" / "workflows" / "staging-deploy.yml"
    assert workflow.is_file(), f"{workflow} not found"
    text: str = workflow.read_text()
    assert "verify-cloudflared-token.sh" in text, (
        "staging-deploy.yml must invoke the cloudflared token guard"
    )
