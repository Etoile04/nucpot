"""Service layer for the POST /api/ontology/versions registration endpoint (NFM-3591).

The register-version API lets a client atomically:

1. Supply a ``version_tag`` (e.g. ``v1``), ``created_by`` identity, a
   ``source_url`` whose body we will fetch, and a SHA-256 ``checksum``
   in the canonical ``sha256:<hex>`` form.
2. Have the server fetch ``source_url``, recompute the SHA-256 of the
   body, and reject with :class:`ChecksumMismatchError` if the supplied
   checksum does not match.
3. Insert exactly one new ``OntologyVersion`` row in a single
   transaction.

The endpoint deliberately does NOT mutate ``k_entity_types`` or
``k_relation_types`` rows — those are owned by the loader and only the
FK column (``ontology_version_id``) is the joint point between this
service and the rest of the ontology pipeline.  Storing the new metadata
in the existing ``ontology_data`` JSONB column keeps the schema
additive (no destructive migration required by deliverable 5).

Why a dedicated service module?

* Keeps the route handler thin (request → service → response).
* Allows unit tests to drive the checksum logic without spinning up
  FastAPI.
* Funnels the IntegrityError → 409 mapping in one place so we don't
  leak DB-layer exceptions to the API surface.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ontology_version import OntologyVersion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public error types — the route layer maps these to HTTP responses
# ---------------------------------------------------------------------------


class ChecksumMismatchError(ValueError):
    """Raised when the supplied ``checksum`` does not match the fetched body.

    The message includes both the expected and observed checksums so the
    caller can tell at a glance which side is wrong.
    """

    def __init__(self, expected: str, observed: str) -> None:
        super().__init__(f"checksum mismatch: expected {expected}, got {observed}")
        self.expected = expected
        self.observed = observed


class SourceUrlRequiredError(ValueError):
    """Raised when ``source_url`` is missing or empty.

    Per spec: ``Nullable source_url → 400 ('source_url required for
    checksum validation')``.
    """

    def __init__(self) -> None:
        super().__init__("source_url required for checksum validation")


class VersionTagExistsError(ValueError):
    """Raised when the supplied ``version_tag`` collides with an existing row.

    Maps to HTTP 409.  The unique constraint lives on
    ``OntologyVersion.version`` (the DB column that backs the API's
    ``version_tag``); see ``UniqueConstraint('version', ...)`` on the
    model.
    """

    def __init__(self, version_tag: str) -> None:
        super().__init__(f"version_tag already exists: {version_tag!r}")
        self.version_tag = version_tag


class SourceFetchError(RuntimeError):
    """Raised when the source URL could not be fetched.

    Surfaced as a 502 Bad Gateway by the route layer.
    """

    def __init__(self, source_url: str, reason: str) -> None:
        super().__init__(f"failed to fetch {source_url}: {reason}")
        self.source_url = source_url
        self.reason = reason


# ---------------------------------------------------------------------------
# Fetcher abstraction — overridable for tests
# ---------------------------------------------------------------------------


#: Type alias for an async fetcher.  Replaced in tests via
#: ``unittest.mock.patch`` so the test suite never hits the network.
SourceFetcher = Callable[[str], Awaitable[bytes]]


async def _fetch_source_body(source_url: str) -> bytes:
    """Fetch ``source_url`` and return its raw bytes.

    Supports ``http://`` / ``https://`` via ``httpx``.  Local ``file://``
    URLs are accepted for offline fixtures.  All other schemes raise
    :class:`SourceFetchError`.
    """
    parsed = urlparse(source_url)
    if parsed.scheme in ("http", "https"):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(source_url, follow_redirects=True)
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as exc:
            raise SourceFetchError(source_url, str(exc)) from exc
    if parsed.scheme == "file":
        from pathlib import Path

        try:
            return Path(parsed.path).read_bytes()
        except OSError as exc:
            raise SourceFetchError(source_url, str(exc)) from exc
    raise SourceFetchError(source_url, f"unsupported scheme: {parsed.scheme!r}")


# ---------------------------------------------------------------------------
# Public service function
# ---------------------------------------------------------------------------


def _parse_checksum(value: str) -> str:
    """Validate ``checksum`` format ``sha256:<hex>`` and return the hex part.

    Raises :class:`ValueError` if the prefix is missing or the hex is not
    exactly 64 lowercase/uppercase hex chars.
    """
    if not isinstance(value, str) or ":" not in value:
        raise ValueError(f"checksum must be 'sha256:<hex>' (got {value!r})")
    scheme, _, hex_part = value.partition(":")
    if scheme.lower() != "sha256":
        raise ValueError(f"checksum scheme must be 'sha256' (got {scheme!r})")
    if len(hex_part) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hex_part):
        raise ValueError("checksum hex must be exactly 64 hex chars")
    return hex_part


async def register_ontology_version(
    session: AsyncSession,
    *,
    version_tag: str,
    created_by_user_id: uuid.UUID,
    created_by_display: str,
    source_url: str | None,
    checksum: str,
    fetcher: SourceFetcher | None = None,
) -> OntologyVersion:
    """Register a new ontology version with SHA-256 source validation.

    Args:
        session: Async SQLAlchemy session.  The service commits the
            transaction so the IntegrityError → VersionTagExistsError
            translation happens inside the same DB transaction boundary
            (single-row insert per AC).
        version_tag: API-facing identifier (mapped to the model's
            ``version`` column).
        created_by_user_id: The authenticated user's UUID (FK to
            ``users.id``).  Resolved by the route layer from the auth
            dependency.
        created_by_display: Free-form display string from the request
            body (e.g. email or agent id).  Persisted inside
            ``ontology_data`` for response echo since the row's FK
            column carries the UUID instead.
        source_url: URL whose body we must fetch and verify; ``None`` or
            empty triggers :class:`SourceUrlRequiredError`.
        checksum: Expected SHA-256 of the body, formatted
            ``sha256:<hex>``.
        fetcher: Optional override for the source-body fetcher.  Used
            by tests to avoid network IO.  Defaults to
            :func:`_fetch_source_body`.

    Returns:
        The newly inserted :class:`OntologyVersion` row.

    Raises:
        SourceUrlRequiredError: ``source_url`` is missing.
        ChecksumMismatchError: ``checksum`` does not match the fetched body.
        VersionTagExistsError: ``version_tag`` collides with an existing row.
        SourceFetchError: The body could not be fetched.
    """
    if not source_url:
        raise SourceUrlRequiredError()

    expected_hex = _parse_checksum(checksum)

    fetch = fetcher or _fetch_source_body
    body = await fetch(source_url)
    observed_hex = hashlib.sha256(body).hexdigest()
    observed = f"sha256:{observed_hex}"
    if observed_hex.lower() != expected_hex.lower():
        raise ChecksumMismatchError(expected=checksum, observed=observed)

    # Persist the new row.  We store source_url + checksum inside the
    # existing ``ontology_data`` JSONB so we don't need a destructive
    # schema migration (per deliverable 5: "endpoint only inserts a new
    # version row; does NOT mutate existing k_entity_types /
    # k_relation_types rows").  ``created_by_display`` is the free-form
    # identity string from the request body — preserved for response
    # echo since the row's FK column carries the authenticated user's
    # UUID.
    payload: dict[str, Any] = {
        "source_url": source_url,
        "checksum": checksum,
        "created_by_raw": created_by_display,
    }

    ov = OntologyVersion(
        version=version_tag,
        status="draft",
        changelog=None,
        ontology_data=payload,
        created_by=created_by_user_id,
    )

    session.add(ov)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # The unique constraint on ``version`` is what enforces
        # duplicate ``version_tag`` detection (model declares
        # ``UniqueConstraint('version', name='uq_ontology_versions_version')``).
        # Translate the driver-level error into our domain error.
        if "uq_ontology_versions_version" in str(exc.orig) or "UNIQUE" in str(exc.orig).upper():
            raise VersionTagExistsError(version_tag) from exc
        raise
    await session.refresh(ov)
    return ov


__all__ = [
    "ChecksumMismatchError",
    "SourceFetchError",
    "SourceFetcher",
    "SourceUrlRequiredError",
    "VersionTagExistsError",
    "_fetch_source_body",
    "register_ontology_version",
]
