"""NFM-2593 DB-write overhead benchmark: orchestrator path.

Counts the cumulative number of ``session.add()`` and ``session.flush()``
calls issued by the orchestrator code path on a mocked AsyncSession.

The legacy-vs-new comparison test was removed in NFM-3008 when the V1
legacy path was deleted (strangler-fig cutover complete). The remaining
tests verify orchestrator-only write patterns.

The benchmark is structural — it counts call sites rather than timing
wall-clock I/O — because we mock the session to avoid pulling a real
PostgreSQL connection. This isolates the architectural overhead from
network latency, query plan cost, and pytest fixture cost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures (re-used from the parity test pattern)
# ---------------------------------------------------------------------------


SAMPLE_RAW_EXTRACTIONS: list[dict[str, Any]] = [
    {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "lattice_constant",
        "value": 5.47,
        "unit": "angstrom",
        "method": "DFT",
        "source": "doi:10.1234/bench",
        "source_doi": "10.1234/bench",
        "confidence": "high",
        "uncertainty": 0.01,
        "temperature": 300.0,
    },
    {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "bulk_modulus",
        "value": 207.5,
        "unit": "GPa",
        "method": "DFT",
        "source": "doi:10.1234/bench",
        "source_doi": "10.1234/bench",
        "confidence": "high",
        "uncertainty": 1.0,
        "temperature": 300.0,
    },
]


@dataclass
class _GateResult:
    dedup_hash: str
    confidence: str = "high"


@dataclass
class _BulkResult:
    accepted: list[_GateResult] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[Any] = field(default_factory=list)


@dataclass
class _GapTuple:
    pass


@dataclass
class _GapStats:
    covered: int = 0
    total_target_tuples: int = 0


@dataclass
class _GapResult:
    gaps: list[Any] = field(default_factory=list)
    stats: _GapStats = field(default_factory=_GapStats)


def _build_bulk_result_for(raw: list[dict[str, Any]]) -> _BulkResult:
    from nfm_db.services.quality_gate import compute_dedup_hash

    accepted = []
    for prop in raw:
        h = compute_dedup_hash(
            element_system=str(prop.get("element_system", "")),
            phase=prop.get("phase"),
            property_name=str(prop.get("property", prop.get("property_name", ""))),
            method=prop.get("method"),
            source=str(prop.get("source", "")),
        )
        accepted.append(_GateResult(dedup_hash=h))
    return _BulkResult(accepted=accepted, rejected=[], duplicates=[])


# ---------------------------------------------------------------------------
# Session-write counter
# ---------------------------------------------------------------------------


class _WriteCountingSession:
    """Mock AsyncSession that records add/flush/commit/execute counts."""

    def __init__(self) -> None:
        self.add_count = 0
        self.flush_count = 0
        self.commit_count = 0
        self.execute_count = 0
        # AsyncMocks for the async surface methods.
        self.flush = AsyncMock(side_effect=self._inc_flush)
        self.commit = AsyncMock(side_effect=self._inc_commit)
        self.execute = AsyncMock(side_effect=self._inc_execute)
        # session.add is sync.
        self.add = MagicMock(side_effect=self._inc_add)

    def _inc_add(self, _obj: Any) -> None:
        self.add_count += 1

    async def _inc_flush(self) -> None:
        self.flush_count += 1

    async def _inc_commit(self) -> None:
        self.commit_count += 1

    async def _inc_execute(self, _stmt: Any) -> Any:
        self.execute_count += 1
        # Default to no-op result (skip detection sees nothing).
        # NOTE: ``scalars().first()`` MUST be a sync MagicMock — see commit
        # 9d8ffacc (fix(NFM-2876): revert broken await on
        # ``result.scalars().first()``). SQLAlchemy 2.0+ ``AsyncSession.execute``
        # returns a sync ``Result`` whose ``.scalars().first()`` is the sync
        # accessor; ``await`` raises ``TypeError: object NoneType can't be
        # used in 'await' expression`` in 2.0.50 (verified empirically).
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        result.scalars = MagicMock()
        result.scalars.return_value = MagicMock()
        result.scalars.return_value.first = MagicMock(return_value=None)
        return result


# ---------------------------------------------------------------------------
# Path runners
# ---------------------------------------------------------------------------


async def _run_orchestrator_bench(
    raw: list[dict[str, Any]],
    session: _WriteCountingSession,
) -> None:
    from nfm_db.models.extraction_job import ExtractionJob
    from nfm_db.services.extraction_orchestrator import ExtractionOrchestrator

    bulk = _build_bulk_result_for(raw)
    gap = _GapResult(
        gaps=[_GapTuple()],
        stats=_GapStats(covered=0, total_target_tuples=1),
    )

    orm_job = ExtractionJob(
        source_reference="doi:10.1234/bench",
        source_type="doi",
        extract_figures=False,
        extract_tables=False,
    )
    orm_job.id = uuid.UUID(int=0x1234567890ABCDEF1234567890ABCDEF)

    with (
        patch(
            "nfm_db.services.extraction_orchestrator.QualityGateService"
        ) as mock_qg_cls,
        patch(
            "nfm_db.services.extraction_orchestrator.GapScanService"
        ) as mock_gap_cls,
    ):
        mock_qg = mock_qg_cls.return_value
        mock_qg.process_bulk = AsyncMock(return_value=bulk)
        mock_qg.stage_record = AsyncMock()

        mock_gap_scanner = mock_gap_cls.return_value
        mock_gap_scanner.scan_gaps = AsyncMock(return_value=gap)

        orchestrator = ExtractionOrchestrator(session, orm_job)  # type: ignore[arg-type]

        async def fake_chunk(step, **kwargs):
            step.status = "completed"
            step.completed_at = datetime.now(UTC)

        async def fake_extract(step, **kwargs):
            step.status = "completed"
            step.completed_at = datetime.now(UTC)
            orchestrator._context["raw_extractions"] = list(raw)

        orchestrator._step_chunk = fake_chunk  # type: ignore[assignment]
        orchestrator._step_extract = fake_extract  # type: ignore[assignment]

        await orchestrator.run()


# ---------------------------------------------------------------------------
# Benchmark assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_persists_step_rows() -> None:
    """Sanity-check: the new path persists one ExtractionStep record per pipeline step."""
    session = _WriteCountingSession()
    await _run_orchestrator_bench(SAMPLE_RAW_EXTRACTIONS, session)

    # 5 steps x (1 insert + 1 update at completion) = 10 step-row writes.
    # Plus job-level writes (status flips) and any per-chunk / per-property
    # metadata writes from the real _step_map / _step_quality_gate paths.
    assert session.add_count >= 10, (
        f"Orchestrator should write >= 10 step rows "
        f"(5 steps x insert+update), got {session.add_count}"
    )


@pytest.mark.asyncio
async def test_orchestrator_batches_step_lifecycle_flushes() -> None:
    """Step lifecycle updates wait for the run-level flush boundary."""
    session = _WriteCountingSession()
    await _run_orchestrator_bench(SAMPLE_RAW_EXTRACTIONS, session)

    # The benchmark path persists step rows in a single batch at the
    # run boundary.  Two intra-step metadata flushes remain (map and
    # quality_gate) so ``session.refresh(step)`` sees the persisted
    # metadata column.  Lifecycle flushes per step would add ten more
    # calls and recreate the overhead regression.
    assert session.flush_count == 4, (
        "Step lifecycle persistence should be batched to run boundaries, "
        f"got {session.flush_count} flushes"
    )
