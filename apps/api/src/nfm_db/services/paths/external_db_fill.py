"""External database fill path handler (NFM-2649).

Queries the existing :class:`ExternalDataSourceClient` for the
material-system + property triple carried by the request.  The
handler returns a :class:`DispatchResult` whose ``data_found`` flag
reflects whether any of the three backends (NIST IPR, OpenKIM,
Materials Project) returned data.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.paths.base import DispatchResult, GapFillPath

logger = logging.getLogger(__name__)


__all__ = ["ExternalDBFillPath"]


#: Preferences accepted by this handler.
HANDLED_PREFERENCES: frozenset[str] = frozenset({"external_db", "any"})


def _reference_for(source: str, query_id: str) -> str:
    """Build the ``<source>:<query_id>`` reference string.

    The reference is opaque to callers but uniquely identifies the
    origin of the query for audit purposes.
    """
    return f"{source}:{query_id}"


def _is_meaningful_result(value: Any) -> bool:
    """Return ``True`` when an external query returned a usable payload.

    The placeholders used by :class:`ExternalDataSourceClient` always
    populate the result dict (with ``values: []`` etc.) so a ``None``
    check is the only reliable signal that the upstream query failed.
    """
    return value is not None


class ExternalDBFillPath(GapFillPath):
    """External-DB path: query ExternalDataSourceClient.

    The handler lazily imports :class:`ExternalDataSourceClient` so
    unit tests can run with the client class patched (and don't need
    the httpx stack at import time).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # GapFillPath protocol
    # ------------------------------------------------------------------

    def can_handle(self, source_preference: str) -> bool:
        """Return ``True`` for ``"external_db"`` or ``"any"``."""
        return source_preference in HANDLED_PREFERENCES

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Query the external DBs for the request triple.

        Calls all three backends (NIST IPR, OpenKIM, Materials Project)
        and aggregates the results.  ``data_found`` is ``True`` when
        at least one backend returned a payload.
        """
        # Lazy import keeps the unit-test path free of httpx.
        from nfm_db.services.external_data_sources import (
            ExternalDataSourceClient,
        )

        formula = request.material_system
        property_name = request.property

        client = ExternalDataSourceClient()
        try:
            results: dict[str, Any] = {}

            nist_result = await client.query_nist_ipr(
                formula=formula,
                property_name=property_name,
            )
            if _is_meaningful_result(nist_result):
                results["nist_ipr"] = nist_result

            openkim_result = await client.query_openkim(
                species=formula,
                property_name=property_name,
            )
            if _is_meaningful_result(openkim_result):
                results["openkim"] = openkim_result

            mp_result = await client.query_materials_project(
                formula=formula,
                property_name=property_name,
            )
            if _is_meaningful_result(mp_result):
                results["materials_project"] = mp_result
        finally:
            await client.close()

        data_found = len(results) > 0
        primary_source = next(iter(results), None)
        reference = (
            _reference_for(primary_source, str(request.id))
            if primary_source is not None
            else f"none:{request.id}"
        )

        logger.info(
            "ExternalDBFillPath queried %d/3 sources for request %s "
            "(data_found=%s)",
            len(results),
            request.id,
            data_found,
        )

        return DispatchResult(
            success=True,
            path="external_db",
            reference=reference,
            data_found=data_found,
            metadata={
                "external_results": results,
                "source_count": len(results),
                "queried_sources": 3,
                "material_system": formula,
                "property": property_name,
            },
        )
