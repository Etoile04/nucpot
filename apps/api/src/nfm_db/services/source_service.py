"""Service layer for data source CRUD operations (NFM-698).

Provides async functions for listing, retrieving, and creating
data sources with eager-loaded author relationships.

NFM-4089 (F4 followup): also exposes :func:`get_or_create_source` and the
``_find_source_by_*`` lookup helpers so every known ingest path (PDF upload,
DOI fetch, admin POST /sources, extraction mapper with-DOI, extraction
mapper no-DOI) goes through a single dedup gate before INSERT.
"""

import hashlib
import logging
import re
import uuid
from typing import Any, Literal

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nfm_db.models import DataSource, DataSourceAuthor
from nfm_db.schemas.common import PaginatedResponse
from nfm_db.schemas.source import (
    AuthorResponse,
    DataSourceAuthorResponse,
    DataSourceCreate,
    DataSourceDetailResponse,
    DataSourceResponse,
)

logger = logging.getLogger(__name__)

# NFM-4089 AC2: cap how much text we hash for content_fingerprint dedup.
# Beyond ~64 KB the chance of accidental collision rises sharply while the
# per-ingest cost of the SHA-256 becomes noticeable.
_CONTENT_FINGERPRINT_CAP_BYTES = 65536


async def list_sources(
    db: AsyncSession,
    *,
    year: int | None = None,
    source_type: str | None = None,
    ontology_version: str | None = None,
    page: int = 1,
    per_page: int = 20,
    sort: str = "created_at",
    order: Literal["asc", "desc"] = "desc",
) -> PaginatedResponse[DataSourceResponse]:
    """Return a paginated, filtered list of data sources.

    ontology_version (NFM-3478 s3) matches against the JSONB
    DataSource.metadata_.extraction_ontology_version key set by
    process_literature (s2-lit-ov, #1009). On Postgres uses JSONB
    containment (`@>`, indexable). On other dialects (sqlite tests) we
    fetch the candidate rows without the JSONB predicate and post-filter
    in Python — only the production deployment hits the JSONB path.
    """

    stmt = select(DataSource)

    if year is not None:
        stmt = stmt.where(DataSource.year == year)

    if source_type is not None:
        stmt = stmt.where(DataSource.source_type == source_type)

    sqlite_post_filter: bool = False
    if ontology_version is not None:
        bind_value = f'{{"extraction_ontology_version": "{ontology_version}"}}'
        if db.bind and db.bind.dialect.name == "postgresql":
            stmt = stmt.where(
                text("metadata_ @> CAST(:ov_json AS jsonb)").bindparams(
                    bindparam("ov_json", bind_value)
                )
            )
        else:
            # sqlite test fallback — note we will Python-post-filter below
            sqlite_post_filter = True

    sort_column = {
        "created_at": DataSource.created_at,
        "updated_at": DataSource.updated_at,
        "title": DataSource.title,
        "year": DataSource.year,
    }.get(sort, DataSource.created_at)

    direction = sort_column.desc() if order == "desc" else sort_column.asc()
    stmt = stmt.order_by(direction)

    # Count total matching rows
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)
    rows = (await db.execute(stmt)).scalars().all()

    # sqlite test path — only relevant when ontology_version filter was set
    # and we're not on Postgres. Production hits the JSONB predicate above
    # and never enters this branch.
    if sqlite_post_filter and ontology_version is not None:
        rows = [
            r
            for r in rows
            if isinstance(r.metadata_, dict)
            and r.metadata_.get("extraction_ontology_version") == ontology_version
        ]
        total = len(rows)

    items = [DataSourceResponse.model_validate(r) for r in rows]
    pages = max(1, -(-total // per_page)) if total > 0 else 0  # ceil

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=per_page,
        pages=pages,
    )


async def get_source(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> DataSourceDetailResponse | None:
    """Return a single source with authors, ordered by author_order.

    Uses selectinload for the DataSource → DataSourceAuthor → Author chain.
    Returns None if not found.
    """

    stmt = (
        select(DataSource)
        .options(selectinload(DataSource.data_source_authors).selectinload(DataSourceAuthor.author))
        .where(DataSource.id == source_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()

    if row is None:
        return None

    # Build the flat source response from the row (no relationship access)
    source_resp = DataSourceResponse.model_validate(row)

    # Build author responses from eagerly-loaded junction entries
    sorted_links = sorted(row.data_source_authors, key=lambda x: x.author_order)
    authors = [
        DataSourceAuthorResponse(
            id=link.id,
            data_source_id=link.data_source_id,
            author_id=link.author_id,
            author_order=link.author_order,
            is_corresponding=link.is_corresponding,
            created_at=link.created_at,
            updated_at=link.updated_at,
            author=AuthorResponse.model_validate(link.author),
        )
        for link in sorted_links
    ]

    return DataSourceDetailResponse(
        **source_resp.model_dump(),
        authors=authors,
    )


async def create_source(
    db: AsyncSession,
    data: DataSourceCreate,
) -> DataSourceResponse:
    """Create a new data source and return the response.

    NFM-4089 AC2: now routed through :func:`get_or_create_source` so the
    admin ``POST /sources`` endpoint cannot accidentally re-insert a source
    that already exists by DOI / file_hash / content_md.  If a match is
    found we return that row as-is (no UPDATE) — callers asking "create"
    with a duplicate key receive the existing record back, matching the
    idempotency contract used by the PDF/DOI upload endpoints.
    """

    payload = data.model_dump()
    source, was_created = await get_or_create_source(
        db,
        title=payload["title"],
        doi=payload.get("doi"),
        file_hash=payload.get("file_hash"),
        content_md=payload.get("content_md"),
        source_type=payload.get("source_type", "other"),
        # All remaining payload fields (journal, year, abstract, etc.) are
        # applied only on first INSERT — existing rows are returned unchanged.
        fields={
            k: v
            for k, v in payload.items()
            if k not in {"doi", "title", "source_type", "file_hash", "content_md"}
        },
    )
    if was_created:
        await db.commit()
        await db.refresh(source)
    # When ``was_created`` is False the helper returned a pre-existing row
    # without ``db.add``-ing anything, so there is nothing to commit or roll
    # back.  The session remains usable for follow-up work in the caller.

    return DataSourceResponse.model_validate(source)


# ---------------------------------------------------------------------------
# NFM-4089 AC2 — dedup helpers
#
# Single ingest gate for every code path that wants to write a row to
# ``data_sources``.  Dedup order is DOI → file_hash → content_md fingerprint,
# matching the existing unique constraint (``uq_data_sources_doi``) plus the
# soft constraints implied by the upload-path idempotency check.  Callers
# still own the transaction: this function never commits, only ``db.add``'s a
# new row when no existing match is found.
# ---------------------------------------------------------------------------


async def _find_source_by_doi(
    db: AsyncSession,
    doi: str,
) -> DataSource | None:
    """Find an existing :class:`DataSource` by DOI.

    Returns ``None`` when no match.  ``doi`` must be non-empty — callers
    should pre-filter ``None`` to keep the SQL plan trivial.
    """
    stmt = select(DataSource).where(DataSource.doi == doi)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_source_by_file_hash(
    db: AsyncSession,
    file_hash: str,
) -> DataSource | None:
    """Find an existing :class:`DataSource` by SHA-256 ``file_hash``.

    The ``file_hash`` column has no UNIQUE constraint today (NFM-4089), so
    this lookup is opportunistic — it short-circuits accidental re-ingest of
    an identical PDF/Markdown payload but does not guarantee uniqueness.
    We return the first match by primary key without any ordering, which is
    deterministic and avoids relying on ``updated_at`` when two near-simultaneous
    inserts share the same value.
    """
    stmt = select(DataSource).where(DataSource.file_hash == file_hash).limit(1)
    return (await db.execute(stmt)).scalars().first()


def _content_fingerprint(content_md: str) -> str:
    """SHA-256 over the first ``_CONTENT_FINGERPRINT_CAP_BYTES`` of content.

    Cheap fingerprint used by :func:`_find_source_by_content_fingerprint` for
    the DOI-less / file_hash-less ingest path.  We do NOT store the fingerprint
    on the row (would require a schema change); instead we hash on lookup.
    Two sources that share a fingerprint are very likely the same literature.
    """
    sample = content_md.encode("utf-8")[:_CONTENT_FINGERPRINT_CAP_BYTES]
    return hashlib.sha256(sample).hexdigest()


async def _find_source_by_content_fingerprint(
    db: AsyncSession,
    content_md: str,
) -> DataSource | None:
    """Find an existing :class:`DataSource` whose ``content_md`` matches.

    Scans the most-recently-updated rows that have a non-null
    ``content_md`` and compares fingerprints in Python.  Only used when DOI /
    file_hash are unavailable, which is rare; we accept the O(N) cost.
    """
    if not content_md:
        return None
    target_fp = _content_fingerprint(content_md)
    stmt = (
        select(DataSource)
        .where(DataSource.content_md.is_not(None))
        .order_by(DataSource.updated_at.desc())
        .limit(50)
    )
    for row in (await db.execute(stmt)).scalars().all():
        if row.content_md is None:
            continue
        if _content_fingerprint(row.content_md) == target_fp:
            return row
    return None


#: NFM-4088 (lifted from extraction_to_db_mapper) — guard against
#: UUID-pattern ``title``. Root cause: prior source's primary-key
#: string was being copied into the new row's ``title`` when the
#: extraction pipeline emitted a UUID instead of a real reference.
#: Canonical 36-char UUID, case-insensitive, anchored on both ends.
_UUID_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _reject_uuid_title(title: str) -> None:
    """Raise ``ValueError`` when ``title`` is a 36-char UUID string.

    NFM-4088 AC-4 (write-path guard).

    The pre-fix DOI-empty branch silently inserted a row whose ``title``
    was the primary-key UUID of another source. Migration 070 cleans the
    existing rows; this guard prevents the regression from re-emerging
    inside :func:`get_or_create_source`. We refuse the INSERT rather than
    silently substitute because the only way the title can be a UUID is a
    logic bug in the upstream extraction chain — substituting a different
    label would mask that bug.
    """
    if _UUID_TITLE_PATTERN.match(title):
        raise ValueError(
            f"Refusing to create DataSource with UUID-pattern title={title!r}. "
            "The upstream extraction chain supplied a UUID instead of a "
            "literature reference; investigate the extractor before retrying."
        )


async def _find_source_by_title(
    db: AsyncSession,
    title: str,
) -> DataSource | None:
    """Find an existing :class:`DataSource` by exact ``title`` equality.

    NFM-4088 AC-3 (write-path guard fallback 1).

    Returns at most one row; the ``data_sources`` table has no UNIQUE
    constraint on ``title`` (only ``doi``) so two rows may legitimately
    share a title in legacy states. We use ``.first()`` rather than
    ``scalar_one_or_none()`` to avoid raising ``MultipleResultsFound``
    (mirrors the NFM-3919 dedup-by-formula pattern).
    """
    if not title:
        return None
    stmt = select(DataSource).where(DataSource.title == title).limit(1)
    return (await db.execute(stmt)).scalars().first()


async def _find_source_by_content_md_prefix(
    db: AsyncSession,
    source_file: str | None,
) -> DataSource | None:
    """Find an existing :class:`DataSource` by ``source_file`` substring match.

    NFM-4088 AC-3 (write-path guard fallback 2).

    When ``source_file`` is a Markdown path the NFM-1486 PDF pipeline
    uploaded, the corresponding ``content_md`` column holds the parsed
    text and was uploaded under the same file. We match by
    ``LIKE '%<basename>%'`` — exact equality is unreliable across
    absolute-vs-relative paths.

    Returns ``None`` when ``source_file`` is absent or no row matches.
    """
    if not source_file:
        return None
    basename = source_file.rstrip("/").split("/")[-1]
    if not basename or len(basename) < 4:
        return None
    stmt = (
        select(DataSource)
        .where(DataSource.content_md.is_not(None))
        .where(DataSource.content_md.like(f"%{basename}%"))
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def get_or_create_source(
    db: AsyncSession,
    *,
    title: str,
    doi: str | None = None,
    file_hash: str | None = None,
    content_md: str | None = None,
    source_file: str | None = None,
    source_type: str = "other",
    fields: dict[str, Any] | None = None,
) -> tuple[DataSource, bool]:
    """Return an existing :class:`DataSource` matching the inputs, or create one.

    NFM-4089 AC2 + NFM-4088 AC-3/AC-4: this is the single ingest gate every
    code path must funnel through before INSERTing into ``data_sources``.
    It replaces ad-hoc ``DataSource(...)`` + ``db.add(...)`` blocks in:

      * ``apps/api/src/nfm_db/api/v1/literature.py`` (PDF upload, DOI ingest)
      * ``apps/api/src/nfm_db/services/source_service.py`` (POST /sources)
      * ``apps/api/src/nfm_db/services/extraction_to_db_mapper.py`` (with-DOI, no-DOI)

    Dedup order (applied in this exact sequence):

        0. **Fail-fast:** raise ``ValueError`` when ``title`` matches the
           canonical 36-char UUID regex (NFM-4088 AC-4).  Refusing the
           INSERT keeps the regression from re-emerging.
        1. ``doi`` (matches ``uq_data_sources_doi``; DOI-present branch).
        2. ``file_hash`` (no DB constraint today — opportunistic).
        3. ``title`` exact equality (NFM-4088 AC-3 fallback 1 — catches
           placeholder-reuse: ``"Unattributed source (no DOI)"`` collapses
           to one canonical row across reruns).
        4. ``content_md`` LIKE-prefix against the ``source_file`` basename
           (NFM-4088 AC-3 fallback 2 — NFM-1486 PDF upload pipeline).
        5. ``content_md`` SHA-256 fingerprint over the first 64 KB
           (NFM-4089 opportunistic; expensive, last resort).

    When every lookup misses, a fresh :class:`DataSource` is added to the
    session using ``title``, ``doi``, ``file_hash``, ``content_md``,
    ``source_type`` plus any extras passed via ``fields``.  The caller is
    responsible for ``db.flush()`` / ``db.commit()`` so existing transaction
    semantics are preserved.

    Parameters
    ----------
    db:
        Active async session.
    title:
        Human-readable title; used both for the lookup-miss INSERT and for
        callers that want to attach a label after the fact.
    doi:
        Optional DOI; primary dedup key.
    file_hash:
        Optional SHA-256 of the stored artifact (PDF bytes or Markdown bytes).
    content_md:
        Optional extracted Markdown; used for fingerprint dedup when DOI is
        missing and ``file_hash`` differs across ingest runs.
    source_file:
        Optional ingest-time path or basename (``item.source_file`` from
        the extraction mapper).  Used for the LIKE-prefix dedup against
        ``content_md`` (NFM-1486 case).  Distinct from ``content_md``
        which holds the actual parsed text.
    source_type:
        One of ``VALID_SOURCE_TYPES`` (``schemas.source``).  Defaults to
        ``"other"`` to match the pre-existing extraction-mapper behaviour.
    fields:
        Extra :class:`DataSource` column values to set on a fresh row only
        (ignored when an existing row is returned).  Useful for attaching
        ``file_path``, ``parse_status``, ``original_filename`` etc. without
        a separate update.

    Returns
    -------
    tuple[DataSource, bool]
        ``(source, created)`` — ``created`` is ``True`` only when this call
        actually added a new row to the session.
    """
    # 0. NFM-4088 AC-4: refuse INSERT when title is a UUID string.  This
    # is the regression sentinel — the only way an ingest path lands a
    # UUID in the title column is a logic bug in the upstream chain.
    _reject_uuid_title(title)

    if doi:
        existing = await _find_source_by_doi(db, doi)
        if existing is not None:
            return existing, False
    if file_hash:
        existing = await _find_source_by_file_hash(db, file_hash)
        if existing is not None:
            return existing, False
    if not doi:
        # NFM-4088 AC-3 fallback 1: title-exact dedup (placeholder reuse).
        existing = await _find_source_by_title(db, title)
        if existing is not None:
            return existing, False
        # NFM-4088 AC-3 fallback 2: source-file basename LIKE on content_md
        # (NFM-1486 PDF upload pipeline).  Only when source_file is set and
        # the baseline title-exact lookup missed.
        existing = await _find_source_by_content_md_prefix(db, source_file)
        if existing is not None:
            return existing, False
    if not doi and content_md:
        existing = await _find_source_by_content_fingerprint(db, content_md)
        if existing is not None:
            return existing, False

    payload: dict[str, Any] = {
        "doi": doi,
        "title": title,
        "source_type": source_type,
    }
    if file_hash is not None:
        payload["file_hash"] = file_hash
    if content_md is not None:
        payload["content_md"] = content_md
    if fields:
        # Allow callers to override defaults, but never let `fields` clobber
        # the dedup keys we just used to look up.
        for key in ("doi", "title", "source_type", "file_hash", "content_md"):
            fields.pop(key, None)
        payload.update(fields)

    source = DataSource(**payload)
    db.add(source)
    return source, True
