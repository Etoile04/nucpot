"""Tests for multimodal pipeline stage in extraction_pipeline (NFM-980).

Covers:
- ExtractionJob multimodal field defaults
- trigger_extraction accepts and stores multimodal options
- Multimodal stage runs after text extraction when enabled
- Stub mode generates realistic mock multimodal data
- Multimodal failure does NOT affect text extraction success
- No regression in text-only extraction path (extract_figures=False, extract_tables=False)
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _job_store,
    trigger_extraction,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_job_store():
    _job_store.clear()
    yield
    _job_store.clear()


def _make_mock_session() -> AsyncMock:
    """Create a mock async session with commit."""
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


_SAMPLE_EXTRACTED: list[dict[str, Any]] = [
    {
        "property_name": "density",
        "property": "density",
        "value": 10.0,
        "source": "test_source",
        "confidence": "high",
        "element_system": "UO2",
    },
]


def _make_mock_pipeline_dependencies():
    """Return a context manager that enters all pipeline dependency patches.

    Yields a tuple of (env_dict, mock_extract, mock_qg, mock_gs) via ExitStack
    so callers can use a single ``with`` statement.
    """
    stack = ExitStack()

    @stack.callback
    def _cleanup():
        pass  # ExitStack handles unenter automatically

    cm = patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"})
    stack.enter_context(cm)

    mock_extract = stack.enter_context(
        patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock),
    )
    mock_qg = stack.enter_context(
        patch("nfm_db.services.extraction_pipeline.QualityGateService"),
    )
    mock_gs = stack.enter_context(
        patch("nfm_db.services.extraction_pipeline.GapScanService"),
    )

    return stack, mock_extract, mock_qg, mock_gs


# ---------------------------------------------------------------------------
# ExtractionJob multimodal field tests
# ---------------------------------------------------------------------------


class TestExtractionJobMultimodalFields:
    """Tests for multimodal fields on ExtractionJob dataclass."""

    def test_extract_figures_defaults_false(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.extract_figures is False

    def test_extract_tables_defaults_false(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.extract_tables is False

    def test_figure_types_defaults_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.figure_types is None

    def test_confidence_threshold_defaults_0_5(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.confidence_threshold == 0.5

    def test_conflict_strategy_defaults_prefer_vlm(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.conflict_strategy == "prefer_vlm"

    def test_figures_defaults_empty_list(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.figures == []

    def test_tables_defaults_empty_list(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.tables == []

    def test_multimodal_fields_can_be_set(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            extract_figures=True,
            extract_tables=True,
            figure_types=["line", "bar"],
            confidence_threshold=0.7,
            conflict_strategy="prefer_text",
        )
        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["line", "bar"]
        assert job.confidence_threshold == 0.7
        assert job.conflict_strategy == "prefer_text"


# ---------------------------------------------------------------------------
# trigger_extraction multimodal option tests
# ---------------------------------------------------------------------------


class TestTriggerExtractionMultimodalOptions:
    """Tests for trigger_extraction accepting multimodal options."""

    @pytest.mark.asyncio
    async def test_accepts_extract_figures_option(self) -> None:
        """trigger_extraction stores extract_figures on the job."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                session,
                source_reference="test_source",
                source_type="file",
                extract_figures=True,
            )
            assert job.extract_figures is True

    @pytest.mark.asyncio
    async def test_accepts_extract_tables_option(self) -> None:
        """trigger_extraction stores extract_tables on the job."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                session,
                source_reference="test_source",
                source_type="file",
                extract_tables=True,
            )
            assert job.extract_tables is True

    @pytest.mark.asyncio
    async def test_accepts_figure_types_option(self) -> None:
        """trigger_extraction stores figure_types on the job."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                session,
                source_reference="test_source",
                source_type="file",
                extract_figures=True,
                figure_types=["line", "heatmap"],
            )
            assert job.figure_types == ["line", "heatmap"]

    @pytest.mark.asyncio
    async def test_accepts_confidence_threshold_option(self) -> None:
        """trigger_extraction stores confidence_threshold on the job."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                session,
                source_reference="test_source",
                source_type="file",
                extract_figures=True,
                confidence_threshold=0.8,
            )
            assert job.confidence_threshold == 0.8

    @pytest.mark.asyncio
    async def test_accepts_conflict_strategy_option(self) -> None:
        """trigger_extraction stores conflict_strategy on the job."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                session,
                source_reference="test_source",
                source_type="file",
                extract_figures=True,
                conflict_strategy="prefer_text",
            )
            assert job.conflict_strategy == "prefer_text"


# ---------------------------------------------------------------------------
# Multimodal pipeline execution tests
# ---------------------------------------------------------------------------


class TestMultimodalPipelineExecution:
    """Tests for multimodal stage running after text extraction."""

    @pytest.mark.asyncio
    async def test_multimodal_runs_when_extract_figures_true(self) -> None:
        """When extract_figures=True, run_multimodal_extraction is called."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            # Non-empty results prevent early return; QG accepted=[] skips staging
            mock_extract.return_value = _SAMPLE_EXTRACTED
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            with patch(
                "nfm_db.services.multimodal_extraction.run_multimodal_extraction",
                new_callable=AsyncMock,
            ) as mock_mm:
                await trigger_extraction(
                    session,
                    source_reference="test_source",
                    source_type="file",
                    extract_figures=True,
                )
                mock_mm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multimodal_runs_when_extract_tables_true(self) -> None:
        """When extract_tables=True, run_multimodal_extraction is called."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            # Non-empty results prevent early return; QG accepted=[] skips staging
            mock_extract.return_value = _SAMPLE_EXTRACTED
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            with patch(
                "nfm_db.services.multimodal_extraction.run_multimodal_extraction",
                new_callable=AsyncMock,
            ) as mock_mm:
                await trigger_extraction(
                    session,
                    source_reference="test_source",
                    source_type="file",
                    extract_tables=True,
                )
                mock_mm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multimodal_not_called_when_both_false(self) -> None:
        """When both extract_figures and extract_tables are False, multimodal is skipped."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            with patch(
                "nfm_db.services.multimodal_extraction.run_multimodal_extraction",
                new_callable=AsyncMock,
            ) as mock_mm:
                await trigger_extraction(
                    session,
                    source_reference="test_source",
                    source_type="file",
                )
                mock_mm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stub_mode_populates_figures_and_tables(self) -> None:
        """In stub mode, figures and tables are populated on the job."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            # Non-empty results prevent early return so Stage 5 is reached
            mock_extract.return_value = _SAMPLE_EXTRACTED
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            with patch(
                "nfm_db.services.multimodal_extraction.run_multimodal_extraction",
                new_callable=AsyncMock,
            ) as mock_mm:
                # Simulate what run_multimodal_extraction does in stub mode
                def _simulate_mm(job, text_props):
                    job.figures = [
                        {
                            "figure_type": "line",
                            "title": "Stub Figure",
                            "source": job.source_reference,
                            "confidence": 0.85,
                            "provider": "stub",
                            "model": "stub",
                            "extraction_time_ms": 0.0,
                            "fallback_used": False,
                        },
                    ]
                    job.tables = [
                        {
                            "figure_type": "table",
                            "title": "Stub Table",
                            "source": job.source_reference,
                            "confidence": 0.9,
                            "headers": ["Col1", "Col2"],
                            "num_rows": 3,
                            "provider": "stub",
                            "model": "stub",
                            "extraction_time_ms": 0.0,
                            "fallback_used": False,
                        },
                    ]

                mock_mm.side_effect = _simulate_mm
                job = await trigger_extraction(
                    session,
                    source_reference="test_source",
                    source_type="file",
                    extract_figures=True,
                    extract_tables=True,
                )
                assert len(job.figures) == 1
                assert job.figures[0]["figure_type"] == "line"
                assert len(job.tables) == 1
                assert job.tables[0]["figure_type"] == "table"


# ---------------------------------------------------------------------------
# Multimodal failure isolation tests
# ---------------------------------------------------------------------------


class TestMultimodalFailureIsolation:
    """Tests that multimodal failure does NOT affect text extraction success."""

    @pytest.mark.asyncio
    async def test_multimodal_failure_does_not_fail_job(self) -> None:
        """When multimodal stage raises, job still succeeds from text extraction."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            # Text extraction returns results → pipeline succeeds
            extracted = [
                {
                    "property_name": "density",
                    "value": 10.0,
                    "source": "test",
                    "confidence": "high",
                    "property": "density",
                    "element_system": "UO2",
                },
            ]
            mock_extract.return_value = extracted

            mock_gate = AsyncMock()
            mock_gate.process_bulk = AsyncMock(
                return_value=MagicMock(
                    accepted=[MagicMock(dedup_hash="h1")],
                    rejected=[],
                    duplicates=[],
                ),
            )
            mock_gate.stage_record = AsyncMock()
            mock_qg.return_value = mock_gate

            with patch(
                "nfm_db.services.multimodal_extraction.run_multimodal_extraction",
                new_callable=AsyncMock,
                side_effect=RuntimeError("VLM service unavailable"),
            ), patch(
                "nfm_db.services.quality_gate.compute_dedup_hash",
                return_value="h1",
            ):
                job = await trigger_extraction(
                    session,
                    source_reference="test_source",
                    source_type="file",
                    extract_figures=True,
                )
                # Text extraction succeeded → job should be COMPLETED
                assert job.status == JobStatus.COMPLETED
                assert job.staged_count == 1
                assert job.error_message is None

    @pytest.mark.asyncio
    async def test_multimodal_failure_logged_as_warning(self) -> None:
        """When multimodal stage raises, a warning is logged."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            # Non-empty results prevent early return so Stage 5 is reached
            mock_extract.return_value = _SAMPLE_EXTRACTED
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            with patch(
                "nfm_db.services.multimodal_extraction.run_multimodal_extraction",
                new_callable=AsyncMock,
                side_effect=RuntimeError("VLM down"),
            ), patch("nfm_db.services.extraction_pipeline.logger") as mock_logger:
                await trigger_extraction(
                    session,
                    source_reference="test_source",
                    source_type="file",
                    extract_figures=True,
                )
                # Verify warning was logged
                mock_logger.warning.assert_called()
                warning_calls = [
                    c for c in mock_logger.warning.call_args_list
                ]
                assert any(
                    "multimodal" in str(c).lower() or "non-fatal" in str(c).lower()
                    for c in warning_calls
                )


# ---------------------------------------------------------------------------
# Text-only regression tests
# ---------------------------------------------------------------------------


class TestTextOnlyRegression:
    """Ensure no regression in the text-only extraction path."""

    @pytest.mark.asyncio
    async def test_text_only_pipeline_completes(self) -> None:
        """Text-only pipeline (no multimodal options) completes as before."""
        session = _make_mock_session()
        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = []
            mock_qg.return_value.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                session,
                source_reference="text_only_source",
                source_type="file",
            )
            assert job.status == JobStatus.COMPLETED
            assert job.extract_figures is False
            assert job.extract_tables is False
            assert job.figures == []
            assert job.tables == []

    @pytest.mark.asyncio
    async def test_text_only_with_staged_results(self) -> None:
        """Text-only with accepted results still stages correctly."""
        session = _make_mock_session()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[],
                duplicates=[],
            ),
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock()

        extracted = [
            {
                "property_name": "density",
                "value": 10.0,
                "source": "test",
                "confidence": "high",
                "property": "density",
                "element_system": "UO2",
            },
        ]

        stack, mock_extract, mock_qg, mock_gs = _make_mock_pipeline_dependencies()
        with stack:
            mock_extract.return_value = extracted
            mock_qg.return_value = mock_gate
            mock_gs.return_value = mock_scanner

            with patch(
                "nfm_db.services.quality_gate.compute_dedup_hash",
                return_value="h1",
            ):
                job = await trigger_extraction(
                    session,
                    source_reference="text_only_staged",
                    source_type="doi",
                )
            assert job.status == JobStatus.COMPLETED
            assert job.staged_count == 1
            assert job.figures == []
            assert job.tables == []
