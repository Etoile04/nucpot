"""NFM-4170 — config-side test for the 6 canonical ``data_sources.id`` UUIDs.

This validates that the config artifacts (env templates + production
compose) agree on the canonicals published in NFM-4162 and consumed
by ``apps.api.src.nfm_db.services.attribution_flag`` (landed on
``origin/NFM-4159-attribution-datasets``, PR #1113).  The consumer
itself is exercised by ``test_attribution_flag.py`` once the
NFM-4159 PR merges; this test pins the *config* half in isolation so
the two halves can ship independently.

Why these specific files
------------------------

* ``.env.prod.example`` — the root operator template (NFM-2221 drift
  detector scans ``.env.prod`` against ``docker/.env.prod``).
* ``docker/.env.prod.example`` — the template loaded into the api
  container via ``--env-file docker/.env.prod``.
* ``docker/.env.staging.example`` — staging template; staging uses
  ``env_file: docker/.env.staging.api`` and the staging deploy
  script derives it from ``docker/.env.staging``.
* ``docker-compose.prod.yml`` — wires ``${NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS:-}``
  onto the api container with a default-empty fallback so the
  feature-flag defaults to the safe no-op when the var is unset
  (matches the NFM-4159 AC "seeded EMPTY").

The 6 canonicals
----------------

Sourced from NFM-4162 (CPO PUBLISHED, comment ``6a1392c2-…``).
Bounded SQL against ``nucpot-prod-clone-nfm4139`` re-verified the
distinct canonicals = 6 on 2026-09-02 (uuid_titled=14,
unknown_source=6, unattributed=12, bad_uuid_resolved=14).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

# Repo-rooted absolute paths so the test is independent of CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_ENV_EXAMPLE = REPO_ROOT / ".env.prod.example"
DOCKER_PROD_ENV_EXAMPLE = REPO_ROOT / "docker" / ".env.prod.example"
DOCKER_STAGING_ENV_EXAMPLE = REPO_ROOT / "docker" / ".env.staging.example"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"

ENV_VAR_NAME = "NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"

# Canonical-id order (matches NFM-4162 PUBLISHED table).
EXPECTED_CANONICAL_UUIDS: tuple[str, ...] = (
    "1a0f45d9-b4a4-45f2-a073-9d5779139e9c",
    "49034bf0-f58e-4900-889d-11342c77c518",
    "673258f9-21dc-4485-a6dd-1eb1df13ed23",
    "9320cb50-eb65-4178-8d2e-c56aeb848b21",
    "a4c37a11-13da-4316-8025-7adb1b9c5651",
    "cd36999a-b0a0-4d76-935f-6595bb09cb64",
)


# ---------------------------------------------------------------------------
# Parsing helper
# ---------------------------------------------------------------------------

# ``KEY=VALUE`` shell-style with optional leading whitespace tolerated,
# matching how Docker / shell ``source`` parses env files.
_ENV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Return ``{KEY: raw_value}`` for every non-comment ``KEY=VALUE`` line.

    Inline comments are NOT stripped — values may legitimately contain
    ``#`` (e.g. URLs with fragments).  The verify-env-sync script does
    the same.
    """
    if not path.exists():  # pragma: no cover — files are committed
        pytest.skip(f"{path} not present in this checkout")
    parsed: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _ENV_LINE_RE.match(raw)
        if match is None:
            continue
        parsed[match.group(1)] = match.group(2)
    return parsed


def _parse_canonical_uuids(raw_value: str) -> tuple[uuid.UUID, ...]:
    """Parse the comma-separated UUID list (whitespace tolerant, empty OK)."""
    parts = [p.strip() for p in raw_value.split(",")]
    parts = [p for p in parts if p]
    return tuple(uuid.UUID(p) for p in parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_path,key_name",
    [
        (ROOT_ENV_EXAMPLE, ENV_VAR_NAME),
        (DOCKER_PROD_ENV_EXAMPLE, ENV_VAR_NAME),
        # Staging uses the ``STAGING_NFM_*`` prefix convention so the
        # value doesn't collide with system env; ``scripts/staging_deploy.sh``
        # strips the prefix when forwarding into ``docker/.env.staging.api``.
        (DOCKER_STAGING_ENV_EXAMPLE, f"STAGING_{ENV_VAR_NAME}"),
    ],
    ids=["root-env-prod-example", "docker-env-prod-example", "docker-env-staging-example"],
)
def test_env_template_declares_canonical_config_key(env_path: Path, key_name: str) -> None:
    """Every env template that drives api container runtime MUST declare the key."""
    parsed = _parse_env_file(env_path)
    assert key_name in parsed, (
        f"{env_path.name}: missing {key_name}; "
        f"add the canonical 6-UUID list (NFM-4162 PUBLISHED, comment 6a1392c2)."
    )


@pytest.mark.parametrize(
    "env_path,key_name",
    [
        (ROOT_ENV_EXAMPLE, ENV_VAR_NAME),
        (DOCKER_PROD_ENV_EXAMPLE, ENV_VAR_NAME),
        (DOCKER_STAGING_ENV_EXAMPLE, f"STAGING_{ENV_VAR_NAME}"),
    ],
)
def test_env_template_canonical_value_parses_as_six_uuids_in_canonical_order(
    env_path: Path, key_name: str
) -> None:
    """Parsed value must be exactly the 6 canonical UUIDs in canonical-id order."""
    parsed = _parse_env_file(env_path)
    assert key_name in parsed, f"{env_path.name}: missing key (see sibling test)"
    actual = _parse_canonical_uuids(parsed[key_name])
    expected = tuple(uuid.UUID(u) for u in EXPECTED_CANONICAL_UUIDS)
    assert actual == expected, (
        f"{env_path.name}: {key_name} must be the 6 canonical UUIDs in "
        f"canonical-id order. expected={expected} actual={actual}"
    )


def test_prod_compose_wires_canonical_env_into_api_service() -> None:
    """``docker-compose.prod.yml`` api service MUST inject the env var (default empty)."""
    if not PROD_COMPOSE.exists():  # pragma: no cover — file is committed
        pytest.skip(f"{PROD_COMPOSE} not present in this checkout")
    text = PROD_COMPOSE.read_text(encoding="utf-8")

    # Locate the api service block (start at ``api:`` for top-level, end at
    # the next ``^  [a-z]``-prefixed top-level key).  The api service is the
    # only service that needs the env var; the worker / web / lightrag do
    # not touch attribution_flag.
    api_block_match = re.search(
        r"^  api:\n(?P<body>(?:^    .*\n|^\s*\n)*)",
        text,
        flags=re.MULTILINE,
    )
    assert api_block_match is not None, (
        "could not locate top-level `api:` service block in docker-compose.prod.yml"
    )
    api_block = api_block_match.group("body")

    # The wiring must reference the env var name and have a default-empty
    # fallback so deployments that don't set the var fall back to the
    # safe no-op (matches the NFM-4159 AC "seeded EMPTY").
    pattern = rf"{ENV_VAR_NAME}:\s*\${{{ENV_VAR_NAME}:-}}"
    assert re.search(pattern, api_block), (
        f"docker-compose.prod.yml api service must inject "
        f"`{ENV_VAR_NAME}: ${{{ENV_VAR_NAME}:-}}` so the var defaults to "
        f"empty (safe no-op) when not set. Found block:\n{api_block}"
    )


def test_verify_env_sync_accepts_canonical_key() -> None:
    """The pre-deploy drift detector MUST recognise the new key in both env files.

    Re-implements the key-extraction logic from ``scripts/verify-env-sync.sh``
    inline (it does not export a library).  Failure here means a typo'd key
    passed CI but the deploy would silently no-op.
    """
    for env_path in (ROOT_ENV_EXAMPLE, DOCKER_PROD_ENV_EXAMPLE):
        keys = _parse_env_file(env_path).keys()
        assert ENV_VAR_NAME in keys, (
            f"{env_path.name}: {ENV_VAR_NAME} missing — verify-env-sync.sh "
            f"would flag drift on the next deploy."
        )
