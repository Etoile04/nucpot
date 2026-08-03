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
REPO_ROOT: Path = SCRIPT.resolve().parents[1]

PROD_TUNNEL_ID: str = "04b1e559-4547-4568-b77e-e018ca9fa6d6"

#: The placeholder actually shipped in ``docker/.env.staging.example``.
#: NFM-2516: the NFM-2509 guard only recognised the shorter ``change-me``
#: sentinel, so an operator who copied the template verbatim fell through to
#: the JWT decoder and got an opaque "does not look like a JWT" instead of
#: actionable "paste your real staging token" guidance.
EXAMPLE_PLACEHOLDER: str = "change-me-paste-token-from-cloudflare-zero-trust"

#: The original NFM-2509 sentinel. Still recognised, for env files written
#: before the checked-in template grew the longer value.
LEGACY_PLACEHOLDER: str = "change-me"


def _make_token(tunnel_id: str) -> str:
    """Build a fake cloudflared JWT whose payload decodes to *tunnel_id*.

    The real token signs the payload; the guard only needs to read the
    unverified ``t`` claim, so an unsigned payload is enough.
    """
    payload: str = json.dumps({"a": "fake-account", "t": tunnel_id, "s": "fake-secret"})
    b64: str = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    return f"fake-header.{b64}.fake-signature"


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


def test_key_absent_fails(tmp_path: Path) -> None:
    """An absent key must fail — ``docker-compose.staging.yml`` rejects it.

    NFM-2516. The cloudflared service interpolates the token as::

        TUNNEL_TOKEN: ${STAGING_CLOUDFLARE_TUNNEL_TOKEN:?Set STAGING_...}

    The ``:?`` form errors when the variable is *unset or empty*, and the
    service carries no ``profiles:`` key, so it is always part of
    ``compose up``. NFM-2509 skipped on absent with exit 0, which meant the
    guard reported success for the one env-file state compose is guaranteed
    to reject — the operator got a green pre-deploy check and then an
    interpolation error at ``compose up``.
    """
    env = _write_env(tmp_path / ".env.staging", "STAGING_DATABASE_URL=postgres://x\n")
    result = run_script(str(env))
    assert result.returncode == 1, result.stderr
    assert "STAGING_CLOUDFLARE_TUNNEL_TOKEN" in result.stderr
    # The operator needs to know compose will reject this, not just that a
    # key is missing.
    assert "compose" in result.stderr.lower()


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


@pytest.mark.parametrize(
    "placeholder",
    [
        pytest.param(LEGACY_PLACEHOLDER, id="legacy-change-me"),
        pytest.param(EXAMPLE_PLACEHOLDER, id="checked-in-template"),
    ],
)
def test_placeholder_fails_with_actionable_guidance(
    tmp_path: Path, placeholder: str
) -> None:
    """Both placeholders must fail early with actionable guidance.

    NFM-2516, reversing the NFM-2509 ``test_placeholder_still_skips``
    behaviour deliberately. A placeholder is never a working tunnel token:
    compose happily interpolates it (it is non-empty, so ``:?`` is
    satisfied), then ``cloudflared`` fails edge authentication and the
    container crash-loops under ``restart: unless-stopped``. Surfacing that
    at pre-deploy with "paste your real token" beats an opaque crash-loop.

    The checked-in template value previously fell through to the JWT
    decoder and produced "does not look like a JWT", which tells the
    operator nothing about what to do next.
    """
    env = _write_env(
        tmp_path / ".env.staging",
        f"STAGING_CLOUDFLARE_TUNNEL_TOKEN={placeholder}\n",
    )
    result = run_script(str(env))
    assert result.returncode == 1, result.stderr
    assert "placeholder" in result.stderr.lower()
    assert "STAGING_CLOUDFLARE_TUNNEL_TOKEN" in result.stderr
    # Actionable: name where a real token comes from.
    assert "zero trust" in result.stderr.lower()
    # Not the old opaque decoder message.
    assert "does not look like a jwt" not in result.stderr.lower()


def test_checked_in_example_placeholder_is_recognized_by_the_guard() -> None:
    """Lock the template and the guard together.

    This is the actual NFM-2516 defect: ``docker/.env.staging.example``
    drifted to a longer placeholder while the guard still matched only
    ``change-me``. If someone edits the template again, this test fails
    instead of the guard silently degrading to the opaque JWT error.
    """
    example: Path = REPO_ROOT / "docker" / ".env.staging.example"
    assert example.is_file(), f"{example} not found"

    value: str | None = None
    for line in example.read_text().splitlines():
        if line.startswith("STAGING_CLOUDFLARE_TUNNEL_TOKEN="):
            value = line.split("=", 1)[1].strip().strip('"')
    assert value is not None, "template must ship the key the guard checks"
    assert value == EXAMPLE_PLACEHOLDER, (
        f"template placeholder changed to {value!r}; teach the guard about it "
        f"in scripts/verify-cloudflared-token.sh and update EXAMPLE_PLACEHOLDER"
    )
    # And the guard must actually list it as a placeholder.
    assert value in SCRIPT.read_text(), (
        "guard does not recognise the placeholder shipped in the template"
    )


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
