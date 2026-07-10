"""Tests for multimodal extraction API wiring (NFM-853.4).

Covers wiring between V4 extraction API and the multimodal pipeline:
- ExtractionJob multimodal field defaults and overrides
- trigger_extraction passes multimodal params to the job
- Multimodal stage runs when extract_figures/extract_tables are set
- Multimodal stage is skipped by default (backward compat)
- Multimodal stage failure does NOT fail the overall job
- Stub mode returns mock multimodal data
"""

from __future__ import annotations

import os
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


# ---------------------------------------------------------------------------
# ExtractionJob multimodal fields
# ---------------------------------------------------------------------------


class TestExtractionJobMultimodalFields:
    """Tests for multimodal fields on ExtractionJob dataclass."""

    def test_extract_figures_default_false(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.extract_figures is False

    def test_extract_tables_default_false(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.extract_tables is False

    def test_figure_types_default_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.figure_types is None

    def test_confidence_threshold_default(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.confidence_threshold == 0.5

    def test_conflict_strategy_default(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.conflict_strategy == "prefer_vlm"

    def test_figures_default_empty_list(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.figures == []

    def test_tables_default_empty_list(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.tables == []

    def test_set_extract_figures_true(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            extract_figures=True,
        )
        assert job.extract_figures is True

    def test_set_extract_tables_true(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            extract_tables=True,
        )
        assert job.extract_tables is True

    def test_set_figure_types(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            figure_types=["line", "scatter"],
        )
        assert job.figure_types == ["line", "scatter"]

    def test_set_confidence_threshold(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            confidence_threshold=0.8,
        )
        assert job.confidence_threshold == 0.8

    def test_set_conflict_strategy(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            conflict_strategy="prefer_text",
        )
        assert job.conflict_strategy == "prefer_text"

    def test_both_multimodal_flags(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="s1",
            source_type="doi",
            extract_figures=True,
            extract_tables=True,
        )
        assert job.extract_figures is True
        assert job.extract_tables is True


# ---------------------------------------------------------------------------
# trigger_extraction multimodal parameter passing
# ---------------------------------------------------------------------------


class TestTriggerExtractionMultimodalParams:
    """Tests that trigger_extraction passes multimodal params to the job."""

    @pytest.mark.asyncio
    async def test_figures_param_stored_on_job(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
            )
            assert job.extract_figures is True
            assert job.source_reference == "test_source"

    @pytest.mark.asyncio
    async def test_tables_param_stored_on_job(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_tables=True,
            )
            assert job.extract_tables is True

    @pytest.mark.asyncio
    async def test_both_flags_stored_on_job(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
                extract_tables=True,
            )
            assert job.extract_figures is True
            assert job.extract_tables is True

    @pytest.mark.asyncio
    async def test_figure_types_stored_on_job(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
                figure_types=["line", "bar"],
            )
            assert job.figure_types == ["line", "bar"]

    @pytest.mark.asyncio
    async def test_confidence_threshold_stored_on_job(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
                confidence_threshold=0.75,
            )
            assert job.confidence_threshold == 0.75

    @pytest.mark.asyncio
    async def test_conflict_strategy_stored_on_job(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
                conflict_strategy="prefer_text",
            )
            assert job.conflict_strategy == "prefer_text"

    @pytest.mark.asyncio
    async def test_default_no_multimodal(self) -> None:
        """Without multimodal flags, defaults are backward compatible."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )

            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
            )
            assert job.extract_figures is False
            assert job.extract_tables is False


# ---------------------------------------------------------------------------
# Multimodal stage execution (stub mode)
# ---------------------------------------------------------------------------


class TestMultimodalStageStubMode:
    """Tests multimodal stage behavior in stub mode."""

    @pytest.mark.asyncio
    async def test_stub_figures_populated(self) -> None:
        """In stub mode, extract_figures=True populates job.figures."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
        )

        extracted = [
            {"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"},
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
            )
            assert len(job.figures) > 0
            assert all("figure_type" in fig for fig in job.figures)

    @pytest.mark.asyncio
    async def test_stub_tables_populated(self) -> None:
        """In stub mode, extract_tables=True populates job.tables."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
        )

        extracted = [
            {"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"},
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_tables=True,
            )
            assert len(job.tables) > 0
            assert all("figure_type" in tbl for tbl in job.tables)

    @pytest.mark.asyncio
    async def test_stub_both_figures_and_tables(self) -> None:
        """In stub mode with both flags, both job.figures and job.tables populated."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
        )

        extracted = [
            {"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"},
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
                extract_tables=True,
            )
            assert len(job.figures) > 0
            assert len(job.tables) > 0

    @pytest.mark.asyncio
    async def test_no_multimodal_by_default(self) -> None:
        """Without flags, no figures or tables are extracted."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
        )

        extracted = [
            {"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"},
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
            )
            assert job.figures == []
            assert job.tables == []


# ---------------------------------------------------------------------------
# Multimodal stage failure is non-fatal
# ---------------------------------------------------------------------------


class TestMultimodalStageFailure:
    """Tests that multimodal extraction failures don't fail the overall job."""

    @pytest.mark.asyncio
    async def test_vlm_failure_continues_pipeline(self) -> None:
        """When VLM extraction fails, the pipeline should still complete."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
        )

        extracted = [
            {"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"},
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch(
                "nfm_db.services.multimodal_extraction._extract_figures_from_source",
                new_callable=AsyncMock,
                side_effect=RuntimeError("VLM unavailable"),
            ),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="doi",
                extract_figures=True,
            )
            # Pipeline should NOT fail — multimodal is non-fatal
            assert job.status in (JobStatus.COMPLETED, JobStatus.PARTIAL)
