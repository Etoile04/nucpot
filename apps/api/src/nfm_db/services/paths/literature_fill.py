"""Literature fill path handler (NFM-2649).

Creates a :class:`DataSource` placeholder row representing a pending
literature search.  The actual search/extraction is performed by the
Celery ``process_gap_literature_task`` task scheduled by the dispatch
service; this handler simply records the request so the task has a
target DataSource to populate.

The ``can_handle`` method accepts ``"literature"`` and ``"any"`` (the
generic preference that lets the router pick any path).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.source import DataSource
from nfm_db.services.paths.base import DispatchResult, GapFillPath

logger = logging.getLogger(__name__)


__all__ = ["LiteratureFillPath"]


#: Preferences accepted by this handler.
HANDLED_PREFERENCES: frozenset[str] = frozenset({"literature", "any"})


def _build_search_keywords(
    entity_type: str,
    property_name: str,
    material_system: str,
) -> list[str]:
    """Build a sensible set of search keywords for the gap.

    The keywords combine the ontology triple with a common-suffix
    heuristic so the downstream literature search can pick something
    useful even when the request is sparse.
    """
    keywords: list[str] = [
        material_system,
        f"{material_system} {property_name}",
        f"{entity_type} {property_name}",
        f"{property_name} {material_system}",
    ]
    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            deduped.append(kw)
    return deduped


class LiteratureFillPath(GapFillPath):
    """Literature path: create a DataSource placeholder with search keywords.

    MVP simplification: only a placeholder row is created; the actual
    literature search is performed asynchronously by the Celery task
    scheduled by :class:`GapDispatchService`.  The placeholder carries
    the search keywords so the worker can pick up where the request
    left off.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # GapFillPath protocol
    # ------------------------------------------------------------------

    def can_handle(self, source_preference: str) -> bool:
        """Return ``True`` for ``"literature"`` or ``"any"``."""
        return source_preference in HANDLED_PREFERENCES

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Create a DataSource placeholder for the literature search.

        The new DataSource is persisted with ``source_type="placeholder"``
        and the search keywords in its ``metadata_`` JSON bag.  The
        DataSource UUID is returned as the dispatch reference so
        downstream code can locate the placeholder.
        """
        keywords = _build_search_keywords(
            entity_type=request.entity_type,
            property_name=request.property,
            material_system=request.material_system,
        )

        placeholder = DataSource(
            title=(
                f"Literature search for {request.entity_type}."
                f"{request.property} ({request.material_system})"
            ),
            source_type="placeholder",
            external_url=None,
            abstract=None,
            metadata_={
                "kind": "literature_search_placeholder",
                "data_collection_request_id": str(request.id),
                "entity_type": request.entity_type,
                "property": request.property,
                "material_system": request.material_system,
                "search_keywords": keywords,
            },
        )

        self._session.add(placeholder)
        await self._session.flush()

        metadata: dict[str, Any] = {
            "entity_type": request.entity_type,
            "property": request.property,
            "material_system": request.material_system,
            "search_keywords": keywords,
            "data_source_id": str(placeholder.id),
        }

        logger.info(
            "LiteratureFillPath created placeholder DataSource %s for "
            "request %s (keywords=%d)",
            placeholder.id,
            request.id,
            len(keywords),
        )

        return DispatchResult(
            success=True,
            path="literature",
            reference=str(placeholder.id),
            data_found=False,
            metadata=metadata,
        )
