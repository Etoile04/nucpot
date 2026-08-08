"""External-database fill path handler (NFM-2645).

Queries existing external data sources (NIST IPR, OpenKIM, Materials Project)
via :class:`~nfm_db.services.external_data_sources.ExternalDataSourceClient`
using the gap's material system and property name as search parameters.

MVP simplification: queries all three sources and returns the first match.
No cross-source merging or ranking is performed.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.external_data_sources import (
    ExternalDataSource,
    ExternalDataSourceClient,
)
from nfm_db.services.paths.base import DispatchResult

logger = logging.getLogger(__name__)

_HANDLER_NAME = "external_db"

# source_preference values this handler accepts.
_ACCEPTED_PREFERENCES = frozenset({"external_db", "any"})

# Sources to query, in cascade order.
_QUERY_ORDER: list[ExternalDataSource] = [
    ExternalDataSource.NIST_IPR,
    ExternalDataSource.OPENKIM,
    ExternalDataSource.MATERIALS_PROJECT,
]


class ExternalDBFillPath:
    """Fill path that queries external databases for existing data.

    ``can_handle()`` returns ``True`` when the request's
    ``source_preference`` is ``"external_db"`` or ``"any"``.

    ``execute()`` iterates through known external sources and returns
    the first source that has data for the requested property + material
    system combination.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def can_handle(self, request: DataCollectionRequest) -> bool:
        """Return ``True`` when *request* targets external databases."""
        return request.source_preference in _ACCEPTED_PREFERENCES

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Query external data sources for the gap's property and material.

        Returns:
            A :class:`DispatchResult` with *reference* set to
            ``"{source_name}:{property_name}"`` and ``data_found=True``
            when a match is found.
        """
        try:
            result = await self._query_sources(request)
            if result is not None:
                source_name, data = result
                reference = f"{source_name}:{request.property}"
                logger.info(
                    "Found data in %s for property=%r material=%r",
                    source_name,
                    request.property,
                    request.material_system,
                )
                return DispatchResult(
                    success=True,
                    path=_HANDLER_NAME,
                    reference=reference,
                    data_found=True,
                )

            logger.info(
                "No external data found for property=%r material=%r",
                request.property,
                request.material_system,
            )
            return DispatchResult(
                success=True,
                path=_HANDLER_NAME,
                reference=None,
                data_found=False,
            )
        except Exception as exc:
            logger.exception(
                "External DB query failed for request %s",
                request.id,
            )
            return DispatchResult(
                success=False,
                path=_HANDLER_NAME,
                error=f"External DB query failed: {exc}",
            )

    async def _query_sources(
        self,
        request: DataCollectionRequest,
    ) -> tuple[str, dict[str, Any]] | None:
        """Iterate through sources; return first hit or ``None``.

        This method is separated from ``execute()`` for testability: tests
        can mock this method directly instead of the full HTTP stack.
        """
        client = ExternalDataSourceClient()
        try:
            for source in _QUERY_ORDER:
                data = await self._query_single_source(
                    client, source, request.material_system, request.property
                )
                if data is not None:
                    return (source.value, data)
            return None
        finally:
            await client.close()

    @staticmethod
    async def _query_single_source(
        client: ExternalDataSourceClient,
        source: ExternalDataSource,
        formula: str,
        property_name: str,
    ) -> dict[str, Any] | None:
        """Query a single external source by source type."""
        match source:
            case ExternalDataSource.NIST_IPR:
                return await client.query_nist_ipr(formula, property_name)
            case ExternalDataSource.OPENKIM:
                return await client.query_openkim(formula, property_name)
            case ExternalDataSource.MATERIALS_PROJECT:
                return await client.query_materials_project(
                    formula, property_name
                )
            case _:
                return None
