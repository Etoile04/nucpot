"""Static guard: prod LightRAG KV/doc-status storage stays PINNED to PG (NFM-4819).

Background
----------
PR #1334 made ``PGKVStorage`` / ``PGDocStatusStorage`` the compose *defaults*
via ``${PROD_LIGHTRAG_KV_STORAGE:-PGKVStorage}`` interpolation. The prod
host's gitignored ``docker/.env.prod`` still carried
``PROD_LIGHTRAG_KV_STORAGE=JsonKVStorage`` /
``PROD_LIGHTRAG_DOC_STATUS_STORAGE=JsonDocStatusStorage`` from an earlier
era, so the override silently defeated the durability default and the
2026-09-12 cutover deploy no-opped (verified on the running container: env
still ``Json*`` after ea95ce3a0). The file-backed stores are the structural
root cause of both 2026-09-05 / 2026-09-11 corpus wipes (NFM-4736).

Resolution chain:
  * NFM-4736 pinned the two values as literals in ``docker-compose.prod.yml``
    (no ``${VAR:-default}`` indirection — the pin IS the wall).
  * NFM-4804 deleted the two stale lines from the host's
    ``docker/.env.prod`` (2026-09-13, operational — the file is gitignored).
  * The 2026-09-13 07:18Z deploy (image ``nucpot-prod-lightrag:1ceefe83b…``)
    recreated the sidecar; its runtime env is ``PG*`` across the board.

What this test enforces
-----------------------
1. ``docker-compose.prod.yml`` ``services.lightrag.environment`` maps
   ``LIGHTRAG_KV_STORAGE`` → ``PGKVStorage`` and
   ``LIGHTRAG_DOC_STATUS_STORAGE`` → ``PGDocStatusStorage`` as **literal
   strings** — a ``${PROD_LIGHTRAG_KV_STORAGE:-…}`` indirection is a failure
   even when the default is PG, because it re-arms the env-file override.
2. ``docker/.env.prod.example`` (the template a fresh host provisions from)
   contains no ``PROD_LIGHTRAG_KV_STORAGE`` / ``PROD_LIGHTRAG_DOC_STATUS_STORAGE``
   keys, so provisioning cannot regenerate the override.
3. The migration runbook must not instruct operators to roll back via those
   env keys — they are inert since the pin; following the old text during an
   incident would silently no-op the rollback. Rollback must point at editing
   ``docker-compose.prod.yml``.

Failure modes
-------------
A future PR that re-introduces the interpolation, re-adds the keys to the
env template, or restores the stale runbook rollback text fails here before
it can re-split ground truth between the JSON files and the PG tables (the
state that breaks the sanctioned-SQL audit gates — rag_audit /
``run-sql.sh`` reads).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# Repo-rooted absolute paths so the test is independent of CWD.
# Layout: <repo>/apps/api/tests/compose/test_lightrag_kv_storage_pinned_pg.py
# parents[0] = tests/compose, [1] = tests, [2] = api, [3] = apps, [4] = repo
REPO_ROOT = Path(__file__).resolve().parents[4]

PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
ENV_PROD_EXAMPLE = REPO_ROOT / "docker" / ".env.prod.example"
MIGRATION_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "lightrag-pg-kv-storage-migration.md"

# The NFM-4736 pin: literal PG backends for KV + doc-status.
PINNED_ENV: dict[str, str] = {
    "LIGHTRAG_KV_STORAGE": "PGKVStorage",
    "LIGHTRAG_DOC_STATUS_STORAGE": "PGDocStatusStorage",
}

# Env-file keys that must never come back: each one re-arms the defeated
# override that this issue exists to bury.
FORBIDDEN_ENV_FILE_KEYS: tuple[str, ...] = (
    "PROD_LIGHTRAG_KV_STORAGE",
    "PROD_LIGHTRAG_DOC_STATUS_STORAGE",
)

# Compose interpolation marker — any ${...} in a pinned value re-arms the
# env-file override path, even when the fallback default is still PG.
_INTERPOLATION_RE = re.compile(r"\$\{.*\}")
_COMPOSE_TAG_RE = re.compile(r"!(?:override|reset|merge)\b")


def _load_compose(path: Path) -> dict[str, Any]:
    """Load a compose YAML and return the top-level mapping.

    Same tag-stripping contract as the sibling NFM-4481 guard: compose-spec
    custom tags (``!override`` etc.) are stripped before ``safe_load``; the
    ``services:`` block we inspect never uses them.
    """
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(_COMPOSE_TAG_RE.sub("", text))
    assert isinstance(loaded, dict), (
        f"{path}: top-level YAML must be a mapping (services:), got {type(loaded).__name__}"
    )
    return loaded


def _lightrag_environment(path: Path) -> dict[str, Any]:
    """Return the ``lightrag`` service's ``environment:`` mapping.

    Raises (test failure) if the service or its environment block is missing —
    unlike the sibling port guard there is no legitimate prod shape without
    these env entries.
    """
    doc = _load_compose(path)
    lightrag = (doc.get("services") or {}).get("lightrag")
    assert isinstance(lightrag, dict), (
        f"{path}: `services.lightrag` must be a mapping, got {type(lightrag).__name__}"
    )
    env = lightrag.get("environment")
    assert isinstance(env, dict), (
        f"{path}: `services.lightrag.environment` must be a mapping, got {type(env).__name__}"
    )
    return env


def _pinned_literal_violations(env: dict[str, Any]) -> list[str]:
    """Return human-readable violations of the NFM-4819 pin contract.

    A violation is: pinned key missing, value not the literal PG backend, or
    value containing ``${...}`` interpolation (re-arms the env-file override).
    """
    violations: list[str] = []
    for key, expected in PINNED_ENV.items():
        raw = env.get(key)
        if raw is None:
            violations.append(f"{key}: missing (expected literal `{expected}`)")
            continue
        rendered = str(raw)
        if _INTERPOLATION_RE.search(rendered):
            violations.append(
                f"{key}: {rendered!r} uses env-var interpolation — that re-arms "
                "the env-file override path NFM-4736/NFM-4819 buried; pin a "
                "literal value instead"
            )
        elif rendered != expected:
            violations.append(f"{key}: {rendered!r} != pinned literal {expected!r}")
    return violations


def _env_file_key_violations(text: str) -> list[str]:
    """Return forbidden env-file keys present as KEY= assignments in ``text``."""
    return [key for key in FORBIDDEN_ENV_FILE_KEYS if re.search(rf"^\s*{key}\s*=", text, re.M)]


def _forbidden_key_references(text: str) -> list[str]:
    """Return forbidden keys referenced as assignments anywhere in prose.

    Unanchored counterpart of ``_env_file_key_violations``: catches
    ``…set `PROD_LIGHTRAG_KV_STORAGE=JsonKVStorage` in docker/.env.prod…``
    mid-sentence, where the line-start detector legitimately does not fire.
    """
    return [key for key in FORBIDDEN_ENV_FILE_KEYS if re.search(rf"\b{key}\s*=", text)]


# ---------------------------------------------------------------------------
# 1. Prod compose pins the PG backends as literals
# ---------------------------------------------------------------------------


class TestProdComposePinsPgStorage:
    def test_kv_and_doc_status_storage_are_literal_pg(self) -> None:
        """Both pinned keys are literal PG backends, no interpolation."""
        violations = _pinned_literal_violations(_lightrag_environment(PROD_COMPOSE))
        assert not violations, (
            f"{PROD_COMPOSE.name}: NFM-4819 requires LIGHTRAG_KV_STORAGE / "
            "LIGHTRAG_DOC_STATUS_STORAGE pinned as literal PG backends on the "
            "`lightrag` service (the JSON stores are the structural root cause "
            "of both 2026-09-05 / 2026-09-11 corpus wipes). Violations:\n  "
            + "\n  ".join(violations)
        )

    def test_vector_and_graph_storage_defaults_still_pg_and_networkx(self) -> None:
        """The overridable pair keeps PG-vector / NetworkX defaults (drift alarm).

        These two remain ``${PROD_*:-…}``-overridable by design (NFM-1764 /
        AGE-extension constraint), but if the *defaults* ever drift away from
        PGVectorStorage / NetworkXStorage that is an unintended config change
        and must be consciously reviewed, not silently merged.
        """
        env = _lightrag_environment(PROD_COMPOSE)
        vector = str(env.get("LIGHTRAG_VECTOR_STORAGE", ""))
        graph = str(env.get("LIGHTRAG_GRAPH_STORAGE", ""))
        assert "PGVectorStorage" in vector, (
            f"{PROD_COMPOSE.name}: LIGHTRAG_VECTOR_STORAGE default must remain "
            f"PGVectorStorage, got {vector!r}"
        )
        assert "NetworkXStorage" in graph, (
            f"{PROD_COMPOSE.name}: LIGHTRAG_GRAPH_STORAGE default must remain "
            f"NetworkXStorage (PGGraphStorage needs the AGE extension the "
            f"pgvector image does not ship), got {graph!r}"
        )


# ---------------------------------------------------------------------------
# 2. Env template cannot regenerate the override on a fresh host
# ---------------------------------------------------------------------------


class TestEnvProdExampleStaysClean:
    def test_no_kv_storage_override_keys_in_template(self) -> None:
        """``docker/.env.prod.example`` has no PROD_LIGHTRAG_*_STORAGE keys."""
        if not ENV_PROD_EXAMPLE.exists():
            pytest.skip(f"{ENV_PROD_EXAMPLE} not present in this checkout")
        violations = _env_file_key_violations(ENV_PROD_EXAMPLE.read_text(encoding="utf-8"))
        assert not violations, (
            f"{ENV_PROD_EXAMPLE}: NFM-4819 forbids re-adding override key(s) "
            f"{violations!r} — a fresh host provisioning from this template "
            "would regenerate the Json* override the NFM-4736 compose pin "
            "exists to bury."
        )


# ---------------------------------------------------------------------------
# 3. Runbook must not teach the now-inert env-file rollback
# ---------------------------------------------------------------------------


class TestMigrationRunbookRollbackIsNotInert:
    def test_runbook_does_not_teach_env_override_rollback(self) -> None:
        """The runbook rollback must not point at the inert env-file keys.

        Since the compose pin (NFM-4736), setting
        ``PROD_LIGHTRAG_KV_STORAGE`` / ``PROD_LIGHTRAG_DOC_STATUS_STORAGE``
        in ``docker/.env.prod`` does NOTHING — no compose key interpolates
        them. An operator following the old rollback text during an incident
        would believe they had restored the JSON backends while the stack
        kept running on PG. The only sanctioned rollback is editing
        ``docker-compose.prod.yml``.
        """
        if not MIGRATION_RUNBOOK.exists():
            pytest.skip(f"{MIGRATION_RUNBOOK} not present in this checkout")
        text = MIGRATION_RUNBOOK.read_text(encoding="utf-8")
        violations = _forbidden_key_references(text)
        assert not violations, (
            f"{MIGRATION_RUNBOOK.name}: references inert override key(s) "
            f"{violations!r}. Since the NFM-4736 literal pin these env keys "
            "are dead — rollback instructions must point at editing "
            "`docker-compose.prod.yml`, not `docker/.env.prod`."
        )

    def test_runbook_documents_compose_edit_rollback(self) -> None:
        """The rollback section names ``docker-compose.prod.yml`` as the lever."""
        if not MIGRATION_RUNBOOK.exists():
            pytest.skip(f"{MIGRATION_RUNBOOK} not present in this checkout")
        text = MIGRATION_RUNBOOK.read_text(encoding="utf-8")
        rollback = text.split("## Rollback", 1)
        assert len(rollback) == 2, f"{MIGRATION_RUNBOOK.name}: expected a `## Rollback` section."
        assert "docker-compose.prod.yml" in rollback[1], (
            f"{MIGRATION_RUNBOOK.name}: the Rollback section must name "
            "`docker-compose.prod.yml` as the rollback lever (the only path "
            "that actually changes the pinned storage backends)."
        )


# ---------------------------------------------------------------------------
# Regression fixtures: prove the guards CATCH the bad shapes
# ---------------------------------------------------------------------------


class TestRegressionFixtures:
    """Synthetic bad inputs must be flagged; good inputs must pass.

    A future refactor that weakens the violation helpers (e.g. drops the
    interpolation check) is caught here before it lands.
    """

    def test_interpolated_default_is_a_violation_even_when_default_is_pg(self) -> None:
        """``${PROD_LIGHTRAG_KV_STORAGE:-PGKVStorage}`` fails — it re-arms override."""
        env = {"LIGHTRAG_KV_STORAGE": "${PROD_LIGHTRAG_KV_STORAGE:-PGKVStorage}"}
        violations = _pinned_literal_violations(env)
        assert any("interpolation" in v for v in violations), (
            "interpolated default must be flagged as re-arming the override; "
            f"got violations: {violations!r}"
        )

    def test_json_backend_value_is_a_violation(self) -> None:
        env = {"LIGHTRAG_KV_STORAGE": "JsonKVStorage"}
        violations = _pinned_literal_violations(env)
        assert any("JsonKVStorage" in v for v in violations)

    def test_missing_pinned_key_is_a_violation(self) -> None:
        violations = _pinned_literal_violations({})
        assert len(violations) == len(PINNED_ENV)

    def test_clean_pin_passes(self) -> None:
        assert _pinned_literal_violations(dict(PINNED_ENV)) == []

    def test_env_template_with_stale_key_is_flagged(self) -> None:
        text = "PROD_LIGHTRAG_KV_STORAGE=JsonKVStorage\nOTHER=1\n"
        assert _env_file_key_violations(text) == ["PROD_LIGHTRAG_KV_STORAGE"]

    def test_env_template_with_graph_storage_only_passes(self) -> None:
        """The benign vector/graph keys (still interpolated by design) pass."""
        text = (
            "PROD_LIGHTRAG_VECTOR_STORAGE=PGVectorStorage\n"
            "PROD_LIGHTRAG_GRAPH_STORAGE=NetworkXStorage\n"
        )
        assert _env_file_key_violations(text) == []

    def test_prose_reference_is_flagged_by_runbook_detector(self) -> None:
        """Mid-sentence `set PROD_LIGHTRAG_KV_STORAGE=… in docker/.env.prod` is caught."""
        prose = (
            "Revert the compose default (or set `PROD_LIGHTRAG_KV_STORAGE=JsonKVStorage` "
            "in `docker/.env.prod`) and redeploy.\n"
        )
        # The env-file detector must NOT fire (key is not at line start)…
        assert _env_file_key_violations(prose) == []
        # …but the runbook prose detector must.
        assert _forbidden_key_references(prose) == ["PROD_LIGHTRAG_KV_STORAGE"]
