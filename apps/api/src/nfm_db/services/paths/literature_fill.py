"""Literature fill path handler (NFM-2645).

Creates a :class:`~nfm_db.models.source.DataSource` placeholder record whose
title encodes the search keywords derived from the missing property and the
material system.  MVP simplification: no actual literature search is performed;
the placeholder simply records *what* needs to be searched for.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.source import DataSource
from nfm_db.services.paths.base import DispatchResult

logger = logging.getLogger(__name__)

_HANDLER_NAME = "literature"

# source_preference values this handler accepts.
_ACCEPTED_PREFERENCES = frozenset({"literature", "any"})


class LiteratureFillPath:
    """Fill path that creates a DataSource placeholder for literature search.

    ``can_handle()`` returns ``True`` when the request's
    ``source_preference`` is ``"literature"`` or ``"any"``.

    ``execute()`` creates a :class:`DataSource` row whose title contains
    the search keywords (property name + material system).  The placeholder
    is *not* sent for parsing — it merely records the intent.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def can_handle(self, request: DataCollectionRequest) -> bool:
        """Return ``True`` when *request* targets literature sources."""
        return request.source_preference in _ACCEPTED_PREFERENCES

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Create a DataSource placeholder for literature search.

        Returns:
            A :class:`DispatchResult` with the new DataSource UUID as
            *reference* and ``data_found=False`` (placeholder only).
        """
        try:
            keywords = self._build_search_keywords(request)
            source = DataSource(
                title=keywords,
                source_type="literature_placeholder",
                parse_status="placeholder",
            )
            self._session.add(source)
            await self._session.flush()

            reference = str(source.id)
            logger.info(
                "Created literature placeholder DataSource %s "
                "for property=%r material=%r",
                reference,
                request.property,
                request.material_system,
            )

            return DispatchResult(
                success=True,
                path=_HANDLER_NAME,
                reference=reference,
                data_found=False,
            )
        except Exception as exc:
            logger.exception(
                "Failed to create literature placeholder for request %s",
                request.id,
            )
            return DispatchResult(
                success=False,
                path=_HANDLER_NAME,
                error=f"Literature placeholder creation failed: {exc}",
            )

    @staticmethod
    def _build_search_keywords(request: DataCollectionRequest) -> str:
        """Compose search-keyword title from the request's gap context."""
        return f"[PLACEHOLDER] {request.property} - {request.material_system}"
