"""ExtractionOrchestrator — V2 pipeline shell (NFM-2568-T1).

Replaces the monolithic ``trigger_extraction`` with a step-based
orchestrator that records per-step progress in the ``extraction_steps``
table (NFM-2567).  Each step checks an ``input_hash`` for skip
detection and persists ``error_message`` on failure, halting the
pipeline.

This is a skeleton: step bodies are stubs that will be filled in by
subsequent tasks (T2–T6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import (
    EXTRACTION_STEP_TYPES,
    ExtractionStep,
)

logger = logging.getLogger(__name__)

# Ordered pipeline steps executed by the orchestrator.
_PIPELINE_STEPS: list[str] = [
    "chunk",
    "extract",
    "map",
    "quality_gate",
    "gap_scan",
]


def compute_input_hash(params: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hex digest for step parameters.

    Used for skip detection: if a previous step with the same hash
    completed successfully, the step can be skipped on re-run.
    """
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ExtractionOrchestrator:
    """Step-based extraction pipeline orchestrator (NFM-2568-T1).

    Wraps an :class:`ExtractionJob` and drives it through the five
    pipeline stages: chunk → extract → map → quality_gate → gap_scan.

    Each step records its status in the ``extraction_steps`` table.
    On failure the pipeline stops and the error is persisted.
    """

    def __init__(
        self,
        session: AsyncSession,
        job: ExtractionJob,
    ) -> None:
        self._session = session
        self._job = job
        # Shared context passed between steps (populated by each step).
        self._context: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> ExtractionJob:
        """Execute all pipeline steps sequentially.

        Returns the job with updated status.  On any step failure the
        pipeline stops immediately and the error is recorded.
        """
        self._job.status = "processing"
        self._job.started_at = datetime.now(UTC)
        self._session.add(self._job)
        await self._session.flush()

        for step_type in _PIPELINE_STEPS:
            try:
                await self._execute_step(step_type, **kwargs)
            except Exception as exc:
                await self._fail_job(
                    f"Step '{step_type}' failed: {exc}",
                )
                return self._job

        self._job.status = "completed"
        self._job.completed_at = datetime.now(UTC)
        self._session.add(self._job)
        await self._session.flush()
        return self._job

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step_type: str,
        **kwargs: Any,
    ) -> None:
        """Run a single pipeline step with skip detection."""
        if step_type not in EXTRACTION_STEP_TYPES:
            raise ValueError(
                f"Unknown step type '{step_type}'; "
                f"must be one of {EXTRACTION_STEP_TYPES}"
            )

        step_params = self._build_step_params(step_type, **kwargs)
        input_hash = compute_input_hash(step_params)

        # Skip detection: if a previous run completed with the same
        # hash, skip this step.
        existing = await self._find_completed_step(
            self._job.id, step_type, input_hash,
        )
        if existing is not None:
            logger.info(
                "Skipping step '%s' for job %s — hash match",
                step_type,
                self._job.id,
            )
            skipped = ExtractionStep(
                job_id=self._job.id,
                step_type=step_type,
                status="skipped",
                input_hash=input_hash,
            )
            self._session.add(skipped)
            await self._session.flush()
            return

        # Create step record.
        step = ExtractionStep(
            job_id=self._job.id,
            step_type=step_type,
            status="running",
            input_hash=input_hash,
            started_at=datetime.now(UTC),
        )
        self._session.add(step)
        await self._session.flush()

        # Dispatch to step implementation.
        step_fn = {
            "chunk": self._step_chunk,
            "extract": self._step_extract,
            "map": self._step_map,
            "quality_gate": self._step_quality_gate,
            "gap_scan": self._step_gap_scan,
        }[step_type]

        await step_fn(step, **kwargs)

        # Mark completed.
        step.status = "completed"
        step.completed_at = datetime.now(UTC)
        self._session.add(step)
        await self._session.flush()

    async def _find_completed_step(
        self,
        job_id: uuid.UUID,
        step_type: str,
        input_hash: str,
    ) -> ExtractionStep | None:
        """Look up a previously completed step with matching hash."""
        stmt = (
            select(ExtractionStep)
            .where(
                ExtractionStep.job_id == job_id,
                ExtractionStep.step_type == step_type,
                ExtractionStep.input_hash == input_hash,
                ExtractionStep.status == "completed",
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    def _build_step_params(
        self,
        step_type: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build deterministic parameter dict for hash computation."""
        params: dict[str, Any] = {
            "step_type": step_type,
            "source_reference": self._job.source_reference,
            "source_type": self._job.source_type,
        }
        # Include pipeline-level options that affect step output.
        for key in ("element_systems", "cache_level", "max_confidence"):
            if key in kwargs:
                params[key] = kwargs[key]
        return params

    async def _fail_job(self, error_message: str) -> None:
        """Mark the current job as failed with an error message."""
        self._job.status = "failed"
        self._job.error_message = error_message
        self._job.completed_at = datetime.now(UTC)
        self._session.add(self._job)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Step stubs (skeleton — real implementations in T2–T6)
    # ------------------------------------------------------------------

    async def _step_chunk(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 1: Chunk source content into processable pieces."""
        logger.info(
            "Step 'chunk' for job %s (stub)", self._job.id,
        )
        self._context["chunks"] = []

    async def _step_extract(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 2: Extract structured data from chunks via LLM."""
        logger.info(
            "Step 'extract' for job %s (stub)", self._job.id,
        )
        self._context["raw_extractions"] = []

    async def _step_map(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 3: Map extracted properties to ontology conventions."""
        logger.info(
            "Step 'map' for job %s (stub)", self._job.id,
        )
        self._context["mapped_properties"] = []

    async def _step_quality_gate(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 4: Quality gate — dedup, range validate, confidence."""
        logger.info(
            "Step 'quality_gate' for job %s (stub)", self._job.id,
        )
        self._context["passed_properties"] = []

    async def _step_gap_scan(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 5: Scan for and record knowledge gaps."""
        logger.info(
            "Step 'gap_scan' for job %s (stub)", self._job.id,
        )
        self._context["gaps"] = []
