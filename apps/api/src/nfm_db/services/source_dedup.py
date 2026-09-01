"""Source-dedup service helpers (NFM-4089 — F4 ingest bypass audit).

The NFM-4084 F4 investigation discovered that ``extraction_jobs`` had
been idle since 2026-08-02 yet ``data_sources`` continued to receive
fresh inserts — meaning multiple ingest paths were each creating
duplicate or UUID-titled ``DataSource`` rows independently.

Migration 071 installs a database-level trigger that rejects UUID-titled
rows, but database-side enforcement is the safety net.  These helpers
let callers build a canonical-idempotent ingest path: a single
``get_or_create_source()`` call that walks DOI → file_hash →
content_md-prefix → canonical-insert.  All current callers
(``literature.py:366,461``, ``source_service.py:163``,
``extraction_to_db_mapper.py:722,766``) are encouraged to delegate here.

The priority order matches migration 070's bad-row matching so the
two layers agree on what "the same source" means:

1. DOI equality (the existing ``uq_data_sources_doi`` constraint
   already enforces uniqueness at the schema layer).
2. ``file_hash`` equality — useful when the same PDF is uploaded
   twice under two different DOIs.
3. ``content_md`` LIKE-prefix (first 64 chars) — fallback when no
   DOI/hash survives the upstream pipeline.

The caller always passes a freshly-built ``DataSource(...)`` instance;
this helper inserts it if no canonical row already exists, or returns
the canonical row otherwise.  The caller is responsible for
``await db.commit()`` + ``db.refresh()``.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource

logger = logging.getLogger(__name__)

#: Length of the leading content_md prefix used for fingerprint match.
#: Kept small so the LIKE match is indexable on PG; large enough that
#: random collisions are vanishing.
_CONTENT_MD_PREFIX_LEN = 64


def _normalise_doi(doi: str | None) -> str | None:
    """Return a normalised DOI or ``None`` for empty input.

    Empty / whitespace / literal "None" inputs collapse to ``None`` so
    the dedup logic does not waste a SELECT on a row whose DOI is
    effectively absent.
    """
    if doi is None:
        return None
    cleaned = doi.strip()
    if not cleaned or cleaned.lower() == "none":
        return None
    return cleaned


def _normalise_file_hash(file_hash: str | None) -> str | None:
    """Return a normalised file hash or ``None`` for empty input."""
    if file_hash is None:
        return None
    cleaned = file_hash.strip()
    return cleaned or None


def _normalise_content_md(content_md: str | None) -> str | None:
    """Return a normalised content_md prefix or ``None`` for empty input."""
    if content_md is None:
        return None
    cleaned = content_md.strip()
    if not cleaned:
        return None
    return cleaned[:_CONTENT_MD_PREFIX_LEN]


async def _find_source_by_doi(
    db: AsyncSession, doi: str
) -> DataSource | None:
    """Return the canonical source matching *doi* or ``None``."""
    stmt = select(DataSource).where(DataSource.doi == doi).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_source_by_file_hash(
    db: AsyncSession, file_hash: str
) -> DataSource | None:
    """Return the canonical source matching *file_hash* or ``None``."""
    stmt = (
        select(DataSource)
        .where(DataSource.file_hash == file_hash)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_source_by_content_md_prefix(
    db: AsyncSession, content_md_prefix: str
) -> DataSource | None:
    """Return the canonical source whose ``content_md`` starts with *prefix*.

    Uses an indexable ``LIKE 'prefix%'`` predicate; this is intentionally
    weaker than a full SHA match because the upstream pipeline often
    strips trailing whitespace or boilerplate.
    """
    stmt = (
        select(DataSource)
        .where(DataSource.content_md.like(f"{content_md_prefix}%"))
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_canonical_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    file_hash: str | None = None,
    content_md: str | None = None,
) -> DataSource | None:
    """Return the canonical source matching the highest-priority candidate.

    Walks DOI → file_hash → content_md-prefix in priority order and
    returns the first match, or ``None`` if every lookup misses.
    """
    doi_norm = _normalise_doi(doi)
    if doi_norm:
        existing = await _find_source_by_doi(db, doi_norm)
        if existing is not None:
            logger.debug(
                "dedup hit on doi=%s (source_id=%s)", doi_norm, existing.id
            )
            return existing

    hash_norm = _normalise_file_hash(file_hash)
    if hash_norm:
        existing = await _find_source_by_file_hash(db, hash_norm)
        if existing is not None:
            logger.debug(
                "dedup hit on file_hash=%s (source_id=%s)",
                hash_norm[:12],
                existing.id,
            )
            return existing

    md_prefix = _normalise_content_md(content_md)
    if md_prefix:
        existing = await _find_source_by_content_md_prefix(db, md_prefix)
        if existing is not None:
            logger.debug(
                "dedup hit on content_md prefix len=%d (source_id=%s)",
                len(md_prefix),
                existing.id,
            )
            return existing

    return None


async def get_or_create_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    title: str,
    source_type: str = "unknown",
    file_hash: str | None = None,
    content_md: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[DataSource, bool]:
    """Return ``(canonical_source, created)`` for an ingest candidate.

    If a canonical row already matches any of (doi, file_hash,
    content_md-prefix) it is returned with ``created=False``.  Otherwise
    a fresh ``DataSource`` is added to the session (NOT yet committed)
    and returned with ``created=True``.

    ``source_type`` defaults to ``"unknown"`` so callers do not need to
    know the canonical taxonomy at every call site; downstream code
    that has a real label should override the kwarg.

    The caller is responsible for ``db.commit()`` / ``db.refresh()``.

    The migration-071 DB trigger still applies — a UUID-titled
    candidate reaches the INSERT and is rejected, surfacing a
    ``health_events`` row.  Callers should not rely on this layer for
    UUID-title rejection; the trigger is the defence-in-depth.
    """
    canonical = await find_canonical_source(
        db, doi=doi, file_hash=file_hash, content_md=content_md
    )
    if canonical is not None:
        return canonical, False

    payload: dict[str, Any] = {
        "title": title,
        "doi": _normalise_doi(doi),
        "source_type": source_type,
    }
    if file_hash:
        payload["file_hash"] = file_hash
    if content_md:
        payload["content_md"] = content_md
    if extra:
        payload.update(extra)

    candidate = DataSource(**payload)
    db.add(candidate)
    await db.flush()  # populate candidate.id without committing
    logger.debug(
        "created new DataSource id=%s doi=%s hash=%s",
        candidate.id,
        candidate.doi,
        (candidate.file_hash or "")[:12],
    )
    return candidate, True


__all__ = [
    "find_canonical_source",
    "get_or_create_source",
]
