"""Gap dispatch service: route DataCollectionRequests to fill paths (NFM-2650).

Thin router over the canonical :class:`GapFillPath` protocol defined in
:mod:`nfm_db.services.paths.base`.  This module must NOT redefine
:class:`DispatchResult` or ``DISPATCH_PATHS`` -- those live in the canonical
``paths/base`` module so all handlers, the router, and downstream consumers
agree on the contract.

Routing rules (from ADR-NFM-2577):

    literature  → LiteratureFillPath
    dft         → DFTFillPath
    external_db → ExternalDBFillPath
    any         → Cascade: external_db → literature → dft (skip handlers
                  whose :meth:`GapFillPath.can_handle` returns ``False``;
                  stop at first :attr:`DispatchResult.data_found` ``True``)

Usage::

    svc = GapDispatchService(session, fill_paths=paths)
    result = await svc.dispatch(dcr)
    results = await svc.dispatch_batch(ontology_version_id=ov_id)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.services.paths.base import (
    DISPATCH_PATHS,
    DISPATCH_STATUSES,
    DispatchResult,
    GapFillPath,
)

logger = logging.getLogger(__name__)

# Cascade priority order for source_preference='any'.
CASCADE_ORDER: tuple[str, ...] = ("external_db", "literature", "dft")

# Default batch limit for dispatch_batch().
DEFAULT_BATCH_LIMIT: int = 10

# Literal stored in ``dispatched_path`` when cascade mode was used.
_CASCADE_PATH_LABEL: str = "cascade"


class GapDispatchService:
    """Routes DataCollectionRequests to the correct fill path.

    Supports single-path routing by ``source_preference`` and cascade mode
    (``source_preference='any'``) that walks handlers in priority order,
    skipping any whose :meth:`GapFillPath.can_handle` returns ``False``.

    Usage::

        svc = GapDispatchService(session, fill_paths=paths)
        result = await svc.dispatch(dcr)
        batch = await svc.dispatch_batch(ontology_version_id=ov_id, limit=10)
    """

    def __init__(
        self,
        session: AsyncSession,
        fill_paths: dict[str, GapFillPath] | None = None,
    ) -> None:
        self._session = session
        self._paths: dict[str, GapFillPath] = fill_paths or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        dcr: DataCollectionRequest,
    ) -> DispatchResult:
        """Route a single DCR by its ``source_preference``.

        Idempotency: if ``dispatched_at`` is already set on *dcr*, the
        request is skipped without invoking any fill path.

        Args:
            dcr: The DataCollectionRequest to dispatch.

        Returns:
            DispatchResult with success/failure and path info.
        """
        # Idempotency: skip already-dispatched requests.
        if dcr.dispatched_at is not None:
            logger.info(
                "Skipping already-dispatched DCR %s (dispatched_path=%s)",
                dcr.id,
                dcr.dispatched_path,
            )
            return DispatchResult(
                success=False,
                path=dcr.dispatched_path or "unknown",
                reference=dcr.result_reference,
                error=f"DCR {dcr.id}: already dispatched",
                data_found=False,
            )

        # Mark dispatch as running.
        dcr.dispatched_at = datetime.now(UTC)
        dcr.dispatch_status = "running"
        dcr.status = "in_progress"

        if dcr.source_preference == "any":
            result = await self._dispatch_cascade(dcr)
        else:
            result = await self._dispatch_single(dcr)

        # Persist DCR state based on result.
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
        """Batch dispatch open, undispatched DCRs for an ontology version.

        Selects up to ``limit`` open DCRs whose ``dispatched_at`` is ``NULL``
        (i.e. they have never been routed to a fill path), ordered by
        urgency descending, and dispatches each one.

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
                results.append(await self.dispatch(dcr))
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
        """Dispatch a DCR to a single fill path by ``source_preference``.

        The service trusts the handler's own ``can_handle``/``execute`` to
        reject inappropriate requests -- per the architectural contract
        (ADR-NFM-2577) the handler, not the router, owns that decision.
        """
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
        """Cascade: try external_db → literature → dft in priority order.

        - Skip a handler whose :meth:`can_handle` returns ``False`` (do not
          call ``execute()`` on it).
        - Stop at the first handler whose ``execute()`` returns
          ``data_found=True``.
        - When no handler finds data, return ``path='cascade'`` so the DCR
          row records that cascade mode was attempted.
        """
        last_result: DispatchResult | None = None
        any_executed = False

        for path_name in CASCADE_ORDER:
            path = self._paths.get(path_name)
            if path is None:
                logger.debug(
                    "Cascade: fill path '%s' not registered, skipping",
                    path_name,
                )
                continue

            if not await path.can_handle(dcr):
                logger.debug(
                    "Cascade: path '%s' cannot handle DCR %s, skipping",
                    path_name,
                    dcr.id,
                )
                continue

            try:
                result = await path.execute(dcr)
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
                continue

            any_executed = True
            last_result = result

            if result.data_found:
                return result

            logger.debug(
                "Cascade: path '%s' found no data for DCR %s, trying next",
                path_name,
                dcr.id,
            )

        if not any_executed and last_result is None:
            # No registered path accepted the request at all.
            return DispatchResult(
                success=False,
                path=_CASCADE_PATH_LABEL,
                reference=None,
                error="No cascade path accepted the request",
                data_found=False,
            )

        # Either every executed path returned no data, or some path
        # raised.  Record cascade mode on the DCR row.
        return DispatchResult(
            success=last_result.success if last_result else False,
            path=_CASCADE_PATH_LABEL,
            reference=last_result.reference if last_result else None,
            error=(
                last_result.error
                if last_result
                else "All cascade paths exhausted"
            ),
            data_found=False,
        )


__all__ = [
    "CASCADE_ORDER",
    "DEFAULT_BATCH_LIMIT",
    "DISPATCH_PATHS",
    "DISPATCH_STATUSES",
    "DispatchResult",
    "GapDispatchService",
    "GapFillPath",
]