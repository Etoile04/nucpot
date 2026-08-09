"""Tests for the V2 pipeline feature-flag helper (NFM-2680).

Strangler-fig routing is gated on ``EXTRACTION_PIPELINE_V2`` (config field
``extraction_v2_enabled``).  The dispatcher must:
1. Default OFF (legacy unchanged).
2. Honour the ``NFM_EXTRACTION_V2_ENABLED`` env var (pydantic Settings
   ``env_prefix="NFM_"``).
3. Expose a cheap cached helper so hot-path call-sites don't re-parse
   settings every invocation.
4. Route to the legacy ``trigger_extraction()`` when OFF.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# These imports DO NOT YET EXIST — RED signal.
from nfm_db.services.extraction_pipeline_dispatch import (
    is_extraction_v2_enabled,
    trigger_extraction_pipeline,
)


def test_is_extraction_v2_enabled_defaults_off():
    """No env var set → False."""
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("NFM_EXTRACTION_V2_ENABLED", None)
        from nfm_db.services.extraction_pipeline_dispatch import (
            is_extraction_v2_enabled,
        )
        try:
            is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        assert is_extraction_v2_enabled() is False


def test_is_extraction_v2_enabled_honours_env_var():
    """Setting NFM_EXTRACTION_V2_ENABLED=1 flips the helper to True."""
    with patch.dict("os.environ", {"NFM_EXTRACTION_V2_ENABLED": "1"}):
        from nfm_db.services.extraction_pipeline_dispatch import (
            is_extraction_v2_enabled,
        )
        try:
            is_extraction_v2_enabled.cache_clear()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        assert is_extraction_v2_enabled() is True


def test_trigger_extraction_pipeline_off_routes_to_legacy(monkeypatch):
    """Flag OFF → the dispatcher must call the legacy trigger_extraction."""
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    # Force OFF regardless of host env.
    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: False)

    called = {"legacy": 0}

    async def fake_legacy(*args, **kwargs):
        called["legacy"] += 1
        return {"routed": "legacy", "args": args, "kwargs": kwargs}

    monkeypatch.setattr(
        "nfm_db.services.extraction_pipeline_dispatch.trigger_extraction",
        fake_legacy,
    )

    import asyncio

    result = asyncio.run(
        dispatch_mod.trigger_extraction_pipeline(
            source_reference="foo.md",
            source_type="file",
        )
    )
    assert called["legacy"] == 1
    assert result["routed"] == "legacy"


def test_trigger_extraction_pipeline_on_routes_to_v2(monkeypatch):
    """Flag ON → the dispatcher must route to the V2 orchestrator path."""
    import nfm_db.services.extraction_pipeline_dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "is_extraction_v2_enabled", lambda: True)

    called = {"v2": 0}

    async def fake_v2(*args, **kwargs):
        called["v2"] += 1
        return {"routed": "v2"}

    # The dispatcher should consult this symbol when ON.
    monkeypatch.setattr(dispatch_mod, "_run_v2_pipeline", fake_v2)

    import asyncio

    result = asyncio.run(
        dispatch_mod.trigger_extraction_pipeline(
            source_reference="foo.md",
            source_type="file",
        )
    )
    assert called["v2"] == 1
    assert result["routed"] == "v2"


def test_is_extraction_v2_enabled_is_cached():
    """Repeated calls within one process must not re-parse settings."""
    from nfm_db.services.extraction_pipeline_dispatch import (
        is_extraction_v2_enabled,
    )
    # If @lru_cache is applied, .cache_info / .cache_clear exist.
    assert hasattr(is_extraction_v2_enabled, "cache_info")
    assert hasattr(is_extraction_v2_enabled, "cache_clear")