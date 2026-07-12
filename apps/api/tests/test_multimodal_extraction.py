"""Tests for multimodal extraction helpers (NFM-979).

Covers:
- ExtractionJob multimodal field defaults
- _apply_conflict_resolution: prefer_vlm, prefer_text, merge strategies
- _extract_figures_from_source: stub mode, filtering, fault tolerance
- _extract_tables_from_source: stub mode, filtering, fault tolerance
- run_multimodal_extraction: orchestration wiring
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.extraction_pipeline import ExtractionJob
from nfm_db.services.multimodal_extraction import (
    _apply_conflict_resolution,
    _extract_figures_from_source,
    _extract_tables_from_source,
    run_multimodal_extraction,
)

# ---------------------------------------------------------------------------
# ExtractionJob field defaults (NFM-979 AC: 7 new fields)
# ---------------------------------------------------------------------------


class TestExtractionJobMultimodalFields:
    """Verify all 7 multimodal fields exist with correct defaults."""

    def test_extract_figures_default_false(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.extract_figures is False

    def test_extract_tables_default_false(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.extract_tables is False

    def test_figure_types_default_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.figure_types is None

    def test_confidence_threshold_default(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.confidence_threshold == 0.5

    def test_conflict_strategy_default(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.conflict_strategy == "prefer_vlm"

    def test_figures_default_empty_list(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.figures == []

    def test_tables_default_empty_list(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="src.md", source_type="markdown")
        assert job.tables == []

    def test_custom_values_accepted(self) -> None:
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.md",
            source_type="markdown",
            extract_figures=True,
            extract_tables=True,
            figure_types=["line", "bar"],
            confidence_threshold=0.8,
            conflict_strategy="merge",
            figures=[{"type": "line"}],
            tables=[{"type": "table"}],
        )
        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["line", "bar"]
        assert job.confidence_threshold == 0.8
        assert job.conflict_strategy == "merge"
        assert job.figures == [{"type": "line"}]
        assert job.tables == [{"type": "table"}]


# ---------------------------------------------------------------------------
# _apply_conflict_resolution
# ---------------------------------------------------------------------------


class TestConflictResolution:
    """Test conflict resolution between text and VLM properties."""

    def _text_props(self) -> list[dict[str, Any]]:
        return [
            {"property_name": "lattice_constant", "value": 5.47, "confidence": "high"},
            {"property_name": "bulk_modulus", "value": 207.0, "confidence": "medium"},
            {"property_name": "thermal_conductivity", "value": 7.5, "confidence": "low"},
        ]

    def _vlm_props(self) -> list[dict[str, Any]]:
        return [
            {"property_name": "lattice_constant", "value": 5.48, "confidence": 0.95},
            {"property_name": "melting_point", "value": 3138, "confidence": 0.88},
        ]

    def test_no_conflicts_returns_both_lists_unchanged(self) -> None:
        text = [{"property_name": "a", "value": 1}]
        vlm = [{"property_name": "b", "value": 2}]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "prefer_vlm")
        assert final_text == [{"property_name": "a", "value": 1}]
        assert final_vlm == [{"property_name": "b", "value": 2}]

    def test_prefer_vlm_keeps_vlm_on_conflict(self) -> None:
        final_text, final_vlm = _apply_conflict_resolution(
            self._text_props(), self._vlm_props(), "prefer_vlm"
        )
        text_names = {p["property_name"] for p in final_text}
        assert "lattice_constant" not in text_names
        assert "bulk_modulus" in text_names
        assert "thermal_conductivity" in text_names
        assert len(final_vlm) == 2

    def test_prefer_text_keeps_text_on_conflict(self) -> None:
        final_text, final_vlm = _apply_conflict_resolution(
            self._text_props(), self._vlm_props(), "prefer_text"
        )
        vlm_names = {p["property_name"] for p in final_vlm}
        assert "lattice_constant" not in vlm_names
        assert "melting_point" in vlm_names
        assert len(final_text) == 3

    def test_merge_combines_both_deduplicates(self) -> None:
        """Merge strategy: combine both, deduplicate by overlap on property_name.

        For conflicts, VLM version is preferred (higher confidence source).
        Unique properties from both sides are included.
        """
        final_text, final_vlm = _apply_conflict_resolution(
            self._text_props(), self._vlm_props(), "merge"
        )
        combined_names = {p["property_name"] for p in final_text}
        assert "lattice_constant" in combined_names
        assert "bulk_modulus" in combined_names
        assert "thermal_conductivity" in combined_names
        assert "melting_point" in combined_names
        assert len(final_text) == 4

        assert len(final_vlm) == 1
        assert final_vlm[0]["property_name"] == "lattice_constant"

    def test_merge_no_conflicts(self) -> None:
        text = [{"property_name": "a", "value": 1}]
        vlm = [{"property_name": "b", "value": 2}]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "merge")
        assert len(final_text) == 2
        assert len(final_vlm) == 0

    def test_invalid_strategy_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            _apply_conflict_resolution([], [], "invalid_strategy")

    def test_empty_inputs(self) -> None:
        for strategy in ("prefer_vlm", "prefer_text", "merge"):
            final_text, final_vlm = _apply_conflict_resolution([], [], strategy)
            assert final_text == []
            assert final_vlm == []


# ---------------------------------------------------------------------------
# _extract_figures_from_source (stub mode)
# ---------------------------------------------------------------------------


class TestExtractFiguresStubMode:
    """Test figure extraction in stub mode."""

    def setup_method(self) -> None:
        os.environ["EXTRACTION_STUB_MODE"] = "true"

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    async def test_returns_stub_figures(self) -> None:
        results = await _extract_figures_from_source("test_source.pdf", None, 0.0)
        assert len(results) == 3
        assert all("figure_type" in r for r in results)

    @pytest.mark.asyncio
    async def test_filters_by_figure_types(self) -> None:
        results = await _extract_figures_from_source("test_source.pdf", ["line"], 0.0)
        assert all(r["figure_type"] == "line" for r in results)

    @pytest.mark.asyncio
    async def test_filters_by_confidence_threshold(self) -> None:
        results = await _extract_figures_from_source("test_source.pdf", None, 0.75)
        assert all(r["confidence"] >= 0.75 for r in results)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_no_results_when_threshold_too_high(self) -> None:
        results = await _extract_figures_from_source("test_source.pdf", None, 1.0)
        assert results == []


# ---------------------------------------------------------------------------
# _extract_tables_from_source (stub mode)
# ---------------------------------------------------------------------------


class TestExtractTablesStubMode:
    """Test table extraction in stub mode."""

    def setup_method(self) -> None:
        os.environ["EXTRACTION_STUB_MODE"] = "true"

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    async def test_returns_stub_tables(self) -> None:
        results = await _extract_tables_from_source("test_source.pdf", 0.0)
        assert len(results) == 2
        assert all("headers" in r for r in results)

    @pytest.mark.asyncio
    async def test_filters_by_confidence_threshold(self) -> None:
        results = await _extract_tables_from_source("test_source.pdf", 0.8)
        assert all(r["confidence"] >= 0.8 for r in results)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_no_results_when_threshold_too_high(self) -> None:
        results = await _extract_tables_from_source("test_source.pdf", 1.0)
        assert results == []


# ---------------------------------------------------------------------------
# _extract_figures_from_source (non-stub, VLM failure → fault tolerant)
# ---------------------------------------------------------------------------


class TestExtractFiguresFaultTolerance:
    """VLM failure should log warning and return empty list, never raise."""

    def setup_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=False)
    async def test_returns_empty_when_vlm_not_configured(self, mock_vlm: Any) -> None:
        results = await _extract_figures_from_source("test.pdf", None, 0.5)
        assert results == []

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=True)
    @patch("nfm_db.services.multimodal_extraction.Path")
    @patch("nfm_db.services.plot_extractor.extract_plot_data", new_callable=AsyncMock)
    async def test_returns_empty_when_vlm_raises(
        self, mock_extract: AsyncMock, mock_path: MagicMock, mock_vlm: Any
    ) -> None:
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.read_bytes.return_value = b"fake_png"
        mock_extract.side_effect = RuntimeError("VLM timeout")

        with (
            patch("nfm_db.services.ocr_fallback.OcrFallback") as mock_ocr_cls,
            patch(
                "nfm_db.services.ocr_fallback.ocr_fallback_plot_result",
                side_effect=RuntimeError("OCR also failed"),
            ),
        ):
            mock_ocr_cls.return_value.extract_text = AsyncMock(
                side_effect=RuntimeError("OCR failed")
            )
            results = await _extract_figures_from_source("test.png", None, 0.5)
            assert results == []


# ---------------------------------------------------------------------------
# _extract_tables_from_source (non-stub, VLM failure → fault tolerant)
# ---------------------------------------------------------------------------


class TestExtractTablesFaultTolerance:
    """VLM failure should log warning and return empty list, never raise."""

    def setup_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=False)
    async def test_returns_empty_when_vlm_not_configured(self, mock_vlm: Any) -> None:
        results = await _extract_tables_from_source("test.pdf", 0.5)
        assert results == []

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=True)
    @patch("nfm_db.services.multimodal_extraction.Path")
    @patch("nfm_db.services.table_extractor.extract_table_data", new_callable=AsyncMock)
    async def test_returns_empty_when_vlm_raises(
        self, mock_extract: AsyncMock, mock_path: MagicMock, mock_vlm: Any
    ) -> None:
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.read_bytes.return_value = b"fake_png"
        mock_extract.side_effect = RuntimeError("VLM timeout")

        with (
            patch("nfm_db.services.ocr_fallback.OcrFallback") as mock_ocr_cls,
            patch(
                "nfm_db.services.ocr_fallback.ocr_fallback_table_result",
                side_effect=RuntimeError("OCR also failed"),
            ),
        ):
            mock_ocr_cls.return_value.extract_text = AsyncMock(
                side_effect=RuntimeError("OCR failed")
            )
            results = await _extract_tables_from_source("test.png", 0.5)
            assert results == []


# ---------------------------------------------------------------------------
# run_multimodal_extraction orchestration
# ---------------------------------------------------------------------------


class TestRunMultimodalExtraction:
    """Test the orchestration function wires helpers into the pipeline."""

    def _make_job(self, **overrides: Any) -> ExtractionJob:
        defaults: dict[str, Any] = {
            "job_id": "j1",
            "source_reference": "test.md",
            "source_type": "markdown",
        }
        defaults.update(overrides)
        return ExtractionJob(**defaults)

    def setup_method(self) -> None:
        os.environ["EXTRACTION_STUB_MODE"] = "true"

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    async def test_skips_when_both_flags_false(self) -> None:
        job = self._make_job()
        await run_multimodal_extraction(job, [])
        assert job.figures == []
        assert job.tables == []

    @pytest.mark.asyncio
    async def test_extracts_figures_when_flag_set(self) -> None:
        job = self._make_job(extract_figures=True, confidence_threshold=0.0)
        await run_multimodal_extraction(job, [])
        assert len(job.figures) > 0
        assert job.tables == []

    @pytest.mark.asyncio
    async def test_extracts_tables_when_flag_set(self) -> None:
        job = self._make_job(extract_tables=True, confidence_threshold=0.0)
        await run_multimodal_extraction(job, [])
        assert len(job.tables) > 0
        assert job.figures == []

    @pytest.mark.asyncio
    async def test_extracts_both_when_both_flags_set(self) -> None:
        job = self._make_job(extract_figures=True, extract_tables=True, confidence_threshold=0.0)
        await run_multimodal_extraction(job, [])
        assert len(job.figures) > 0
        assert len(job.tables) > 0

    @pytest.mark.asyncio
    async def test_applies_conflict_resolution(self) -> None:
        job = self._make_job(
            extract_figures=True,
            confidence_threshold=0.0,
            conflict_strategy="prefer_vlm",
        )
        text_props = [
            {"property_name": "lattice_constant", "value": 5.47},
        ]
        await run_multimodal_extraction(job, text_props)
        assert len(job.figures) > 0

    @pytest.mark.asyncio
    async def test_is_fault_tolerant(self) -> None:
        """Orchestration catches exceptions and does not propagate."""
        job = self._make_job(extract_figures=True, confidence_threshold=0.0)
        with patch(
            "nfm_db.services.multimodal_extraction._extract_figures_from_source",
            new_callable=AsyncMock,
            side_effect=RuntimeError("catastrophic failure"),
        ):
            await run_multimodal_extraction(job, [])
        assert job.figures == []
        assert job.tables == []
