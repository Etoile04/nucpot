"""Ontology loader — version-resolved type access (NFM-3590 / NFM-2868-P1-2-b).

Loads :class:`~nfm_db.models.KEntityType` and
:class:`~nfm_db.models.KRelationType` rows for a given
:class:`~nfm_db.models.OntologyVersion`, refusing to proceed when the
referenced version row is missing or NULL.

Why this exists:
    Without runtime validation, a missing or stale OntologyVersion row would
    silently load types that belong to a different ontology generation and
    pollute the knowledge graph. Every load path must go through
    ``_resolve_version`` first.

Public surface:
    - :func:`load_ontology_types` — load entity + relation rows for a version
    - :func:`_resolve_version` — private helper that fetches and validates
      a single OntologyVersion row
    - :class:`OntologyVersionNotFoundError` — raised on missing/NULL id
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import KEntityType, KRelationType, OntologyVersion

__all__ = [
    "OntologyVersionNotFoundError",
    "load_ontology_types",
]

logger = logging.getLogger(__name__)


class OntologyVersionNotFoundError(LookupError):
    """Raised when a requested ``ontology_version_id`` cannot be resolved.

    Covers both the "row does not exist" case and the "NULL pointer"
    case on a non-baseline load.
    """


async def _resolve_version(
    session: AsyncSession,
    ontology_version_id: uuid.UUID | None,
) -> OntologyVersion:
    """Return the :class:`OntologyVersion` row for ``ontology_version_id``.

    Args:
        session: Async DB session.
        ontology_version_id: The version id to resolve. ``None`` is
            treated as an unresolved reference and rejected on non-baseline
            loads.

    Returns:
        The persisted :class:`OntologyVersion` row.

    Raises:
        OntologyVersionNotFoundError: If ``ontology_version_id`` is ``None``
            or no row matches it. An ERROR log line is emitted before the
            raise.
    """
    if ontology_version_id is None:
        logger.error(
            "ontology_loader: refusing load with NULL ontology_version_id "
            "(non-baseline loads must reference a persisted OntologyVersion)",
        )
        raise OntologyVersionNotFoundError(
            "ontology_version_id is NULL; non-baseline loads require a "
            "persisted OntologyVersion reference",
        )

    stmt = select(OntologyVersion).where(OntologyVersion.id == ontology_version_id)
    result = await session.execute(stmt)
    ov = result.scalar_one_or_none()

    if ov is None:
        logger.error(
            "ontology_loader: refusing load for unknown ontology_version_id=%s "
            "(no OntologyVersion row matches)",
            ontology_version_id,
        )
        raise OntologyVersionNotFoundError(
            f"OntologyVersion not found for ontology_version_id={ontology_version_id}",
        )

    return ov


async def load_ontology_types(
    session: AsyncSession,
    ontology_version_id: uuid.UUID | None,
) -> tuple[list[KEntityType], list[KRelationType]]:
    """Load the entity + relation type rows bound to ``ontology_version_id``.

    Validates the version row exists (or raises) via
    :func:`_resolve_version` before issuing the type queries, so the caller
    never receives rows from a stale or missing generation.

    Args:
        session: Async DB session.
        ontology_version_id: Version id whose types should be loaded.

    Returns:
        ``(entity_types, relation_types)`` — lists of rows carrying
        ``ontology_version_id == ontology_version_id``.

    Raises:
        OntologyVersionNotFoundError: Propagated from :func:`_resolve_version`.
    """
    await _resolve_version(session, ontology_version_id)
    assert ontology_version_id is not None  # narrowed by _resolve_version

    entity_stmt = select(KEntityType).where(
        KEntityType.ontology_version_id == ontology_version_id,
    )
    relation_stmt = select(KRelationType).where(
        KRelationType.ontology_version_id == ontology_version_id,
    )

    entity_result = await session.execute(entity_stmt)
    relation_result = await session.execute(relation_stmt)

    return list(entity_result.scalars().all()), list(relation_result.scalars().all())
