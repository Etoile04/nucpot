"""NFM-2593 parity test: flag=True (orchestrator) vs flag=False (legacy).

Both code paths must produce equivalent results for the same input:
- Same staged properties count
- Same rejected properties count
- Same gap scan call behavior
- Same final job status

Both paths share the underlying helpers (ontofuel_extract, _apply_property_mapping,
QualityGateService, GapScanService); only the orchestration surface differs.

This test mocks at the LLM extraction boundary (the only real-world
side effect that varies per source) and asserts structural parity.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixed input fixture
# ---------------------------------------------------------------------------


SAMPLE_RAW_EXTRACTIONS: list[dict[str, Any]] = [
    {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "lattice_constant",
        "value": 5.47,
        "unit": "angstrom",
        "method": "DFT",
        "source": "doi:10.1234/parity",
        "source_doi": "10.1234/parity",
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
        "source": "doi:10.1234/parity",
        "source_doi": "10.1234/parity",
        "confidence": "high",
        "uncertainty": 1.0,
        "temperature": 300.0,
    },
    {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "formation_energy",
        "value": -10.5,
        "unit": "eV",
        "method": "DFT",
        "source": "doi:10.1234/parity",
        "source_doi": "10.1234/parity",
        "confidence": "medium",
        "uncertainty": 0.2,
        "temperature": 0.0,
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
    """Build a deterministic bulk result matching the sample input.

    Uses real compute_dedup_hash output so ``_find_matching`` resolves
    the same way in both legacy and orchestrator paths.
    """
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


def _build_gap_result(count: int) -> _GapResult:
    """Build a deterministic gap-scan result."""
    return _GapResult(
        gaps=[_GapTuple() for _ in range(count)],
        stats=_GapStats(covered=0, total_target_tuples=count),
    )


# ---------------------------------------------------------------------------
# Path runners
# ---------------------------------------------------------------------------


async def _run_legacy(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the legacy trigger_extraction path (flag=False) and return summary."""
    from nfm_db.services.extraction_pipeline import trigger_extraction

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    bulk = _build_bulk_result_for(raw)
    gap = _build_gap_result(1)

    # NFM-2921 (W1 follow-up): force the V1-flag branch so the legacy
    # mocks at ``extraction_pipeline.QualityGateService`` /
    # ``extraction_pipeline.GapScanService`` apply even when the
    # ambient ``NFM_EXTRACTION_V2_ENABLED=true`` would otherwise route
    # through the V2 orchestrator branch.
    fake_settings = MagicMock()
    fake_settings.extraction_v2_enabled = False

    with (
        patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
        patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new_callable=AsyncMock,
            return_value=list(raw),
        ),
        patch(
            "nfm_db.services.extraction_pipeline.QualityGateService"
        ) as mock_qg_cls,
        patch("nfm_db.services.extraction_pipeline.GapScanService") as mock_gap_cls,
        patch("nfm_db.config.get_settings", lambda: fake_settings),
    ):
        mock_qg = mock_qg_cls.return_value
        mock_qg.process_bulk = AsyncMock(return_value=bulk)
        mock_qg.stage_record = AsyncMock()

        mock_gap_scanner = mock_gap_cls.return_value
        mock_gap_scanner.scan_gaps = AsyncMock(return_value=gap)

        job = await trigger_extraction(
            mock_session,
            source_reference="doi:10.1234/parity",
            source_type="doi",
        )

    # Count gap-scan invocations: the legacy path calls scan_gaps() once
    # after staging (line ~759 of extraction_pipeline.py).
    return {
        "status": job.status,
        "fill_batch_id": job.fill_batch_id,
        "extracted_count": job.extracted_count,
        "accepted_count": job.staged_count,
        "rejected_count": job.rejected_count,
        "duplicate_count": 0,  # legacy path does not surface duplicates
        "gap_scan_calls": mock_gap_scanner.scan_gaps.await_count,
    }


async def _run_orchestrator(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the ExtractionOrchestrator path (flag=True) and return summary.

    We short-circuit the LLM-bound steps (chunk, extract) by injecting
    raw extractions directly into the orchestrator's context, mirroring
    what _step_extract would produce after a real LLM call.
    """
    from nfm_db.models.extraction_job import ExtractionJob
    from nfm_db.services.extraction_orchestrator import ExtractionOrchestrator

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()
    # Make session.execute(stmt).scalar_one_or_none() return None so the
    # orchestrator's skip-detection in _find_completed_step does not falsely
    # treat every step as already-completed.
    _exec_result = MagicMock()
    _exec_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=_exec_result)

    bulk = _build_bulk_result_for(raw)
    gap = _build_gap_result(1)

    orm_job = ExtractionJob(
        source_reference="doi:10.1234/parity",
        source_type="doi",
        extract_figures=False,
        extract_tables=False,
    )
    # Pre-set a stable id so the fill_batch_id comparison is deterministic.
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

        orchestrator = ExtractionOrchestrator(mock_session, orm_job)

        async def fake_chunk(step, **kwargs):
            step.status = "completed"
            step.completed_at = datetime.now(UTC)

        async def fake_extract(step, **kwargs):
            step.status = "completed"
            step.completed_at = datetime.now(UTC)
            orchestrator._context["raw_extractions"] = list(raw)

        orchestrator._step_chunk = fake_chunk  # type: ignore[assignment]
        orchestrator._step_extract = fake_extract  # type: ignore[assignment]

        job = await orchestrator.run()

    qg_meta = orchestrator._context.get("quality_gate_result", {})
    return {
        "status": job.status,
        "fill_batch_id": str(job.id),
        "extracted_count": len(raw),
        "accepted_count": qg_meta.get("staged", 0),
        "rejected_count": qg_meta.get("rejected", 0),
        "duplicate_count": qg_meta.get("duplicates", 0),
        "gap_scan_calls": mock_gap_scanner.scan_gaps.await_count,
    }


# ---------------------------------------------------------------------------
# Parity assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_flag_false_vs_flag_true_equivalent_results() -> None:
    """Same input → equivalent counts under flag=False (legacy) and flag=True (orchestrator)."""
    legacy = await _run_legacy(SAMPLE_RAW_EXTRACTIONS)
    new = await _run_orchestrator(SAMPLE_RAW_EXTRACTIONS)

    assert legacy["extracted_count"] == new["extracted_count"], (
        f"extracted_count mismatch: legacy={legacy['extracted_count']} "
        f"new={new['extracted_count']}"
    )
    assert legacy["accepted_count"] == new["accepted_count"], (
        f"accepted_count mismatch: legacy={legacy['accepted_count']} "
        f"new={new['accepted_count']}"
    )
    assert legacy["rejected_count"] == new["rejected_count"], (
        f"rejected_count mismatch: legacy={legacy['rejected_count']} "
        f"new={new['rejected_count']}"
    )
    assert legacy["duplicate_count"] == new["duplicate_count"], (
        f"duplicate_count mismatch: legacy={legacy['duplicate_count']} "
        f"new={new['duplicate_count']}"
    )
    # Both paths must invoke the gap-scan service exactly once on success.
    assert legacy["gap_scan_calls"] == new["gap_scan_calls"] == 1, (
        f"gap_scan_calls mismatch: legacy={legacy['gap_scan_calls']} "
        f"new={new['gap_scan_calls']}"
    )

    # Final status must match.
    assert legacy["status"] == new["status"]


def test_parity_flag_default_is_false() -> None:
    """The acceptance criterion — flag defaults to False (do NOT flip default)."""
    from nfm_db.config import Settings

    os.environ.pop("NFM_EXTRACTION_V2_ENABLED", None)
    s = Settings()
    assert s.extraction_v2_enabled is False


@pytest.mark.asyncio
async def test_parity_with_zero_rejections_and_duplicates() -> None:
    """When all properties pass the gate, both paths report zero rejected/duplicates."""
    legacy = await _run_legacy(SAMPLE_RAW_EXTRACTIONS[:1])
    new = await _run_orchestrator(SAMPLE_RAW_EXTRACTIONS[:1])

    assert legacy["rejected_count"] == 0
    assert new["rejected_count"] == 0
    assert legacy["duplicate_count"] == 0
    assert new["duplicate_count"] == 0
    assert legacy["accepted_count"] == new["accepted_count"] == 1
    assert legacy["gap_scan_calls"] == new["gap_scan_calls"] == 1


@pytest.mark.asyncio
async def test_parity_empty_input_returns_no_staged_no_gaps() -> None:
    """Empty extraction list → both paths stage zero records and skip gap scan in legacy."""
    legacy = await _run_legacy([])
    new = await _run_orchestrator([])

    assert legacy["extracted_count"] == 0
    assert new["extracted_count"] == 0
    assert legacy["accepted_count"] == 0
    assert new["accepted_count"] == 0
    assert legacy["rejected_count"] == 0
    assert new["rejected_count"] == 0
