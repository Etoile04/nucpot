"""Static guard: api images pin ``--timeout-keep-alive 75`` (NFM-5274 / NFM-5273).

Background
----------
NFM-5273 RCA (2026-09-29): ``nucpot-prod-web`` (Next.js standalone) proxies
``/api/*`` to ``http://nucpot-prod-api:8000`` with Node's default client-side
keep-alive pool (``keepAliveMsec`` free socket reuse window ~unbounded,
``maxFreeTimeout`` governed by the server). uvicorn's default
``--timeout-keep-alive 5`` closes an idle connection after 5s. When Node
reuses a pooled socket in the same instant uvicorn's idle timer fires, the
server sends RST mid-reuse and the proxy call dies with::

    Failed to proxy http://nucpot-prod-api:8000/api/v1/health
    Error: socket hang up (ECONNRESET)

Next converts that to a 500 for cloudflared — a public hard 500 while the
api logs stay spotless (uvicorn never saw the request). Prod-tunnel metrics
counted 121 such 500s since 2026-09-03 (~4-5/day).

Fix contract: the server must NEVER close an idle connection inside the
Node client's reuse window. ``--timeout-keep-alive 75`` pushes the server's
idle close far past any realistic pool-reuse interval, removing the race.

What this test enforces
-----------------------
1. ``docker/prod-api.Dockerfile`` CMD invokes uvicorn with
   ``--timeout-keep-alive 75``.
2. ``docker/staging-api.Dockerfile`` CMD's uvicorn invocation (inside the
   ``sh -c`` migrate-then-serve chain) carries the same flag — staging
   parity keeps the pre-prod environment representative of prod behaviour.
3. The flag is inert if a compose file overrides ``command:`` on the api
   service, so the prod and staging compose files must NOT set an api
   ``command:`` (the Dockerfile CMD is authoritative), and the preview
   compose must not set one either (preview reuses the ``nucpot-prod-api``
   image via ``image:`` — it inherits the fix through the shared CMD).

Failure modes
-------------
A future edit that drops the flag, lowers it back toward the uvicorn
default, or works around the Dockerfile via a compose ``command:`` override
fails here before it can re-arm the ECONNRESET race (AC-3's 48h
``Failed to proxy`` zero-regression window depends on this flag surviving
refactors).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

# Compose-spec custom tags PyYAML cannot resolve; preview uses !override.
# Stripping is a no-op for the prod/staging files (they never use them).
_COMPOSE_TAG_RE = re.compile(r"!(?:override|reset|merge)\b")

# The keep-alive pin this guard exists to protect (NFM-5274 fix spec).
_KEEPALIVE_FLAG_RE = re.compile(r"--timeout-keep-alive(?:=|\s+)75(?=\s|$)")

# Repo-rooted absolute paths so the test is independent of CWD.
# Layout: <repo>/apps/api/tests/compose/test_api_keepalive_timeout.py
# parents[0] = tests/compose, [1] = tests, [2] = api, [3] = apps, [4] = repo
REPO_ROOT = Path(__file__).resolve().parents[4]
PROD_API_DOCKERFILE = REPO_ROOT / "docker" / "prod-api.Dockerfile"
STAGING_API_DOCKERFILE = REPO_ROOT / "docker" / "staging-api.Dockerfile"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
PREVIEW_COMPOSE = REPO_ROOT / "docker-compose.preview.yml"


def _dockerfile_cmd(path: Path) -> str:
    """Flatten a Dockerfile's final ``CMD`` to one string for substring checks.

    Both api Dockerfiles use the exec (JSON-list) form::

        CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", ...]
        CMD ["sh", "-c", "python ... && exec uvicorn nfm_db.main:app ..."]

    so ``json.loads`` on the bracketed payload covers them; the shell-form
    fallback (``CMD uvicorn ...``) is handled by taking the raw remainder.
    ``pytest.skip`` on a missing file keeps the test independent of partial
    CI checkouts, mirroring the sibling compose guards.
    """
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")
    cmd_lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("CMD ")
    ]
    assert cmd_lines, f"{path}: no CMD instruction found"
    payload = cmd_lines[-1][len("CMD ") :].strip()
    if payload.startswith("["):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            pytest.fail(f"{path}: CMD is not valid JSON exec form: {exc}")
        assert isinstance(parsed, list), (
            f"{path}: CMD exec form must be a list, got {type(parsed).__name__}"
        )
        return " ".join(str(token) for token in parsed)
    return payload


def _load_yaml(path: Path) -> dict:
    """Load a compose YAML and return the top-level mapping (NFM-4481 helper)."""
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")
    stripped = _COMPOSE_TAG_RE.sub("", path.read_text(encoding="utf-8"))
    loaded = yaml.safe_load(stripped)
    assert isinstance(loaded, dict), (
        f"{path}: top-level YAML must be a mapping (services:), got {type(loaded).__name__}"
    )
    return loaded


def test_prod_api_cmd_pins_keepalive_75() -> None:
    """Prod api CMD must invoke uvicorn with --timeout-keep-alive 75 (AC-1)."""
    cmd = _dockerfile_cmd(PROD_API_DOCKERFILE)
    assert "uvicorn" in cmd and "nfm_db.main:app" in cmd, (
        f"docker/prod-api.Dockerfile CMD no longer serves uvicorn nfm_db.main:app: {cmd!r}"
    )
    assert _KEEPALIVE_FLAG_RE.search(cmd), (
        "docker/prod-api.Dockerfile CMD is missing '--timeout-keep-alive 75'. "
        "Without it uvicorn falls back to the 5s default idle close, re-arming "
        "the Node keep-alive reuse race that produced the public 500s "
        "(NFM-5273 RCA / NFM-5274)."
    )


def test_staging_api_cmd_pins_keepalive_75() -> None:
    """Staging api CMD's uvicorn invocation carries the same flag (parity)."""
    cmd = _dockerfile_cmd(STAGING_API_DOCKERFILE)
    assert "uvicorn" in cmd and "nfm_db.main:app" in cmd, (
        f"docker/staging-api.Dockerfile CMD no longer serves uvicorn nfm_db.main:app: {cmd!r}"
    )
    assert _KEEPALIVE_FLAG_RE.search(cmd), (
        "docker/staging-api.Dockerfile CMD is missing '--timeout-keep-alive 75'. "
        "Staging must mirror the prod keep-alive pin so pre-prod behaviour "
        "stays representative (NFM-5274 fix spec step 1 parity)."
    )


@pytest.mark.parametrize(
    ("compose_path", "label"),
    [
        (PROD_COMPOSE, "docker-compose.prod.yml"),
        (STAGING_COMPOSE, "docker-compose.staging.yml"),
        (PREVIEW_COMPOSE, "docker-compose.preview.yml"),
    ],
)
def test_compose_leaves_api_cmd_to_dockerfile(compose_path: Path, label: str) -> None:
    """No compose ``command:`` override on the api service may shadow the CMD.

    The Dockerfile pin only governs the running process when the compose
    service lets it through. A future ``command:`` override that omits the
    flag would silently restore the 5s default — this guard forces any such
    change to consciously extend the keep-alive pin.
    """
    services = _load_yaml(compose_path).get("services") or {}
    api_service = services.get("api")
    assert isinstance(api_service, dict), f"{label}: no 'api' service declared"
    assert not api_service.get("command"), (
        f"{label}: services.api sets command:{api_service.get('command')!r}, "
        "which shadows the Dockerfile CMD pinned by NFM-5274 — the "
        "--timeout-keep-alive 75 flag would go inert. Either drop the "
        "override or include the flag in it."
    )
