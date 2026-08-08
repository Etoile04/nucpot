"""Unit tests for paths/base.py — DispatchResult and GapFillPath (NFM-2648).

Tests acceptance criteria:
- DispatchResult is a frozen dataclass with 5 fields
- Type annotations use str | None (not Optional[str])
- GapFillPath is a runtime-checkable Protocol with can_handle() and execute()
- Importable as from nfm_db.services.paths.base import GapFillPath, DispatchResult
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.paths.base import DispatchResult, GapFillPath

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dcr(**overrides: object) -> DataCollectionRequest:
    """Build a mock DataCollectionRequest for testing.

    Uses MagicMock to avoid coupling to the ORM model's column state
    (dispatch columns added by NFM-2647 may not be in the committed schema).
    """
    dcr = MagicMock(spec=DataCollectionRequest)
    dcr.id = "test-dcr-id"
    dcr.source_preference = "literature"
    for key, value in overrides.items():
        setattr(dcr, key, value)
    return dcr


def _make_concrete_handler(
    *,
    can_handle_result: bool = True,
    execute_result: DispatchResult | None = None,
) -> object:
    """Build a concrete GapFillPath implementation for testing."""

    class ConcreteHandler:
        async def can_handle(self, request: DataCollectionRequest) -> bool:  # noqa: ARG002
            return can_handle_result

        async def execute(
            self,
            request: DataCollectionRequest,
        ) -> DispatchResult:
            if execute_result is not None:
                return execute_result
            return DispatchResult(
                success=True,
                path="concrete",
                reference=str(request.id),
            )

    return ConcreteHandler()


# ---------------------------------------------------------------------------
# DispatchResult
# ---------------------------------------------------------------------------


class TestDispatchResult:
    """Test the DispatchResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        """DispatchResult MUST be frozen (immutable)."""
        assert dataclasses.is_dataclass(DispatchResult)
        dr = DispatchResult(success=True, path="test")
        with pytest.raises(dataclasses.FrozenInstanceError):
            dr.success = False  # type: ignore[misc]

    def test_five_fields(self) -> None:
        """DispatchResult has exactly 5 fields: success, path, reference, error, data_found."""
        fields = {f.name for f in dataclasses.fields(DispatchResult)}
        assert fields == {"success", "path", "reference", "error", "data_found"}

    def test_field_types(self) -> None:
        """Field types match spec: bool, str, str|None, str|None, bool.

        Note: with ``from __future__ import annotations``, dataclass field
        types are stored as strings, so we compare string representations.
        """
        field_map = {f.name: f.type for f in dataclasses.fields(DispatchResult)}
        # Annotations are stringified when __future__ annotations is active.
        assert field_map["success"] == "bool"
        assert field_map["path"] == "str"
        assert field_map["reference"] in ("str | None", "Optional[str]")
        assert field_map["error"] in ("str | None", "Optional[str]")
        assert field_map["data_found"] == "bool"

    def test_defaults(self) -> None:
        """Optional fields default to None/False."""
        dr = DispatchResult(success=True, path="test")
        assert dr.reference is None
        assert dr.error is None
        assert dr.data_found is False

    def test_equality(self) -> None:
        """Frozen dataclasses support value equality."""
        a = DispatchResult(success=True, path="lit", reference="r1")
        b = DispatchResult(success=True, path="lit", reference="r1")
        assert a == b

    def test_all_success(self) -> None:
        """Construct with all fields set."""
        dr = DispatchResult(
            success=True,
            path="literature",
            reference="ref-123",
            error=None,
            data_found=True,
        )
        assert dr.success is True
        assert dr.path == "literature"
        assert dr.reference == "ref-123"
        assert dr.error is None
        assert dr.data_found is True

    def test_failure(self) -> None:
        """Construct a failure result."""
        dr = DispatchResult(
            success=False,
            path="dft",
            reference=None,
            error="No DFT provider available",
            data_found=False,
        )
        assert dr.success is False
        assert dr.error == "No DFT provider available"
        assert dr.data_found is False

    def test_repr(self) -> None:
        """DispatchResult has a useful repr."""
        dr = DispatchResult(success=True, path="test")
        r = repr(dr)
        assert "DispatchResult" in r
        assert "success=True" in r
        assert "path='test'" in r


# ---------------------------------------------------------------------------
# GapFillPath Protocol
# ---------------------------------------------------------------------------


class TestGapFillPathProtocol:
    """Test the GapFillPath runtime-checkable Protocol."""

    def test_is_protocol(self) -> None:
        """GapFillPath is a Protocol."""
        assert hasattr(GapFillPath, "_is_protocol")

    def test_is_runtime_checkable(self) -> None:
        """GapFillPath is decorated with @runtime_checkable."""
        assert isinstance(_make_concrete_handler(), GapFillPath)

    def test_concrete_handler_satisfies_protocol(self) -> None:
        """A concrete class with can_handle + execute satisfies GapFillPath."""
        handler = _make_concrete_handler()
        assert isinstance(handler, GapFillPath)

    def test_handler_missing_can_handle_rejected(self) -> None:
        """A class missing can_handle() does NOT satisfy the protocol."""

        class BadHandler:
            async def execute(self, request: DataCollectionRequest) -> DispatchResult:  # noqa: ARG002
                return DispatchResult(success=True, path="bad")

        assert not isinstance(BadHandler(), GapFillPath)

    def test_handler_missing_execute_rejected(self) -> None:
        """A class missing execute() does NOT satisfy the protocol."""

        class BadHandler:
            async def can_handle(self, request: DataCollectionRequest) -> bool:  # noqa: ARG002
                return True

        assert not isinstance(BadHandler(), GapFillPath)

    def test_can_handle_returns_bool(self) -> None:
        """can_handle() returns a bool."""
        handler = _make_concrete_handler(can_handle_result=True)
        dcr = _make_dcr(source_preference="literature")
        assert isinstance(handler, GapFillPath)
        result = asyncio.run(handler.can_handle(dcr))
        assert result is True

    def test_execute_returns_dispatch_result(self) -> None:
        """execute() returns a DispatchResult."""
        expected = DispatchResult(
            success=True,
            path="concrete",
            reference="abc",
            data_found=True,
        )
        handler = _make_concrete_handler(execute_result=expected)
        dcr = _make_dcr()
        result = asyncio.run(handler.execute(dcr))
        assert result == expected


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    """Test that the public API is importable as specified in AC."""

    def test_import_from_base(self) -> None:
        """Importable as 'from nfm_db.services.paths.base import ...'."""
        from nfm_db.services.paths.base import DispatchResult as DR2
        from nfm_db.services.paths.base import GapFillPath as GP2

        assert DR2 is DispatchResult
        assert GP2 is GapFillPath

    def test_import_from_package(self) -> None:
        """Importable as 'from nfm_db.services.paths import ...'."""
        from nfm_db.services.paths import DispatchResult as DR2
        from nfm_db.services.paths import GapFillPath as GP2

        assert DR2 is DispatchResult
        assert GP2 is GapFillPath

    def test_package_all_exports(self) -> None:
        """__all__ contains at minimum DispatchResult and GapFillPath."""
        import nfm_db.services.paths as pkg

        assert "DispatchResult" in pkg.__all__
        assert "GapFillPath" in pkg.__all__
