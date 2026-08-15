"""DFT fill path handler (NFM-2649).

Creates a :class:`DFTCalculation` row marked as ``"pending"`` and
annotates the ``computation_metadata`` bag with a ``placeholder=true``
flag so downstream consumers can distinguish a stub from a real
calculation.

The handler uses the existing ``DFTCalculation`` model — no new
schema is required; ``computation_metadata`` carries the
placeholder marker.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.dft_calculation import DFTCalculation
from nfm_db.services.paths.base import DispatchResult, GapFillPath

logger = logging.getLogger(__name__)


__all__ = ["DFTFillPath"]


#: Preferences accepted by this handler.
HANDLED_PREFERENCES: frozenset[str] = frozenset({"dft", "any"})

#: Default functional for stub DFT calculations.  Editable later by the
#: domain expert — same as GapDispatchService's default.
DEFAULT_FUNCTIONAL: str = "PBE"

#: Default plane-wave cutoff in eV.  Typical for actinide systems.
DEFAULT_CUTOFF_EV: float = 520.0


class DFTFillPath(GapFillPath):
    """DFT path: create a DFTCalculation stub marked as placeholder."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # GapFillPath protocol
    # ------------------------------------------------------------------

    def can_handle(self, source_preference: str) -> bool:
        """Return ``True`` for ``"dft"`` or ``"any"``."""
        return source_preference in HANDLED_PREFERENCES

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Create a DFTCalculation stub bound to the request.

        The new row uses a ``calculation_id`` of the form
        ``"gap-<uuid>"`` so it can be traced back to the originating
        :class:`DataCollectionRequest`.  ``computation_metadata`` is
        populated with the request triple and a ``placeholder=true``
        marker so downstream consumers can tell the stub from a real
        computation.
        """
        calc_id = f"gap-{request.id}"
        computation_metadata: dict[str, Any] = {
            "data_collection_request_id": str(request.id),
            "entity_type": request.entity_type,
            "property": request.property,
            "material_system": request.material_system,
            "urgency": request.urgency,
            "placeholder": True,
        }

        calc = DFTCalculation(
            calculation_id=calc_id,
            functional=DEFAULT_FUNCTIONAL,
            cutoff_energy=DEFAULT_CUTOFF_EV,
            status="pending",
            source="gap_dispatch",
            computation_metadata=computation_metadata,
            notes=(
                f"Auto-created (placeholder) by DFTFillPath for "
                f"{request.entity_type}.{request.property} "
                f"({request.material_system})"
            ),
        )

        self._session.add(calc)
        await self._session.flush()

        logger.info(
            "DFTFillPath created placeholder DFTCalculation %s for "
            "request %s",
            calc.id,
            request.id,
        )

        return DispatchResult(
            success=True,
            path="dft",
            reference=str(calc.id),
            data_found=False,
            metadata={
                "dft_calculation_id": str(calc.id),
                "calculation_id": calc_id,
                "placeholder": True,
                "status": "pending",
                "functional": DEFAULT_FUNCTIONAL,
                "cutoff_energy": DEFAULT_CUTOFF_EV,
            },
        )
