"""Tests for the skill factory + pin-lock wiring (NFM-4547 / AC-8).

The factory is the seam where ``EXTRACTION_SKILL_ENABLED`` flips
path-A's prompt from ontology-driven to skill-driven (§4.1 of ADR-016).
The CI guard (``apps/api/scripts/check_skill_pin.py``) is a separate
binary; these tests cover the Python-side checks the factory enforces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nfm_db.services.extraction_skill import (
    EXTRACTION_SKILL_ENABLED_ENV,
    EXTRACTION_SKILL_REPO_PIN_ENV,
    EXTRACTION_SKILL_VERSION_ENV,
    SkillPinMismatchError,
    assert_pin_consistent,
    extract_skill_prompt,
    is_skill_enabled,
    load_lock_file,
    resolve_skill_pin,
)

# ---------------------------------------------------------------------------
# Lock file loader
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_lock_file_returns_lock_dataclass() -> None:
    lock = load_lock_file()
    assert lock.catalog_id == "nuclear-materials-skills"
    assert "nuclear-property-extraction-v4" in lock.skills
    assert lock.default_skill == "nuclear-property-extraction-v4"


# ---------------------------------------------------------------------------
# is_skill_enabled — dark-launch default
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_skill_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXTRACTION_SKILL_ENABLED_ENV, raising=False)
    assert is_skill_enabled() is False


@pytest.mark.parametrize("flag_value", ["1", "true", "TRUE", "yes", "on"])
@pytest.mark.unit
def test_skill_flag_truthy_values(
    monkeypatch: pytest.MonkeyPatch, flag_value: str
) -> None:
    monkeypatch.setenv(EXTRACTION_SKILL_ENABLED_ENV, flag_value)
    assert is_skill_enabled() is True


# ---------------------------------------------------------------------------
# Pin consistency — AC-8 fail-closed
# ---------------------------------------------------------------------------


@pytest.fixture
def env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXTRACTION_SKILL_ENABLED_ENV, raising=False)
    monkeypatch.delenv(EXTRACTION_SKILL_REPO_PIN_ENV, raising=False)
    monkeypatch.delenv(EXTRACTION_SKILL_VERSION_ENV, raising=False)


def _stub_lock() -> Any:
    """Build a minimal SkillsLock matching the lock file shape."""
    from nfm_db.services.extraction_skill import SkillsLock

    return SkillsLock(
        catalog_id="nuclear-materials-skills",
        upstream_url="https://example.com/repo.git",
        pin="a" * 40,
        ref="v1.7.2",
        default_skill="nuclear-property-extraction-v4",
        skills={
            "nuclear-property-extraction-v4": {
                "version": "v1.7.2",
                "entrypoint": "skills/nuclear-property-extraction-v4/SKILL.md",
            }
        },
        raw={},
    )


@pytest.mark.unit
def test_assert_pin_consistent_raises_on_pin_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EXTRACTION_SKILL_REPO_PIN_ENV, "b" * 40)
    monkeypatch.setenv(EXTRACTION_SKILL_VERSION_ENV, "v1.7.2")
    with pytest.raises(SkillPinMismatchError) as excinfo:
        assert_pin_consistent(env=None, lock=_stub_lock())
    assert "drift" in str(excinfo.value).lower()
    assert excinfo.value.env_pin == "b" * 40
    assert excinfo.value.lock_pin == "a" * 40


@pytest.mark.unit
def test_assert_pin_consistent_raises_on_version_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EXTRACTION_SKILL_REPO_PIN_ENV, "a" * 40)
    monkeypatch.setenv(EXTRACTION_SKILL_VERSION_ENV, "v9.9.9")
    with pytest.raises(SkillPinMismatchError):
        assert_pin_consistent(env=None, lock=_stub_lock())


@pytest.mark.unit
def test_assert_pin_consistent_rejects_zero_pin_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock file ships with a zero SHA placeholder; once any real
    SHA is set in env, the factory must fail closed rather than
    silently ship.
    """
    from nfm_db.services.extraction_skill import SkillsLock

    monkeypatch.setenv(EXTRACTION_SKILL_REPO_PIN_ENV, "a" * 40)
    zero_lock = SkillsLock(
        catalog_id="x",
        upstream_url="https://example.com/repo.git",
        pin="0" * 40,
        ref=None,
        default_skill="nuclear-property-extraction-v4",
        skills={
            "nuclear-property-extraction-v4": {
                "version": "v1.7.2",
                "entrypoint": "skills/nuclear-property-extraction-v4/SKILL.md",
            }
        },
        raw={},
    )
    with pytest.raises(SkillPinMismatchError):
        assert_pin_consistent(env=None, lock=zero_lock)


@pytest.mark.unit
def test_assert_pin_consistent_passes_when_lock_and_env_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EXTRACTION_SKILL_REPO_PIN_ENV, "a" * 40)
    monkeypatch.setenv(EXTRACTION_SKILL_VERSION_ENV, "v1.7.2")
    version, sha = assert_pin_consistent(env=None, lock=_stub_lock())
    assert version == "v1.7.2"
    assert sha == "a" * 40


@pytest.mark.unit
def test_resolve_skill_pin_falls_back_to_lock(env_off: None) -> None:
    """When env is unset, the lock file is the source of truth so
    tests don't need to plumb env everywhere.
    """
    version, sha = resolve_skill_pin(env={}, lock=_stub_lock())
    assert version == "v1.7.2"
    assert sha == "a" * 40


# ---------------------------------------------------------------------------
# Factory — dark-launch behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_factory_returns_ontology_prompt_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§4.1 dark launch — flag off means ontology prompt unchanged."""
    from nfm_db.services.extraction_prompt import build_ontology_extraction_prompt

    monkeypatch.delenv(EXTRACTION_SKILL_ENABLED_ENV, raising=False)

    fake_ontology = _StubOntologyVersion()

    prompt_off = extract_skill_prompt(
        skill_version=None,
        ontology_version=fake_ontology,  # type: ignore[arg-type]
        lock=_stub_lock(),
        env={},
    )

    prompt_legacy = build_ontology_extraction_prompt(fake_ontology)  # type: ignore[arg-type]
    assert prompt_off == prompt_legacy


@pytest.mark.unit
def test_factory_falls_back_when_skill_flag_on_but_no_local_md(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§2.7 dark-launch tolerance — when the flag is on but the local
    SKILL.md is absent (the production posture until the upstream
    clone lands), the factory logs a warning and falls back to the
    ontology prompt instead of crashing. Pin consistency must hold
    first.
    """
    from nfm_db.services.extraction_skill import (
        SkillsLock,
    )

    monkeypatch.setenv(EXTRACTION_SKILL_ENABLED_ENV, "true")
    monkeypatch.setenv(EXTRACTION_SKILL_REPO_PIN_ENV, "a" * 40)
    monkeypatch.setenv(EXTRACTION_SKILL_VERSION_ENV, "v1.7.2")

    fake_ontology = _StubOntologyVersion()

    # Build a lock whose ``skills`` entrypoint points at a non-existent
    # file. The factory should detect that and fall back.
    lock = SkillsLock(
        catalog_id="x",
        upstream_url="https://example.com/repo.git",
        pin="a" * 40,
        ref=None,
        default_skill="nuclear-property-extraction-v4",
        skills={
            "nuclear-property-extraction-v4": {
                "version": "v1.7.2",
                "entrypoint": "skills/does-not-exist.md",
            }
        },
        raw={},
    )

    with patch(
        "nfm_db.services.extraction_skill._resolve_lock_file_path",
        return_value=tmp_path / "lock.yaml",
    ):
        prompt = extract_skill_prompt(
            skill_version=None,
            ontology_version=fake_ontology,  # type: ignore[arg-type]
            lock=lock,
            env={
                EXTRACTION_SKILL_ENABLED_ENV: "true",
                EXTRACTION_SKILL_REPO_PIN_ENV: "a" * 40,
                EXTRACTION_SKILL_VERSION_ENV: "v1.7.2",
            },
        )

    # Falls back to the ontology prompt — same content as legacy.
    assert "核材料性能数据抽取系统 v4" in prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubOntologyVersion:
    """Mimics the subset of OntologyVersion the factory reads.

    The factory only touches ``ontology_data``; this stub provides a
    minimal valid mapping without forcing tests to import the ORM.
    """

    version = "v0.4.0"
    id = "ov-stub"

    @property
    def ontology_data(self) -> dict[str, Any]:
        return {
            "property_categories": [
                {
                    "name": "Physical properties",
                    "standard_properties": ["formation energy", "binding energy"],
                }
            ],
            "entity_types": [],
            "relation_types": [],
        }
