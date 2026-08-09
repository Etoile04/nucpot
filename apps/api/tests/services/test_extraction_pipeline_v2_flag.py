"""Tests for the EXTRACTION_PIPELINE_V2 feature flag dispatch (NFM-2677-B1).

The strangler-fig pipeline decomposition introduces a new flag that gates
old (legacy) vs. new (B3) extraction pipeline. This flag is intentionally
separate from NFM-2568's ``extraction_v2_enabled`` — the two efforts are
independent and the new flag lives at the call-site, not inside
``trigger_extraction()``.

Covers:
1. ``Settings.extraction_pipeline_v2`` defaults to False.
2. ``is_extraction_v2_enabled()`` returns False by default and reads
   ``NFM_EXTRACTION_PIPELINE_V2`` from the environment.
3. The dispatch wrapper routes to legacy ``trigger_extraction()`` when
   the flag is OFF.
4. The dispatch wrapper routes to ``trigger_extraction_v2()`` when the
   flag is ON.
"""

from __future__ import annotations

from typing import Any

import pytest

from nfm_db.config import Settings, is_extraction_v2_enabled
from nfm_db.services import extraction_pipeline_dispatch as dispatch_mod

# ---------- Settings + helper ------------------------------------------------


def test_settings_extraction_pipeline_v2_defaults_to_false() -> None:
    """Default OFF — strangler-fig must not change behavior for existing callers."""
    s = Settings()
    assert s.extraction_pipeline_v2 is False


def test_is_extraction_v2_enabled_returns_false_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NFM_EXTRACTION_PIPELINE_V2", raising=False)
    is_extraction_v2_enabled.cache_clear()
    try:
        assert is_extraction_v2_enabled() is False
    finally:
        is_extraction_v2_enabled.cache_clear()


def test_is_extraction_v2_enabled_reads_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NFM_EXTRACTION_PIPELINE_V2", "true")
    is_extraction_v2_enabled.cache_clear()
    try:
        assert is_extraction_v2_enabled() is True
    finally:
        is_extraction_v2_enabled.cache_clear()


# ---------- Dispatch wrapper -------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_routes_to_legacy_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF → calls legacy trigger_extraction() with all kwargs."""
    monkeypatch.setenv("NFM_EXTRACTION_PIPELINE_V2", "false")
    is_extraction_v2_enabled.cache_clear()

    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_legacy(**kwargs: Any) -> str:
        calls.append(("legacy", kwargs))
        return "legacy-result"

    async def fake_v2(**kwargs: Any) -> str:
        calls.append(("v2", kwargs))
        return "v2-result"

    monkeypatch.setattr(dispatch_mod, "trigger_extraction", fake_legacy)
    monkeypatch.setattr(dispatch_mod, "trigger_extraction_v2", fake_v2)

    try:
        result = await dispatch_mod.trigger_extraction_pipeline(
            session=None,  # type: ignore[arg-type]
            source_reference="ref-1",
            source_type="doi",
        )
    finally:
        is_extraction_v2_enabled.cache_clear()

    assert result == "legacy-result"
    assert len(calls) == 1
    assert calls[0][0] == "legacy"
    assert calls[0][1]["source_reference"] == "ref-1"
    assert calls[0][1]["source_type"] == "doi"


@pytest.mark.asyncio
async def test_dispatch_routes_to_v2_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON → calls new V2 orchestrator stub (B3 will fill in the body)."""
    monkeypatch.setenv("NFM_EXTRACTION_PIPELINE_V2", "true")
    is_extraction_v2_enabled.cache_clear()

    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_legacy(**kwargs: Any) -> str:
        calls.append(("legacy", kwargs))
        return "legacy-result"

    async def fake_v2(**kwargs: Any) -> str:
        calls.append(("v2", kwargs))
        return "v2-result"

    monkeypatch.setattr(dispatch_mod, "trigger_extraction", fake_legacy)
    monkeypatch.setattr(dispatch_mod, "trigger_extraction_v2", fake_v2)

    try:
        result = await dispatch_mod.trigger_extraction_pipeline(
            session=None,  # type: ignore[arg-type]
            source_reference="ref-2",
            source_type="doi",
        )
    finally:
        is_extraction_v2_enabled.cache_clear()

    assert result == "v2-result"
    assert len(calls) == 1
    assert calls[0][0] == "v2"
    assert calls[0][1]["source_reference"] == "ref-2"
    assert calls[0][1]["source_type"] == "doi"
