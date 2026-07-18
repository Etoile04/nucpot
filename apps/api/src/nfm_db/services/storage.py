"""Pluggable storage backend for literature uploads (NFM-1486).

Defines a :class:`StorageBackend` Protocol and a default
:class:`LocalDiskStorage` implementation rooted at ``LITERATURE_STORAGE_ROOT``
(default ``/app/uploads/literature``).  The factory :func:`get_storage` reads
``LITERATURE_STORAGE_BACKEND`` (default ``"local"``) and returns the matching
backend; an S3 backend is reserved for a later issue (see NFM-1485-2).

All paths returned by ``save`` are *root-relative* (``{datasource_id}/{name}``)
so the storage layer can be swapped without rewriting stored references.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_STORAGE_ROOT = "/app/uploads/literature"
DEFAULT_BACKEND = "local"

# Filename sanitization rules:
#   - Replace any character that is not [A-Za-z0-9._-] with underscore
#   - Strip leading dots so the result is not a hidden file
#   - Collapse consecutive underscores
#   - If the result is empty, the caller falls back to <datasource_id>.pdf
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_LEADING_DOTS_RE = re.compile(r"^\.+")


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe basename.  May return ``""`` if input is empty."""
    replaced = _SAFE_NAME_RE.sub("_", name)
    stripped = _LEADING_DOTS_RE.sub("", replaced)
    collapsed = re.sub(r"_+", "_", stripped).strip("_-") if stripped else ""
    return collapsed


def _raise_traversal(reason: str) -> None:
    """Raise ``ValueError`` for any disallowed filename pattern."""
    raise ValueError(f"Unsafe filename rejected: {reason}")


def _validate_safe_filename(name: str) -> None:
    """Reject names that escape the per-datasource directory.

    Path *separators* are NOT rejected here — the sanitizer collapses them
    into underscores so user filenames like ``sub/dir\\report.pdf`` become a
    safe basename.  Only traversal patterns (``..``) and absolute paths are
    rejected outright.
    """
    if not name:
        return  # caller decides fallback
    if os.path.isabs(name):
        _raise_traversal("absolute path not allowed")
    # Look at every path segment for traversal — separators elsewhere are fine.
    segments = re.split(r"[\\/]+", name)
    for seg in segments:
        if seg == "..":
            _raise_traversal("path traversal not allowed")
    if name.strip() in {".", ".."}:
        _raise_traversal("path traversal not allowed")


# ---------------------------------------------------------------------------
# Protocol + concrete backends
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageBackend(Protocol):
    """Storage abstraction for uploaded literature files.

    All concrete backends store opaque bytes and return *backend-relative*
    paths (i.e. paths relative to the backend's root, not the host filesystem).
    """

    def save(self, datasource_id: UUID, filename: str, data: bytes) -> str:
        """Persist *data* under the datasource's directory and return the relative path."""
        ...

    def read(self, relative_path: str) -> bytes:
        """Return the bytes previously written to *relative_path*."""
        ...

    def delete(self, relative_path: str) -> None:
        """Remove the file at *relative_path*.  Missing files are silently ignored."""
        ...

    def exists(self, relative_path: str) -> bool:
        """Return True iff a file currently exists at *relative_path*."""
        ...


class LocalDiskStorage:
    """Filesystem-backed :class:`StorageBackend` rooted at a configurable directory.

    Files are laid out as ``{root}/{datasource_id}/{sanitized_filename}``.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- helpers -----------------------------------------------------------

    def _resolve(self, relative_path: str) -> Path:
        """Return the absolute path for *relative_path*, raising if it escapes root."""
        candidate = (self.root / relative_path).resolve()
        # Defense in depth: confirm resolved path is still under self.root.
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError(f"Path escapes storage root: {relative_path}") from exc
        return candidate

    def _sanitize_for(self, datasource_id: UUID, filename: str) -> str:
        """Validate, sanitize, and (if needed) fall back to ``{id}.pdf``."""
        _validate_safe_filename(filename)
        safe = _sanitize_filename(filename)
        if not safe:
            safe = f"{datasource_id}.pdf"
        return safe

    # ---- StorageBackend surface ------------------------------------------

    def save(self, datasource_id: UUID, filename: str, data: bytes) -> str:
        """Write *data* under ``{root}/{datasource_id}/{sanitized}``."""
        safe = self._sanitize_for(datasource_id, filename)
        dest_dir = self.root / str(datasource_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / safe
        dest_path.write_bytes(data)
        return f"{datasource_id}/{safe}"

    def read(self, relative_path: str) -> bytes:
        """Read the bytes previously written to *relative_path*."""
        return self._resolve(relative_path).read_bytes()

    def delete(self, relative_path: str) -> None:
        """Remove the file at *relative_path*; missing files are a no-op."""
        path = self._resolve(relative_path)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def exists(self, relative_path: str) -> bool:
        """Return True iff the file currently exists at *relative_path*."""
        return self._resolve(relative_path).is_file()


# ---------------------------------------------------------------------------
# S3 stub (reserved for a later issue)
# ---------------------------------------------------------------------------


class S3Storage:
    """Stub seam for an S3 / MinIO backend (NFM-1485-2+).

    Intentionally not implemented in NFM-1486; constructing one raises
    ``NotImplementedError`` so callers can detect the missing implementation
    during integration rather than silently writing to a fake path.
    """

    def __init__(self, *_: Any, **__: Any) -> None:  # pragma: no cover - stub seam
        raise NotImplementedError(
            "S3Storage is a reserved seam for NFM-1485-2. "
            "Use LocalDiskStorage via get_storage() for now.",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _resolve_root() -> Path:
    """Read ``LITERATURE_STORAGE_ROOT`` (default :data:`DEFAULT_STORAGE_ROOT`)."""
    raw = os.environ.get("LITERATURE_STORAGE_ROOT", DEFAULT_STORAGE_ROOT)
    return Path(raw)


def get_storage() -> StorageBackend:
    """Return the configured :class:`StorageBackend` instance.

    Reads ``LITERATURE_STORAGE_BACKEND`` (default ``"local"``) and dispatches.
    The function returns a fresh :class:`LocalDiskStorage` rooted at the
    resolved storage directory so tests can monkeypatch the env and get a
    new backend bound to the right root.
    """
    backend = os.environ.get("LITERATURE_STORAGE_BACKEND", DEFAULT_BACKEND).lower()
    if backend == "local":
        return LocalDiskStorage(_resolve_root())
    if backend == "s3":
        return S3Storage()  # type: ignore[return-value]
    raise ValueError(f"Unknown LITERATURE_STORAGE_BACKEND: {backend!r}")


__all__ = [
    "DEFAULT_BACKEND",
    "DEFAULT_STORAGE_ROOT",
    "LocalDiskStorage",
    "S3Storage",
    "StorageBackend",
    "get_storage",
]
