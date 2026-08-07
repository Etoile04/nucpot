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

from nfm_db.models.extraction_chunk import ExtractionChunk
from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import (
    EXTRACTION_STEP_TYPES,
    ExtractionStep,
)
from nfm_db.services import chunker as _chunker_module
from nfm_db.services.gap_scan_service import GapScanService
from nfm_db.services.quality_gate import QualityGateService

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

        # Mark completed — unless the step body already set a terminal
        # status of its own. gap_scan is the one non-fatal step (NFM-2568-T5):
        # on failure it self-reports status='failed' rather than raising,
        # and the orchestrator must respect that instead of overwriting
        # with 'completed'.
        if step.status == "running":
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
        """Build deterministic parameter dict for hash computation.

        Per-step additions:
        - ``chunk``: includes ``content`` so the hash changes when the
          source text changes.
        - ``extract``: includes the sorted ``chunk_ids`` for the job so the
          hash changes when chunks are added/removed.
        - ``map``: includes the ``raw_extractions`` being mapped so the hash
          changes when the extract step produces different properties.
        """
        params: dict[str, Any] = {
            "step_type": step_type,
            "source_reference": self._job.source_reference,
            "source_type": self._job.source_type,
        }
        # Include pipeline-level options that affect step output.
        for key in ("element_systems", "cache_level", "max_confidence"):
            if key in kwargs:
                params[key] = kwargs[key]

        if step_type == "chunk" and "content" in kwargs:
            params["content"] = kwargs["content"]

        if step_type == "extract":
            chunk_ids = sorted(
                str(c.id) for c in self._context.get("chunks", [])
            )
            params["chunk_ids"] = chunk_ids
            params["extract_figures"] = bool(kwargs.get("extract_figures", False))

        if step_type == "map":
            # map's input is the raw_extractions carried forward from
            # _step_extract. Include them so skip detection is
            # content-aware (NFM-2568-T2): a re-run whose extractions
            # changed must not reuse a stale mapping.
            params["raw_extractions"] = self._context.get("raw_extractions") or []

        if step_type == "gap_scan":
            # gap_scan's input is the staged_properties carried forward
            # from _step_quality_gate. Include them so skip detection
            # is content-aware (NFM-2568-T5).
            staged = self._context.get("passed_properties") or []
            params["staged_properties"] = staged

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
        """Step 1: Chunk source content via the new chunker module.

        Uses ``nfm_db.services.chunker.chunk_text`` (NFM-2567) which
        records ``source_span`` offsets for every chunk.  Each chunk is
        persisted as an :class:`ExtractionChunk` row keyed by
        ``job_id`` + ``chunk_index`` and the chunk list is exposed via
        ``self._context["chunks"]`` for downstream steps.

        When ``content`` is not supplied, the step still completes
        successfully with an empty chunk list — callers that only want
        to exercise the downstream pipeline (e.g. map / quality_gate /
        gap_scan) can do so without touching the chunker.
        """
        content = kwargs.get("content")
        source_type = self._job.source_type or kwargs.get("source_type", "unknown")

        if content is None:
            logger.warning(
                "Step 'chunk' for job %s: no content supplied — "
                "producing empty chunk list",
                self._job.id,
            )
            self._context["chunks"] = []
            return

        logger.info(
            "Step 'chunk' for job %s: chunking %d chars (source_type=%s)",
            self._job.id,
            len(content),
            source_type,
        )

        # Use the new chunker module (NFM-2567) — not _chunk_content().
        chunk_data_list = _chunker_module.chunk_text(content)

        # Persist ExtractionChunk records.
        chunks: list[ExtractionChunk] = []
        for idx, cdata in enumerate(chunk_data_list):
            chunk = ExtractionChunk(
                job_id=self._job.id,
                content=cdata.content,
                source_span=dict(cdata.source_span),
                chunk_index=idx,
                token_count=cdata.token_estimate,
                source_reference=self._job.source_reference,
            )
            self._session.add(chunk)
            chunks.append(chunk)

        await self._session.flush()

        # Expose chunks to downstream steps via context.
        self._context["chunks"] = chunks
        logger.info(
            "Step 'chunk' for job %s: persisted %d chunks",
            self._job.id,
            len(chunks),
        )

    async def _step_extract(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 2: Extract structured data from each chunk via ontofuel_extract.

        Wraps ``ontofuel_extract()`` (NFM-66/NFM-523.3) per chunk.  Each
        chunk's content is fed via a temporary file so the existing
        ``ontofuel_extract`` internals stay untouched.  Raw extraction
        results are tagged with ``chunk_id`` and exposed via
        ``self._context["raw_extractions"]``.

        When no chunks are present (e.g. the chunk step found no content
        to chunk) the step still completes successfully with an empty
        ``raw_extractions`` list — downstream steps (map, quality_gate,
        gap_scan) handle empty input gracefully.
        """
        # Chunks come from context (populated by _step_chunk) or kwargs.
        chunks: list[ExtractionChunk] = list(
            self._context.get("chunks") or kwargs.get("chunks") or []
        )
        if not chunks:
            logger.warning(
                "Step 'extract' for job %s: no chunks — producing empty "
                "raw_extractions",
                self._job.id,
            )
            self._context["raw_extractions"] = []
            return

        element_systems: list[str] | None = kwargs.get("element_systems")
        cache_level: str | None = kwargs.get("cache_level")
        max_confidence: str | None = kwargs.get("max_confidence")
        extract_figures: bool = bool(kwargs.get("extract_figures", False))

        logger.info(
            "Step 'extract' for job %s: extracting from %d chunk(s) "
            "(element_systems=%s, cache_level=%s, max_confidence=%s, "
            "extract_figures=%s)",
            self._job.id,
            len(chunks),
            element_systems,
            cache_level,
            max_confidence,
            extract_figures,
        )

        # Import lazily to avoid pulling LLM client at module import time.
        from nfm_db.services.extraction_pipeline import (
            EmptyExtractionError,
            ontofuel_extract,
        )

        raw_extractions: list[dict[str, Any]] = []
        for chunk in chunks:
            # Wrap ontofuel_extract per chunk via a temp file.  The
            # function's internals are unchanged — we just give it a
            # file path pointing at this chunk's content.
            tmp_path: str | None = None
            try:
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    delete=False,
                    encoding="utf-8",
                ) as tmp:
                    tmp.write(chunk.content)
                    tmp_path = tmp.name

                chunk_results = await ontofuel_extract(
                    source_reference=tmp_path,
                    source_type="file",
                    element_systems=element_systems,
                    db=self._session,
                )
            except EmptyExtractionError:
                # Structural failures (missing DataSource etc.) — surface
                # up to caller; this step should not corrupt the pipeline.
                raise
            finally:
                if tmp_path:
                    try:
                        import os
                        os.unlink(tmp_path)
                    except OSError:
                        pass

            # Tag every result with the originating chunk for traceability.
            for item in chunk_results:
                # Immutable: create new dict rather than mutating.
                tagged = dict(item)
                tagged["chunk_id"] = chunk.id
                tagged["chunk_index"] = chunk.chunk_index
                raw_extractions.append(tagged)

        self._context["raw_extractions"] = raw_extractions
        logger.info(
            "Step 'extract' for job %s: collected %d raw extractions from %d chunk(s)",
            self._job.id,
            len(raw_extractions),
            len(chunks),
        )

    async def _step_map(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 3: Map extracted properties to ontology conventions (NFM-2568-T2).

        Wraps the existing ``_apply_property_mapping`` helper from
        ``extraction_pipeline`` so the V2 orchestrator reuses the
        battle-tested normalization logic without modifying it.

        On success the mapped properties are persisted to
        ``step.metadata_`` (queryable from the DB) and forwarded to
        downstream steps via ``self._context["mapped_properties"]``.

        On failure the step is marked ``failed`` with an error
        message, then re-raised so the orchestrator's outer ``run``
        loop halts and marks the job as failed.
        """
        # Import lazily to avoid a top-level cycle and to keep the
        # orchestrator's import surface small.
        from nfm_db.services.extraction_pipeline import _apply_property_mapping

        # Input is whatever _step_extract produced.  Default to [] when
        # the previous step did not run (defensive — orchestrator order
        # makes this unlikely, but tests may construct isolated runs).
        raw_input: list[dict[str, Any]] = list(
            self._context.get("raw_extractions", []),
        )
        cache_level: str | None = kwargs.get("cache_level")

        try:
            mapped = _apply_property_mapping(
                raw_properties=raw_input,
                cache_level=cache_level,
            )
        except Exception as exc:
            logger.exception(
                "Step 'map' failed for job %s", self._job.id,
            )
            # Record the failure on the step record itself, then
            # re-raise so ``run()`` halts the pipeline and marks the
            # job as failed.  We do NOT swallow the exception.
            step.status = "failed"
            step.error_message = (
                f"{type(exc).__name__}: {exc}"
            )
            step.completed_at = datetime.now(UTC)
            self._session.add(step)
            await self._session.flush()
            raise

        # Persist mapped results on the step record so they are
        # queryable from the DB even if downstream steps fail.
        # ``metadata_`` uses the trailing-underscore column name to
        # avoid clashing with SQLAlchemy's MetaData class.
        step.metadata_ = {
            "input_count": len(raw_input),
            "mapped_count": len(mapped),
            "cache_level": cache_level,
            "mapped_properties": mapped,
        }
        self._session.add(step)
        await self._session.flush()

        # Forward to downstream steps (quality_gate, gap_scan).
        self._context["mapped_properties"] = mapped

        logger.info(
            "Step 'map' for job %s — %d → %d properties (cache_level=%s)",
            self._job.id,
            len(raw_input),
            len(mapped),
            cache_level,
        )

    async def _step_quality_gate(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 4: Quality gate — dedup, range validate, confidence (NFM-2588).

        Wraps :meth:``QualityGateService.process_bulk`` over the
        mapped properties produced by :meth:``_step_map``. Each
        accepted record is staged via
        :meth:``QualityGateService.stage_record`` with
        self._job.id as the fill_batch_id so downstream stages
        can trace staged rows back to the originating extraction job.

        Counts of staged / rejected / duplicate records are persisted
        on the step's metadata_ field for operator inspection.
        Failures propagate to :meth:``run`` which marks the step
        failed and halts the pipeline (the orchestrator's outer
        _execute_step handles error recording).
        """
        mapped_properties: list[dict[str, Any]] = list(
            self._context.get("mapped_properties", []),
        )

        # Lazy import to avoid a top-level cycle (extraction_pipeline
        # already imports the orchestrator module indirectly).
        from nfm_db.services.extraction_pipeline import _find_matching

        staged_count = 0
        rejected_count = 0
        duplicate_count = 0

        if mapped_properties:
            gate = QualityGateService(self._session)
            bulk_result = await gate.process_bulk(mapped_properties)

            fill_batch_id = self._job.id

            for gate_result in bulk_result.accepted:
                matching_raw = _find_matching(
                    mapped_properties, gate_result.dedup_hash,
                )
                if matching_raw is None:
                    continue
                await gate.stage_record(
                    matching_raw,
                    gate_result,
                    fill_batch_id=fill_batch_id,
                )
                staged_count += 1

            rejected_count = len(bulk_result.rejected)
            duplicate_count = len(bulk_result.duplicates)

        step.metadata_ = {
            "staged": staged_count,
            "rejected": rejected_count,
            "duplicates": duplicate_count,
        }
        self._session.add(step)
        await self._session.flush()

        self._context["quality_gate_result"] = {
            "staged": staged_count,
            "rejected": rejected_count,
            "duplicates": duplicate_count,
        }

    async def _step_gap_scan(
        self,
        step: ExtractionStep,
        **kwargs: Any,
    ) -> None:
        """Step 5: Scan for and record knowledge gaps (NFM-2568-T5).

        Wraps :meth:`GapScanService.scan_gaps` and persists the result
        on the step record and in shared context. This is the **one
        non-fatal** step in the orchestrator: if ``scan_gaps`` raises,
        the failure is recorded on the step (``status='failed'`` +
        ``error_message``) but does NOT halt the pipeline. Other steps
        propagate exceptions, which mark the job as ``failed`` upstream
        in :meth:`_execute_step`.

        Input hash: SHA-256 of the serialized ``staged_properties``
        carried forward from the quality_gate step. Stored on
        ``step.input_hash`` for skip detection (already hashed by
        :meth:`_build_step_params` but kept explicit here for clarity)
        and mirrored into ``step.metadata_['input_hash']`` for
        operator-visible traceability.
        """
        staged_properties: list = self._context.get("passed_properties") or []

        # SHA-256 of the JSON-serialized staged_properties — same
        # canonicalization as :func:`compute_input_hash` so the two
        # hashes match exactly.
        data_hash = compute_input_hash({"staged_properties": staged_properties})
        step.input_hash = data_hash
        step.metadata_ = {
            "input_hash": data_hash,
            "non_fatal": True,
        }

        try:
            scanner = GapScanService(self._session)
            element_systems = kwargs.get("element_systems")
            result = await scanner.scan_gaps(element_systems=element_systems)

            gap_tuples = list(result.gaps)
            self._context["gaps"] = gap_tuples
            step.metadata_ = {
                "input_hash": data_hash,
                "gap_count": len(gap_tuples),
                "covered": result.stats.covered,
                "total_targets": result.stats.total_target_tuples,
                "non_fatal": True,
            }
            logger.info(
                "Step 'gap_scan' for job %s: %d gaps identified "
                "(covered=%d/%d)",
                self._job.id,
                len(gap_tuples),
                result.stats.covered,
                result.stats.total_target_tuples,
            )
        except Exception as exc:
            # Non-fatal path: capture the failure on the step row so
            # operators can see why the scan was skipped, but DO NOT
            # re-raise — the rest of the pipeline should complete.
            logger.warning(
                "Step 'gap_scan' failed (non-fatal) for job %s: %s",
                self._job.id,
                exc,
                exc_info=True,
            )
            step.status = "failed"
            step.error_message = f"gap_scan failed (non-fatal): {exc}"
            step.completed_at = datetime.now(UTC)
            step.metadata_ = {
                "input_hash": data_hash,
                "gap_count": 0,
                "non_fatal": True,
                "error": str(exc),
            }
