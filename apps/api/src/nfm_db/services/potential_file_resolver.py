"""Canonical ``file_url`` governance for potentials (NFM-4309 / BUG-37).

Storage spec
============

``potentials.file_url`` stores ONE canonical form — the backend proxy
download path::

    /api/v1/potentials/{id}/file

The URL is anonymous, stable, and identical for the web download button
and direct API consumers (AutoVC).  The *backing storage location* is
NOT the URL: it lives in ``potentials.extra.file_storage``::

    {"kind": "uploads",  "key": "<uuid>.<ext>"}
    {"kind": "supabase", "objects": ["potentials/library/x.eam.alloy", ...]}

``uploads`` keys are relative to the configured upload directory (the
shared ``prod-uploads`` volume mounted at ``/app/uploads`` in prod).
``supabase`` objects are public-storage object paths (or, for foreign
origins, absolute URLs).  Rows with several files (legacy
comma-separated fields) keep the full object list; ``?index=`` selects
which one the proxy serves (default 0).

Historical forms (pre-migration, still tolerated on read)
---------------------------------------------------------

===========================  =======================================
form                         example
===========================  =======================================
``/uploads/<key>``           ``/uploads/<uuid>.tersoff``            (form 1)
``/app/uploads/<file>``      ``/app/uploads/Fe_Mendelev_2007v2.eam.fs`` (form 2, container path)
``/storage/v1/object/public/…``  relative Supabase path           (form 3)
``https://…supabase.co/storage/v1/object/public/…``  absolute     (form 4)
===========================  =======================================

Migration ``083_normalize_potential_file_urls`` rewrites all of them to
the canonical form.  Until then (and for rows written by older code
paths) the resolver falls back to parsing ``file_url`` itself.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from typing import Any
from uuid import UUID

from nfm_db.services.upload_service import PotentialUploadError

#: Canonical proxy path template (the single storage spec, BUG-37 §1).
CANONICAL_FILE_URL_TEMPLATE = "/api/v1/potentials/{potential_id}/file"

#: Default Supabase project origin (matches apps/web/src/lib/file-url.ts).
DEFAULT_SUPABASE_PUBLIC_ORIGIN = "https://gzhiqyopzlmnkdzammhx.supabase.co"

_STORAGE_V1_MARKER = "/storage/v1/object/public/"
_UPLOADS_PREFIX = "/uploads/"
_APP_UPLOADS_PREFIX = "/app/uploads/"
_CANONICAL_PREFIX = "/api/v1/potentials/"

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_RE = re.compile(r"^\\\\[^\\]")


class FileUrlForm(Enum):
    """Classification of a stored ``file_url`` value."""

    EMPTY = "empty"
    FILESYSTEM_PATH = "filesystem_path"  # container/host path — BUG-37 form 2
    UPLOADS_RELATIVE = "uploads_relative"  # BUG-37 form 1
    SUPABASE_RELATIVE = "supabase_relative"  # BUG-37 form 3
    HTTP_ABSOLUTE = "http_absolute"  # BUG-37 form 4 (supabase or foreign)
    CANONICAL_PROXY = "canonical_proxy"  # NFM-4309 spec
    BARE_FILENAME = "bare_filename"


def canonical_file_url(potential_id: UUID | str) -> str:
    """Return the canonical download URL for a potential id."""
    return CANONICAL_FILE_URL_TEMPLATE.format(potential_id=potential_id)


def classify_file_url(url: str | None) -> FileUrlForm:
    """Classify a stored ``file_url`` into one of the historical forms."""
    if url is None or not url.strip():
        return FileUrlForm.EMPTY
    value = url.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return FileUrlForm.HTTP_ABSOLUTE
    if value.startswith("file://") or _WINDOWS_DRIVE_RE.match(value) or _UNC_RE.match(value):
        return FileUrlForm.FILESYSTEM_PATH
    if value.startswith(_CANONICAL_PREFIX):
        return FileUrlForm.CANONICAL_PROXY
    if value.startswith(_APP_UPLOADS_PREFIX):
        return FileUrlForm.FILESYSTEM_PATH
    if value.startswith(_STORAGE_V1_MARKER):
        return FileUrlForm.SUPABASE_RELATIVE
    if value.startswith(_UPLOADS_PREFIX):
        return FileUrlForm.UPLOADS_RELATIVE
    if value.startswith("/"):
        # Any other absolute path is a filesystem/container path.
        return FileUrlForm.FILESYSTEM_PATH
    return FileUrlForm.BARE_FILENAME


def split_file_url_objects(url: str | None) -> list[str]:
    """Split a (possibly comma-separated) multi-object ``file_url``."""
    if not url:
        return []
    return [part.strip() for part in url.split(",") if part.strip()]


def storage_ref_from_file_url(url: str | None) -> dict[str, Any] | None:
    """Map a legacy ``file_url`` onto a ``extra.file_storage`` reference.

    Returns ``None`` when the URL carries no recoverable location
    (empty, bare filename, or canonical proxy without ``extra``).
    """
    form = classify_file_url(url)
    if form is FileUrlForm.UPLOADS_RELATIVE:
        key = url.strip()[len(_UPLOADS_PREFIX) :]  # type: ignore[union-attr]
        return {"kind": "uploads", "key": key} if key else None
    if form is FileUrlForm.FILESYSTEM_PATH:
        # /app/uploads/<file> maps onto the shared uploads volume key.
        value = url.strip()  # type: ignore[union-attr]
        if value.startswith(_APP_UPLOADS_PREFIX):
            key = value[len(_APP_UPLOADS_PREFIX) :]
            return {"kind": "uploads", "key": key} if key else None
        return None
    if form in (FileUrlForm.SUPABASE_RELATIVE, FileUrlForm.HTTP_ABSOLUTE):
        objects: list[str] = []
        for part in split_file_url_objects(url):
            marker_at = part.find(_STORAGE_V1_MARKER)
            if marker_at >= 0:
                # Relative or absolute Supabase path → canonical object path.
                objects.append(part[marker_at + len(_STORAGE_V1_MARKER) :])
            else:
                objects.append(part)
        return {"kind": "supabase", "objects": objects} if objects else None
    return None


def resolve_storage_ref(
    extra: dict[str, Any] | None, file_url: str | None
) -> dict[str, Any] | None:
    """Resolve the backing storage reference for a potential row.

    Prefers the explicit ``extra.file_storage`` reference; falls back to
    parsing a legacy ``file_url`` (unmigrated rows).
    """
    if isinstance(extra, dict):
        ref = extra.get("file_storage")
        if isinstance(ref, dict) and ref.get("kind") in ("uploads", "supabase"):
            return ref
    return storage_ref_from_file_url(file_url)


def public_object_url(obj: str, origin: str | None = None) -> str:
    """Return the fetchable public URL for a Supabase object path."""
    if obj.startswith("http://") or obj.startswith("https://"):
        return obj
    base = origin or os.environ.get(
        "NFM_SUPABASE_PUBLIC_ORIGIN", DEFAULT_SUPABASE_PUBLIC_ORIGIN
    ).rstrip("/")
    return f"{base}{_STORAGE_V1_MARKER}{obj}"


def validate_persistable_file_url(url: str | None) -> str | None:
    """Ingestion shape contract (BUG-37 §4) — refuse container paths.

    Allowed persisted forms:

    * canonical proxy — ``/api/v1/potentials/{id}/file``
    * legacy site-relative — ``/uploads/<key>``
    * legacy Supabase-relative — ``/storage/v1/object/public/<bucket>/…``
    * absolute ``http(s)://`` URLs

    Rejected: container/filesystem paths (``/app/…``, ``/etc/…``,
    ``file://``, Windows/UNC paths), backslash-containing values, bare
    filenames, and empty keys after an allowed prefix.

    Raises:
        PotentialUploadError: the URL is not a persistable download URL.
    """
    if url is None or url.strip() == "":
        return None
    value = url.strip()
    if "\\" in value:
        raise PotentialUploadError(
            "file_url must not contain backslashes (container/filesystem "
            f"path form refused): {value!r}",
        )
    form = classify_file_url(value)
    if form in (
        FileUrlForm.CANONICAL_PROXY,
        FileUrlForm.UPLOADS_RELATIVE,
        FileUrlForm.SUPABASE_RELATIVE,
        FileUrlForm.HTTP_ABSOLUTE,
    ):
        if form is FileUrlForm.UPLOADS_RELATIVE and value == _UPLOADS_PREFIX:
            raise PotentialUploadError(
                "file_url '/uploads/' has an empty storage key",
            )
        return value
    if form is FileUrlForm.FILESYSTEM_PATH:
        raise PotentialUploadError(
            f"file_url must be a download URL, not a container/filesystem path (BUG-37): {value!r}",
        )
    raise PotentialUploadError(
        "file_url must be '/api/v1/potentials/{id}/file', '/uploads/<key>', "
        f"'/storage/v1/object/public/<bucket>/…' or an absolute http(s) URL — "
        f"got {value!r}",
    )
