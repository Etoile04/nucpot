"""Tests for :mod:`nfm_db.core.path_safety`.

NFM-2781 HOTFIX CR1 — defends the source-text endpoint against
path-traversal attacks via ``chunk.source_reference``.

Each test names the production code change that would make it fail.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nfm_db.core.path_safety import (
    PathNotAllowedError,
    safe_resolve,
)


@pytest.fixture()
def allowlist_dir(tmp_path: Path) -> Path:
    """Create an allowlist base directory."""
    base = tmp_path / "sources"
    base.mkdir()
    return base


class TestSafeResolveHappyPaths:
    """safe_resolve() resolves allowed paths and returns the resolved Path."""

    def test_returns_resolved_path_for_absolute_path_inside_allowlist(
        self,
        allowlist_dir: Path,
    ) -> None:
        """An absolute path that lives inside the allowlist is accepted."""
        target = allowlist_dir / "file.txt"
        target.write_text("hello")

        resolved = safe_resolve(str(target), allowlist_dir)

        assert resolved == target.resolve()
        assert resolved.is_file()

    def test_accepts_relative_path_inside_allowlist(
        self,
        allowlist_dir: Path,
    ) -> None:
        """A relative path inside the allowlist (cwd == allowlist) is accepted."""
        target = allowlist_dir / "inner.txt"
        target.write_text("world")

        old_cwd = os.getcwd()
        try:
            os.chdir(allowlist_dir)
            resolved = safe_resolve("inner.txt", allowlist_dir)
        finally:
            os.chdir(old_cwd)

        assert resolved == target.resolve()


class TestSafeResolveRejections:
    """safe_resolve() raises PathNotAllowedError for escape attempts."""

    def test_rejects_parent_directory_escape(
        self,
        allowlist_dir: Path,
    ) -> None:
        """A ``..`` chain that escapes the allowlist is rejected."""
        with pytest.raises(PathNotAllowedError) as exc_info:
            safe_resolve("../../../../etc/passwd", allowlist_dir)

        assert "parent" in str(exc_info.value).lower() or "escape" in str(
            exc_info.value
        ).lower()

    def test_rejects_absolute_path_outside_allowlist(
        self,
        allowlist_dir: Path,
    ) -> None:
        """An absolute path to a file outside the allowlist is rejected."""
        # safe_resolve() must reject based on the resolved location, not
        # file existence.
        with pytest.raises(PathNotAllowedError) as exc_info:
            safe_resolve("/etc/passwd", allowlist_dir)

        assert exc_info.value.attempted_path == "/etc/passwd"
        assert "outside" in str(exc_info.value).lower() or "escape" in str(
            exc_info.value
        ).lower()

    def test_rejects_symlink_escape(
        self,
        allowlist_dir: Path,
    ) -> None:
        """A symlink inside the allowlist that points outside is rejected."""
        link_path = allowlist_dir / "innocent.txt"

        # Build a symlink whose realpath escapes the allowlist. Use a tmp
        # location one level up (which itself lives under tmp_path, outside
        # the allowlist) as the link target.
        outside_file = allowlist_dir.parent / "outside.txt"
        outside_file.write_text("outside")

        try:
            link_path.symlink_to(outside_file)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            pytest.skip(f"symlinks not supported in this environment: {exc}")

        with pytest.raises(PathNotAllowedError) as exc_info:
            safe_resolve(str(link_path), allowlist_dir)

        assert "symlink" in str(exc_info.value).lower() or "escape" in str(
            exc_info.value
        ).lower()


class TestPathNotAllowedError:
    """The exception carries enough context for safe logging."""

    def test_carries_attempted_path_and_reason(self) -> None:
        err = PathNotAllowedError(
            attempted_path="/etc/passwd",
            reason="absolute path outside allowlist",
        )
        assert err.attempted_path == "/etc/passwd"
        assert "outside" in err.reason
        assert "/etc/passwd" in str(err)
