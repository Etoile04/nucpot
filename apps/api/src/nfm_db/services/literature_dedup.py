"""Literature ingestion dedup layer (NFM-4549, G1-C).

Three concerns, one module:

1. ``normalize_doi`` — canonicalize a raw DOI string (strip URL prefix,
   whitespace, ``doi:`` scheme, lowercase) so the dedup lookup is
   invariant to the way a publisher embeds it.
2. ``resolve_literature_dataset`` — find the existing :class:`Dataset`
   row for a freshly-ingested literature, following the
   ``DOI → content_hash → miss`` ladder from ADR-017 §2.5.
3. ``compute_dedupe_key`` — deterministic hash of
   ``(dataset_id, property_type_id, source_id, value_hash)`` that backs
   the ``uq_property_measurements_dedupe_key`` unique constraint added
   by migration 085. This is the writer-side of the (dataset, property,
   source, value_hash) composite key from ADR-017 §2.6.

The DOI→content_hash fallback chain is implemented in service rather
than in SQL because the DOI column needs Unicode/case/whitespace
normalization before lookup — easier to do once in Python and query
the already-normalized form.
"""
from __future__ import annotations

import hashlib
import logging
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.property import Dataset

logger = logging.getLogger(__name__)

#: A bare DOI looks like ``10.NNNN/<suffix>``. We use this to decide
#: whether a cleaned string is actually a DOI or just residual text.
_DOI_BODY_RE = re.compile(r"^10\.\d{4,9}/\S+$")

#: URL prefixes a publisher copy-paste might leave in place.
_URL_PREFIXES: tuple[str, ...] = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
)

#: ``doi:`` (or ``DOI:``) scheme prefix some metadata exports use.
_DOI_SCHEME_PREFIX_RE = re.compile(r"^doi:\s*", re.IGNORECASE)


def normalize_doi(raw: str | None) -> str | None:
    """Return a canonical DOI string, or ``None`` if *raw* is unusable.

    Steps (each is lossless for valid DOIs):

    1. Strip URL scheme prefixes (``https://doi.org/``, ``dx.doi.org``).
    2. Strip the ``doi:`` / ``DOI:`` scheme prefix.
    3. Collapse whitespace, lowercase.
    4. Validate the result matches the bare DOI grammar; return ``None``
       otherwise so the caller can fall back to content_hash matching.

    >>> normalize_doi("  HTTPS://DOI.ORG/10.1234/FOO  ")
    '10.1234/foo'
    >>> normalize_doi("doi:10.1234/foo")
    '10.1234/foo'
    >>> normalize_doi("")
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None

    lowered = cleaned.lower()
    for prefix in _URL_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            lowered = cleaned.lower()
            break

    # Strip ``doi:`` scheme (case-insensitive; tolerates one space).
    cleaned = _DOI_SCHEME_PREFIX_RE.sub("", cleaned)
    lowered = cleaned.lower()

    # Collapse all whitespace runs (some metadata exports embed a space
    # inside the DOI like ``10.1234/ abc``).
    cleaned = re.sub(r"\s+", "", lowered)
    if not cleaned:
        return None

    if not _DOI_BODY_RE.match(cleaned):
        # Not a recognisable DOI — let the caller fall back to
        # content_hash matching. Returning None (not the raw string)
        # is critical: storing garbage in ``literature_doi`` would
        # silently make the dedup key unusable.
        return None

    return cleaned


def compute_dedupe_key(
    dataset_id: UUID,
    property_type_id: UUID,
    source_id: UUID,
    value_hash: str,
) -> str:
    """Return a deterministic dedupe key for AC-9's unique constraint.

    The key is a SHA-256 hex digest of the four-tuple
    ``(dataset_id, property_type_id, source_id, value_hash)``, prefixed
    with the algorithm name so future migrations to a different hash
    can be expressed without collision risk.

    The composite is the same as ADR-017 §2.6 / spec §3.1:
    ``(dataset, property, source, value_hash)``. It deliberately does
    NOT include ``conditions`` or ``method`` so two measurements taken
    on different temperatures / pressures stay distinct rows (only
    unconditional duplicates collapse — see spec §5 "auto-merge" rule).
    """
    payload = f"{dataset_id}|{property_type_id}|{source_id}|{value_hash}"
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def resolve_literature_dataset(
    db: AsyncSession,
    *,
    doi: str | None,
    content_hash: str | None,
) -> Dataset | None:
    """Return the existing :class:`Dataset` for a freshly-ingested literature.

    Resolution order (ADR-017 §2.5):

    1. Normalize *doi* and look up by ``datasets.literature_doi``.
    2. If that misses and *content_hash* is set, look up by
       ``datasets.literature_content_hash``.
    3. Otherwise return ``None`` so the caller can create a new dataset
       (and, once G1-B ships, a new ``dataset_version``).

    DOI is always tried first: two PDFs with the same content but
    different DOIs (e.g. preprint vs. publisher version) would have
    the same content_hash but represent distinct works — the DOI is
    the authoritative identity signal.
    """
    norm_doi = normalize_doi(doi)
    if norm_doi:
        stmt = select(Dataset).where(Dataset.literature_doi == norm_doi).limit(1)
        hit = (await db.execute(stmt)).scalar_one_or_none()
        if hit is not None:
            logger.info(
                "literature_dedup: DOI hit dataset_id=%s doi=%s",
                hit.id,
                norm_doi,
            )
            return hit

    if content_hash:
        stmt = (
            select(Dataset)
            .where(Dataset.literature_content_hash == content_hash)
            .limit(1)
        )
        hit = (await db.execute(stmt)).scalar_one_or_none()
        if hit is not None:
            logger.info(
                "literature_dedup: content_hash hit dataset_id=%s hash=%s",
                hit.id,
                content_hash,
            )
            return hit

    logger.info(
        "literature_dedup: miss doi=%s content_hash=%s",
        norm_doi,
        content_hash,
    )
    return None


__all__: tuple[str, ...] = (
    "compute_dedupe_key",
    "normalize_doi",
    "resolve_literature_dataset",
)
