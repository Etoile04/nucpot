"""Unit tests for the local-disk storage backend.

Covers the NFM-1486 acceptance criteria for the storage seam:
- save() persists bytes under {root}/{id}/{sanitized}, returns relative path
- read() returns the original bytes
- delete() removes the file
- exists() returns the right bool
- path-traversal filenames raise ValueError
- empty / whitespace-only filenames fall back to <id>.pdf
- get_storage() returns LocalDiskStorage when LITERATURE_STORAGE_BACKEND is unset
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from nfm_db.services.storage import (
    LocalDiskStorage,
    StorageBackend,
    get_storage,
)

# ---------------------------------------------------------------------------
# LocalDiskStorage
# ---------------------------------------------------------------------------


def test_local_disk_storage_save_writes_file_under_id_dir(tmp_path: Path) -> None:
    """save() creates {root}/{datasource_id}/{sanitized} and returns relative path."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)
    data = b"%PDF-1.4 hello world"

    returned = storage.save(ds_id, "report.pdf", data)

    expected_abs = tmp_path / str(ds_id) / "report.pdf"
    assert expected_abs.is_file()
    assert expected_abs.read_bytes() == data
    # Returned path must be ROOT-RELATIVE — i.e. {ds_id}/{sanitized}
    assert returned == f"{ds_id}/report.pdf"
    # And the returned path must resolve to the actual file
    assert (tmp_path / returned).is_file()


def test_local_disk_storage_save_strips_path_separators(tmp_path: Path) -> None:
    """Forward/back slashes in the original filename must not become real path components."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)

    returned = storage.save(ds_id, "sub/dir\\report.pdf", b"x")

    # All separators should collapse to a single safe basename
    leaf = returned.split("/")[-1]
    assert "/" not in leaf
    assert "\\" not in leaf
    assert leaf.endswith(".pdf")
    assert (tmp_path / returned).is_file()


def test_local_disk_storage_save_falls_back_when_sanitize_yields_empty(tmp_path: Path) -> None:
    """A filename that sanitizes to empty must fall back to {datasource_id}.pdf."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)

    # "..." strips leading dots → empty
    returned = storage.save(ds_id, "...", b"x")

    assert returned == f"{ds_id}/{ds_id}.pdf"
    assert (tmp_path / returned).is_file()


def test_local_disk_storage_read_returns_bytes(tmp_path: Path) -> None:
    """read(path) returns the bytes originally passed to save()."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)
    data = b"binary payload \x00\x01\x02"
    rel = storage.save(ds_id, "x.bin", data)

    assert storage.read(rel) == data


def test_local_disk_storage_delete_removes_file(tmp_path: Path) -> None:
    """delete() removes the underlying file."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)
    rel = storage.save(ds_id, "x.bin", b"abc")
    assert (tmp_path / rel).is_file()

    storage.delete(rel)
    assert not (tmp_path / rel).exists()


def test_local_disk_storage_delete_missing_is_noop(tmp_path: Path) -> None:
    """delete() on a missing path should not raise (idempotent)."""
    storage = LocalDiskStorage(root=tmp_path)

    storage.delete(f"{uuid.uuid4()}/nope.bin")  # no exception


def test_local_disk_storage_exists_reports_correct_bool(tmp_path: Path) -> None:
    """exists() returns True only for files currently on disk."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)
    rel = storage.save(ds_id, "x.bin", b"hi")

    assert storage.exists(rel) is True
    storage.delete(rel)
    assert storage.exists(rel) is False
    assert storage.exists(f"{uuid.uuid4()}/never-was.bin") is False


def test_local_disk_storage_rejects_path_traversal_filename(tmp_path: Path) -> None:
    """A filename containing '..' traversal must be rejected with ValueError."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)

    with pytest.raises(ValueError):
        storage.save(ds_id, "../../etc/passwd", b"x")


def test_local_disk_storage_rejects_absolute_path_filename(tmp_path: Path) -> None:
    """An absolute-path filename must be rejected with ValueError."""
    ds_id = uuid.uuid4()
    storage = LocalDiskStorage(root=tmp_path)

    with pytest.raises(ValueError):
        storage.save(ds_id, "/etc/passwd", b"x")


def test_local_disk_storage_protocol_satisfaction(tmp_path: Path) -> None:
    """LocalDiskStorage must satisfy the StorageBackend Protocol."""
    storage = LocalDiskStorage(root=tmp_path)
    assert isinstance(storage, StorageBackend)


# ---------------------------------------------------------------------------
# get_storage() factory
# ---------------------------------------------------------------------------


def test_get_storage_returns_local_disk_when_backend_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default backend (env unset) must produce a LocalDiskStorage instance."""
    monkeypatch.delenv("LITERATURE_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("LITERATURE_STORAGE_ROOT", str(tmp_path))

    backend = get_storage()

    assert isinstance(backend, LocalDiskStorage)
