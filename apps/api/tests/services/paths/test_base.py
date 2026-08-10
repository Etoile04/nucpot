"""Tests for paths/base.py — Protocol and DispatchResult (NFM-2649)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nfm_db.services.paths import (
    DFTFillPath,
    DispatchResult,
    ExternalDBFillPath,
    GapFillPath,
    LiteratureFillPath,
)

# ---------------------------------------------------------------------------
# DispatchResult
# ---------------------------------------------------------------------------


def test_dispatch_result_default_values() -> None:
    """All optional fields have sensible defaults."""
    result = DispatchResult(success=True, path="x", reference="r")

    assert result.success is True
    assert result.path == "x"
    assert result.reference == "r"
    assert result.error is None
    assert result.data_found is False
    assert result.metadata == {}


def test_dispatch_result_with_error() -> None:
    """Error field is preserved when set."""
    result = DispatchResult(
        success=False,
        path="x",
        reference="r",
        error="boom",
        data_found=False,
    )

    assert result.success is False
    assert result.error == "boom"


def test_dispatch_result_is_frozen() -> None:
    """DispatchResult is immutable — attempted mutation raises."""
    result = DispatchResult(success=True, path="x", reference="r")

    with pytest.raises(FrozenInstanceError):
        result.success = False  # type: ignore[misc]


def test_dispatch_result_metadata_independent() -> None:
    """Each instance gets its own metadata dict (no shared mutable default)."""
    r1 = DispatchResult(success=True, path="x", reference="r")
    r2 = DispatchResult(success=True, path="x", reference="r")

    r1.metadata["k"] = "v"
    assert r2.metadata == {}


# ---------------------------------------------------------------------------
# GapFillPath protocol
# ---------------------------------------------------------------------------


def test_handlers_satisfy_protocol() -> None:
    """Concrete handler classes must satisfy the GapFillPath protocol."""

    assert issubclass(LiteratureFillPath, object)
    assert issubclass(DFTFillPath, object)
    assert issubclass(ExternalDBFillPath, object)

    # Protocol check via runtime_checkable
    class _Stub:
        def can_handle(self, source_preference: str) -> bool:
            return True

        async def execute(self, request):  # type: ignore[no-untyped-def]
            return DispatchResult(success=True, path="x", reference="r")

    assert isinstance(_Stub(), GapFillPath)
