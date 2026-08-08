"""DFT fill path handler (NFM-2645).

Creates a :class:`~nfm_db.models.dft_calculation.DFTCalculation` stub record
marked as a placeholder.  The stub records the gap context (property + material
system) in ``computation_metadata`` so that a future DFT workflow can pick
it up, resolve the computational parameters, and submit a real job.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.dft_calculation import DFTCalculation
from nfm_db.services.paths.base import DispatchResult

logger = logging.getLogger(__name__)

_HANDLER_NAME = "dft"

# source_preference values this handler accepts.
_ACCEPTED_PREFERENCES = frozenset({"dft", "any"})

# Sentinel value stored in computation_metadata to identify placeholder rows.
_PLACEHOLDER_MARKER = "stub/placeholder"


class DFTFillPath:
    """Fill path that creates a DFTCalculation stub for DFT computation.

    ``can_handle()`` returns ``True`` when the request's
    ``source_preference`` is ``"dft"`` or ``"any"``.

    ``execute()`` creates a :class:`DFTCalculation` row with minimal
    required fields and ``status="pending"``.  The ``computation_metadata``
    dict contains a ``"placeholder": "stub/placeholder"`` marker so downstream
    consumers can distinguish stubs from real calculations.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def can_handle(self, request: DataCollectionRequest) -> bool:
        """Return ``True`` when *request* targets DFT computation."""
        return request.source_preference in _ACCEPTED_PREFERENCES

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Create a DFTCalculation stub for the gap request.

        Returns:
            A :class:`DispatchResult` with the new DFTCalculation UUID as
            *reference* and ``data_found=False`` (stub only).
        """
        try:
            stub = DFTCalculation(
                calculation_id=f"stub-{request.id}",
                functional="PBE",
                cutoff_energy=520.0,
                status="pending",
                computation_metadata={
                    "placeholder": _PLACEHOLDER_MARKER,
                    "property": request.property,
                    "material_system": request.material_system,
                    "entity_type": request.entity_type,
                    "collection_request_id": str(request.id),
                },
            )
            self._session.add(stub)
            await self._session.flush()

            reference = str(stub.id)
            logger.info(
                "Created DFT stub %s for property=%r material=%r",
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
                "Failed to create DFT stub for request %s",
                request.id,
            )
            return DispatchResult(
                success=False,
                path=_HANDLER_NAME,
                error=f"DFT stub creation failed: {exc}",
            )
