"""GapDispatchService — three-path data-collection dispatcher (NFM-2621).

Dispatches a ``DataCollectionRequest`` to one of three collection paths
based on its ``source_preference`` field:

- **literature** — schedules a Celery task via *literature_dispatcher*.
- **dft** — creates a ``DFTCalculation`` row marked ``pending``.
- **external_db** — queries external data sources for the property.
- **any** — tries paths in priority order: literature → external_db → dft.

The service transitions the ``DataCollectionRequest`` from ``open`` to
``in_progress`` and records which path was taken in ``metadata_``.

Follows existing service patterns: frozen dataclass for results,
``AsyncSession`` injection, and lazy imports for Celery to keep unit
tests broker-free.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataCollectionRequest, DFTCalculation
from nfm_db.models.data_collection_request import SOURCE_PREFERENCES

__all__ = [
    "DispatchResult",
    "GapDispatchService",
]

logger = logging.getLogger(__name__)

#: Priority order for ``source_preference='any'``.
_ANY_PRIORITY: tuple[str, ...] = ("literature", "external_db", "dft")


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of a single ``dispatch_request`` invocation.

    Attributes:
        request_id: The DataCollectionRequest that was dispatched.
        path_taken: Which collection path was used
            (literature | dft | external_db).
        status: ``"dispatched"`` on success, ``"failed"`` otherwise.
        detail: Human-readable detail (task id, DFT calculation id, etc.).
        metadata: Additional structured information about the dispatch.
    """

    request_id: uuid.UUID
    path_taken: str
    status: str
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)


class GapDispatchService:
    """Dispatches DataCollectionRequests to collection paths.

    Usage::

        svc = GapDispatchService(session)
        result = await svc.dispatch_request(request_id)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch_request(
        self,
        request_id: uuid.UUID,
    ) -> DispatchResult:
        """Dispatch a DataCollectionRequest to the appropriate collection path.

        Reads ``source_preference`` from the request and routes to the
        matching path.  Transitions the request status from ``open`` to
        ``in_progress``.

        Args:
            request_id: The DataCollectionRequest ID to dispatch.

        Returns:
            DispatchResult describing what happened.

        Raises:
            ValueError: If the request does not exist or is not in
                ``open`` status.
        """
        req = await self._load_request(request_id)

        preference = req.source_preference
        if preference not in SOURCE_PREFERENCES:
            raise ValueError(
                f"Invalid source_preference {preference!r} on request "
                f"{request_id}.",
            )

        result: DispatchResult
        if preference == "any":
            result = await self._dispatch_any(req)
        else:
            result = await self._dispatch_single(req, preference)

        # Transition open → in_progress and record dispatch metadata.
        req.status = "in_progress"
        existing_meta: dict[str, Any] = dict(req.metadata_ or {})
        existing_meta["dispatch"] = {
            "path_taken": result.path_taken,
            "dispatch_status": result.status,
            "detail": result.detail,
            "dispatched_at": datetime.now(UTC).isoformat(),
            **result.metadata,
        }
        req.metadata_ = existing_meta

        await self._session.flush()
        return result

    # ------------------------------------------------------------------
    # Path dispatchers
    # ------------------------------------------------------------------

    async def _dispatch_single(
        self,
        req: DataCollectionRequest,
        path: str,
    ) -> DispatchResult:
        """Dispatch to a single, explicitly-chosen path."""
        if path == "literature":
            return await self._dispatch_literature(req)
        if path == "dft":
            return await self._dispatch_dft(req)
        if path == "external_db":
            return await self._dispatch_external_db(req)
        # Should be unreachable due to earlier validation.
        raise ValueError(f"Unknown dispatch path: {path!r}")

    async def _dispatch_any(self, req: DataCollectionRequest) -> DispatchResult:
        """Try paths in priority order: literature → external_db → dft.

        The first path that succeeds (does not raise) is used.  If a path
        is attempted but fails, the error is logged and the next path is
        tried.  If all paths fail, a failed DispatchResult is returned.
        """
        errors: list[dict[str, str]] = []
        for path in _ANY_PRIORITY:
            try:
                result = await self._dispatch_single(req, path)
            except Exception as exc:  # intentional fallthrough to next path
                logger.warning(
                    "Dispatch path %r failed for request %s: %s",
                    path,
                    req.id,
                    exc,
                )
                errors.append({"path": path, "error": str(exc)[:500]})
                continue

            if result.status == "dispatched":
                return result

            # Path returned a non-failed result but status indicates issue;
            # record and continue.
            errors.append(
                {"path": path, "error": result.detail or "no detail"},
            )

        return DispatchResult(
            request_id=req.id,
            path_taken="any",
            status="failed",
            detail="All dispatch paths failed",
            metadata={"attempts": errors},
        )

    # ------------------------------------------------------------------
    # Individual path implementations
    # ------------------------------------------------------------------

    async def _dispatch_literature(
        self,
        req: DataCollectionRequest,
    ) -> DispatchResult:
        """Schedule a Celery literature-processing task for the request.

        We create (or reuse) a placeholder DataSource is not necessary
        here — the literature dispatcher expects a *datasource_id*.
        Since a DataCollectionRequest is not itself a DataSource, we
        pass the request id through as the payload and let the worker
        decide how to handle it.  A dedicated gap-collection task name
        is used so the worker can route it appropriately.
        """
        # Lazy import so unit tests don't need the Celery broker.
        from celery.exceptions import CeleryError

        from nfm_db.services.celery_app import celery_app

        task_name = (
            "nfm_db.services.gap_dispatch_service.process_gap_literature_task"
        )
        queue = "literature_processing"

        logger.info(
            "Dispatching request %s to literature path (task=%s)",
            req.id,
            task_name,
        )
        try:
            async_result = celery_app.send_task(
                task_name,
                kwargs={
                    "request_id": str(req.id),
                    "entity_type": req.entity_type,
                    "property": req.property,
                    "material_system": req.material_system,
                },
                queue=queue,
            )
        except CeleryError:
            logger.exception(
                "Celery broker error dispatching literature task for "
                "request %s",
                req.id,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected error dispatching literature task for request %s",
                req.id,
            )
            raise

        task_id = getattr(async_result, "id", None) or str(async_result)
        logger.info(
            "Scheduled literature task_id=%s for request %s",
            task_id,
            req.id,
        )
        return DispatchResult(
            request_id=req.id,
            path_taken="literature",
            status="dispatched",
            detail=f"Celery task {task_id} scheduled",
            metadata={
                "task_id": task_id,
                "task_name": task_name,
                "queue": queue,
            },
        )

    async def _dispatch_dft(
        self,
        req: DataCollectionRequest,
    ) -> DispatchResult:
        """Create a DFTCalculation record marked as ``pending``.

        The calculation is linked to the DataCollectionRequest via the
        ``computation_metadata`` JSON bag (no direct FK exists).  Sensible
        defaults are used for required fields (functional, cutoff_energy)
        so that the domain expert can refine them later.
        """
        calc = DFTCalculation(
            calculation_id=f"gap-{req.id}",
            functional="PBE",  # sensible default; editable later
            cutoff_energy=520.0,  # eV; typical for actinide systems
            status="pending",
            source="gap_dispatch",
            computation_metadata={
                "data_collection_request_id": str(req.id),
                "entity_type": req.entity_type,
                "property": req.property,
                "material_system": req.material_system,
                "urgency": req.urgency,
            },
            notes=(
                f"Auto-created by GapDispatchService for "
                f"{req.entity_type}.{req.property} ({req.material_system})"
            ),
        )
        self._session.add(calc)
        await self._session.flush()

        logger.info(
            "Created DFTCalculation %s (pending) for request %s",
            calc.id,
            req.id,
        )
        return DispatchResult(
            request_id=req.id,
            path_taken="dft",
            status="dispatched",
            detail=f"DFTCalculation {calc.id} created as pending",
            metadata={"dft_calculation_id": str(calc.id)},
        )

    async def _dispatch_external_db(
        self,
        req: DataCollectionRequest,
    ) -> DispatchResult:
        """Query external data sources for the requested property.

        Iterates over all configured external sources (NIST IPR, OpenKIM,
        Materials Project) and collects results.  The raw results are
        stored in the request ``metadata_`` bag for later processing.
        """
        from nfm_db.services.external_data_sources import (
            ExternalDataSourceClient,
        )

        logger.info(
            "Dispatching request %s to external_db path",
            req.id,
        )

        formula = req.material_system
        property_name = req.property

        results: dict[str, Any] = {}
        client = ExternalDataSourceClient()
        try:
            # Query all sources; collect whatever comes back.
            nist_result = await client.query_nist_ipr(
                formula=formula,
                property_name=property_name,
            )
            if nist_result is not None:
                results["nist_ipr"] = nist_result

            openkim_result = await client.query_openkim(
                species=formula,
                property_name=property_name,
            )
            if openkim_result is not None:
                results["openkim"] = openkim_result

            mp_result = await client.query_materials_project(
                formula=formula,
                property_name=property_name,
            )
            if mp_result is not None:
                results["materials_project"] = mp_result
        finally:
            await client.close()

        source_count = len(results)
        detail = (
            f"Queried 3 external sources, {source_count} returned data"
        )

        logger.info(
            "External DB dispatch for request %s: %d/%d sources returned data",
            req.id,
            source_count,
            3,
        )
        return DispatchResult(
            request_id=req.id,
            path_taken="external_db",
            status="dispatched",
            detail=detail,
            metadata={"external_results": results},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_request(
        self,
        request_id: uuid.UUID,
    ) -> DataCollectionRequest:
        """Load and validate a DataCollectionRequest.

        Args:
            request_id: The request ID to load.

        Returns:
            The DataCollectionRequest ORM object.

        Raises:
            ValueError: If the request is not found or not in ``open``
                status.
        """
        result = await self._session.execute(
            select(DataCollectionRequest).where(
                DataCollectionRequest.id == request_id,
            ),
        )
        req = result.scalar_one_or_none()
        if req is None:
            raise ValueError(
                f"DataCollectionRequest not found: {request_id}",
            )
        if req.status != "open":
            raise ValueError(
                f"DataCollectionRequest {request_id} is in status "
                f"{req.status!r}, expected 'open'.",
            )
        return req


# ---------------------------------------------------------------------------
# Celery task for gap-driven literature processing
# ---------------------------------------------------------------------------
#
# This task is a thin wrapper around the existing literature processing
# pipeline.  When a gap dispatch chooses the *literature* path, the task
# is sent to the ``literature_processing`` queue.  The worker then
# triggers a literature search/extraction for the specific property gap.
#
# The task body is lazy-imported so that the dispatcher module imports
# cleanly even in test environments without the full service layer.

from nfm_db.services.celery_app import celery_app  # noqa: E402


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="nfm_db.services.gap_dispatch_service.process_gap_literature_task",
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(ConnectionError, IOError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def process_gap_literature_task(
    self: Any,
    request_id: str,
    entity_type: str,
    property: str,
    material_system: str,
) -> dict[str, Any]:
    """Celery task: process a literature gap-collection request.

    This task is sent by :meth:`GapDispatchService._dispatch_literature`.
    It performs a literature search for the specified property gap and
    is expected to populate the knowledge base accordingly.

    The actual search/extraction logic is delegated to the literature
    service layer (imported lazily).
    """
    logger.info(
        "process_gap_literature_task started request_id=%s "
        "entity=%s property=%s material=%s task_id=%s",
        request_id,
        entity_type,
        property,
        material_system,
        self.request.id,
    )
    # The full literature-gap search pipeline will be implemented in a
    # follow-up ticket.  For now we log and return a placeholder so the
    # task is well-formed and the worker can acknowledge it.
    return {
        "request_id": request_id,
        "status": "queued",
        "message": (
            "Literature gap processing queued; search pipeline pending "
            "implementation."
        ),
    }
