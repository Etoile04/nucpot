"""Tests for path handler __init__ exports and base types (NFM-2645)."""

from __future__ import annotations

from nfm_db.services.paths import (
    DISPATCH_PATHS,
    DISPATCH_STATUSES,
    DFTFillPath,
    DispatchResult,
    ExternalDBFillPath,
    GapFillPath,
    LiteratureFillPath,
)
from nfm_db.services.paths.base import DispatchResult as DirectImport


class TestPackageExports:
    """Verify that all expected symbols are importable from the package."""

    def test_dispatch_result_exported(self) -> None:
        assert DispatchResult is DirectImport

    def test_gap_fill_path_exported(self) -> None:
        assert GapFillPath is not None

    def test_literature_fill_path_exported(self) -> None:
        assert LiteratureFillPath is not None

    def test_dft_fill_path_exported(self) -> None:
        assert DFTFillPath is not None

    def test_external_db_fill_path_exported(self) -> None:
        assert ExternalDBFillPath is not None


class TestDispatchResultFrozen:
    """Verify DispatchResult is a frozen (immutable) dataclass."""

    def test_dispatch_result_defaults(self) -> None:
        result = DispatchResult(success=True, path="test")
        assert result.reference is None
        assert result.error is None
        assert result.data_found is False

    def test_dispatch_result_with_all_fields(self) -> None:
        result = DispatchResult(
            success=True,
            path="literature",
            reference="abc-123",
            error=None,
            data_found=False,
        )
        assert result.success is True
        assert result.path == "literature"
        assert result.reference == "abc-123"

    def test_dispatch_result_failure(self) -> None:
        result = DispatchResult(
            success=False,
            path="dft",
            error="something broke",
        )
        assert result.success is False
        assert result.error == "something broke"
        assert result.data_found is False


class TestDispatchConstants:
    """Verify the canonical dispatch path and status constant tuples."""

    def test_dispatch_paths_is_tuple(self) -> None:
        assert isinstance(DISPATCH_PATHS, tuple)

    def test_dispatch_paths_contents(self) -> None:
        assert set(DISPATCH_PATHS) == {"literature", "dft", "external_db"}

    def test_dispatch_statuses_is_tuple(self) -> None:
        assert isinstance(DISPATCH_STATUSES, tuple)

    def test_dispatch_statuses_contents(self) -> None:
        assert set(DISPATCH_STATUSES) == {
            "pending",
            "running",
            "success",
            "failed",
        }
