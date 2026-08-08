"""Gap dispatch service: route DataCollectionRequests to fill paths (NFM-2650).

Central dispatch service that routes DCRs to the correct fill path based on
``source_preference``.  Supports single-path routing and cascade mode.

Routing rules (from ADR-NFM-2577):
    literature  → LiteratureFillPath
    dft         → DFTFillPath
    external_db → ExternalDBFillPath
    any         → Cascade: external_db → literature → dft (stop at first success)

Usage:
    svc = GapDispatchService(session)
    result = await svc.dispatch(dcr)
    results = await svc.dispatch_batch(ontology_version_id=ov_id)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest

logger = logging.getLogger(__name__)

# Cascade order: external_db (fastest) → literature → dft (slowest).
CASCADE_ORDER: list[str] = ["external_db", "literature", "dft"]

# All valid fill path names.
DISPATCH_PATHS: frozenset[str] = frozenset(CASCADE_ORDER)

# Default batch limit.
DEFAULT_BATCH_LIMIT: int = 10


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchResult:
    """Immutable result of dispatching a single DCR."""

    success: bool
    path: str
    reference: str | None
    error: str | None
    data_found: bool


# ---------------------------------------------------------------------------
# FillPath protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FillPath(Protocol):
    """Protocol for fill-path handlers.

    Each fill path (literature, DFT, external_db) must implement this.
    The ``name`` attribute identifies the path for routing and state tracking.
    """

    name: str

    async def execute(
        self,
        dcr: DataCollectionRequest,
    ) -> DispatchResult:
        """Execute the fill path for the given DCR.

        Args:
            dcr: The DataCollectionRequest to process.

        Returns:
            DispatchResult indicating success/failure and whether data was found.
        """
        ...


# ---------------------------------------------------------------------------
# GapDispatchService
# ---------------------------------------------------------------------------


class GapDispatchService:
    """Routes DataCollectionRequests to the correct fill path.

    Supports single-path routing by ``source_preference`` and cascade mode
    (``source_preference='any'``) that tries paths in priority order.

    Usage:
        svc = GapDispatchService(session)
        result = await svc.dispatch(dcr)
        batch = await svc.dispatch_batch(ontology_version_id=ov_id, limit=10)
    """

    def __init__(
        self,
        session: AsyncSession,
        fill_paths: dict[str, FillPath] | None = None,
    ) -> None:
        self._session = session
        self._paths = fill_paths or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        dcr: DataCollectionRequest,
    ) -> DispatchResult:
        """Route a single DCR by its ``source_preference``.

        Args:
            dcr: The DataCollectionRequest to dispatch.

        Returns:
            DispatchResult with success/failure and path info.
        """
        # Idempotency: skip already-dispatched requests.
        if dcr.dispatched_at is not None and dcr.dispatch_status is not None:
            logger.info(
                "Skipping already-dispatched DCR %s (dispatch_status=%s)",
                dcr.id,
                dcr.dispatch_status,
            )
            return DispatchResult(
                success=False,
                path=dcr.dispatched_path or "unknown",
                reference=dcr.result_reference,
                error=(
                    f"DCR {dcr.id}: already dispatched "
                    f"(status={dcr.dispatch_status})"
                ),
                data_found=False,
            )

        # Mark dispatch as running.
        now = datetime.now(UTC)
        dcr.dispatched_at = now
        dcr.dispatch_status = "running"
        dcr.status = "in_progress"

        if dcr.source_preference == "any":
            result = await self._dispatch_cascade(dcr)
        else:
            result = await self._dispatch_single(dcr)

        # Update DCR state based on result.
        dcr.dispatched_path = result.path
        dcr.dispatch_status = "success" if result.success else "failed"
        if result.reference:
            dcr.result_reference = result.reference

        await self._session.flush()

        log_fn = logger.info if result.success else logger.warning
        log_fn(
            "Dispatched DCR %s → path=%s success=%s data_found=%s",
            dcr.id,
            result.path,
            result.success,
            result.data_found,
        )

        return result

    async def dispatch_batch(
        self,
        ontology_version_id: uuid.UUID,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> list[DispatchResult]:
        """Batch dispatch open DCRs for an ontology version.

        Selects up to ``limit`` open, undispatched DCRs ordered by urgency
        (descending) and dispatches each one.

        Args:
            ontology_version_id: The ontology version to process.
            limit: Maximum number of DCRs to dispatch (default 10).

        Returns:
            List of DispatchResult for each dispatched DCR.
        """
        stmt = (
            select(DataCollectionRequest)
            .where(
                DataCollectionRequest.ontology_version_id == ontology_version_id,
                DataCollectionRequest.status == "open",
                DataCollectionRequest.dispatched_at.is_(None),
            )
            .order_by(DataCollectionRequest.urgency.desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        dcrs = list(result.scalars().all())

        if not dcrs:
            logger.debug(
                "No open undispatched DCRs for ontology_version=%s",
                ontology_version_id,
            )
            return []

        logger.info(
            "Batch dispatching %d DCRs for ontology_version=%s (limit=%d)",
            len(dcrs),
            ontology_version_id,
            limit,
        )

        results: list[DispatchResult] = []
        for dcr in dcrs:
            try:
                r = await self.dispatch(dcr)
                results.append(r)
            except Exception:
                logger.exception(
                    "Error dispatching DCR %s in batch",
                    dcr.id,
                )
                results.append(
                    DispatchResult(
                        success=False,
                        path="error",
                        reference=None,
                        error=f"Unexpected error dispatching DCR {dcr.id}",
                        data_found=False,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _dispatch_single(
        self,
        dcr: DataCollectionRequest,
    ) -> DispatchResult:
        """Dispatch a DCR to a single fill path."""
        pref = dcr.source_preference
        path = self._paths.get(pref)

        if path is None:
            return DispatchResult(
                success=False,
                path=pref,
                reference=None,
                error=f"No fill path registered for source_preference='{pref}'",
                data_found=False,
            )

        return await path.execute(dcr)

    async def _dispatch_cascade(
        self,
        dcr: DataCollectionRequest,
    ) -> DispatchResult:
        """Cascade: try external_db → literature → dft, stop at first success."""
        last_result: DispatchResult | None = None

        for path_name in CASCADE_ORDER:
            path = self._paths.get(path_name)
            if path is None:
                logger.debug(
                    "Cascade: fill path '%s' not registered, skipping",
                    path_name,
                )
                continue

            try:
                result = await path.execute(dcr)
                last_result = result

                if result.data_found:
                    return result

                logger.debug(
                    "Cascade: path '%s' found no data for DCR %s, trying next",
                    path_name,
                    dcr.id,
                )
            except Exception:
                logger.exception(
                    "Cascade: path '%s' raised exception for DCR %s",
                    path_name,
                    dcr.id,
                )
                last_result = DispatchResult(
                    success=False,
                    path=path_name,
                    reference=None,
                    error=f"Exception in path '{path_name}'",
                    data_found=False,
                )

        # All paths exhausted without finding data.
        return DispatchResult(
            success=last_result.success if last_result else False,
            path="cascade",
            reference=last_result.reference if last_result else None,
            error=(
                last_result.error if last_result else "All cascade paths exhausted"
            ),
            data_found=False,
        )


__all__ = [
    "CASCADE_ORDER",
    "DEFAULT_BATCH_LIMIT",
    "DISPATCH_PATHS",
    "DispatchResult",
    "FillPath",
    "GapDispatchService",
]
