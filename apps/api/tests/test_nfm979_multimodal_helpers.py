"""Tests for NFM-979: ExtractionJob multimodal fields and extraction helpers.

TDD: RED phase — tests written before implementation.
Covers:
- ExtractionJob has 7 new multimodal fields with correct defaults
- _extract_figures_from_source integrates with vision_client/plot_extractor
- _extract_tables_from_source integrates with table_extractor
- _apply_conflict_resolution implements prefer_vlm, prefer_text, merge
- All helpers are fault-tolerant (never raise on VLM failure)
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
)


# ---------------------------------------------------------------------------
# ExtractionJob field tests
# ---------------------------------------------------------------------------


class TestExtractionJobMultimodalFields:
    """Verify ExtractionJob has all 7 new multimodal fields with correct defaults."""

    def test_default_extract_figures_is_false(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.extract_figures is False

    def test_default_extract_tables_is_false(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.extract_tables is False

    def test_default_figure_types_is_none(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.figure_types is None

    def test_default_confidence_threshold_is_0_5(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.confidence_threshold == 0.5

    def test_default_conflict_strategy_is_prefer_vlm(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.conflict_strategy == "prefer_vlm"

    def test_default_figures_is_empty_list(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.figures == []

    def test_default_tables_is_empty_list(self) -> None:
        job = ExtractionJob(job_id="t1", source_reference="src.md", source_type="markdown")
        assert job.tables == []

    def test_custom_values_accepted(self) -> None:
        job = ExtractionJob(
            job_id="t2",
            source_reference="src.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=True,
            figure_types=["line", "scatter"],
            confidence_threshold=0.8,
            conflict_strategy="merge",
            figures=[{"type": "line"}],
            tables=[{"page": 1}],
        )
        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["line", "scatter"]
        assert job.confidence_threshold == 0.8
        assert job.conflict_strategy == "merge"
        assert job.figures == [{"type": "line"}]
        assert job.tables == [{"page": 1}]

    def test_existing_fields_unaffected(self) -> None:
        job = ExtractionJob(
            job_id="t3",
            source_reference="src.md",
            source_type="markdown",
            element_systems=["UO2"],
            cache_level="L1",
            max_confidence="high",
        )
        assert job.job_id == "t3"
        assert job.element_systems == ["UO2"]
        assert job.cache_level == "L1"
        assert job.max_confidence == "high"


# ---------------------------------------------------------------------------
# _extract_figures_from_source tests (stub mode)
# ---------------------------------------------------------------------------


class TestExtractFiguresFromSource:
    """Verify figure extraction via VLM with OCR fallback."""

    def setup_method(self) -> None:
        os.environ["EXTRACTION_STUB_MODE"] = "true"

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    async def test_stub_mode_returns_stub_figures(self) -> None:
        """In stub mode, returns pre-canned figure results."""
        results = await _extract_figures_from_source("paper.md", None, 0.0)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "figure_type" in r
            assert "confidence" in r

    @pytest.mark.asyncio
    async def test_figure_types_filter(self) -> None:
        """When figure_types is provided, only matching types are returned."""
        results = await _extract_figures_from_source(
            "paper.md", figure_types=["line"], threshold=0.0
        )
        for r in results:
            assert r["figure_type"] == "line"

    @pytest.mark.asyncio
    async def test_confidence_threshold_filter(self) -> None:
        """Results below threshold are filtered out."""
        results = await _extract_figures_from_source(
            "paper.md", figure_types=None, threshold=0.9
        )
        for r in results:
            assert r["confidence"] >= 0.9

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=False)
    async def test_vlm_not_configured_returns_empty(self, mock_vlm: Any) -> None:
        """When VLM is not configured, returns empty list (not error)."""
        os.environ.pop("EXTRACTION_STUB_MODE", None)
        results = await _extract_figures_from_source("paper.md", None, 0.5)
        assert results == []

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=True)
    @patch("nfm_db.services.multimodal_extraction.Path")
    @patch("nfm_db.services.plot_extractor.extract_plot_data", new_callable=AsyncMock)
    async def test_vlm_error_returns_empty(
        self, mock_extract: AsyncMock, mock_path: MagicMock, mock_vlm: Any
    ) -> None:
        """When VLM raises, logs warning and returns empty list."""
        os.environ.pop("EXTRACTION_STUB_MODE", None)
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.read_bytes.return_value = b"fake_png"
        mock_extract.side_effect = RuntimeError("VLM timeout")

        with patch(
            "nfm_db.services.ocr_fallback.OcrFallback"
        ) as mock_ocr_cls, patch(
            "nfm_db.services.ocr_fallback.ocr_fallback_plot_result",
            side_effect=RuntimeError("OCR also failed"),
        ):
            mock_ocr_cls.return_value.extract_text = AsyncMock(
                side_effect=RuntimeError("OCR failed")
            )
            results = await _extract_figures_from_source("paper.md", None, 0.5)
            assert results == []


# ---------------------------------------------------------------------------
# _extract_tables_from_source tests (stub mode)
# ---------------------------------------------------------------------------


class TestExtractTablesFromSource:
    """Verify table extraction integrates with table_extractor and is fault-tolerant."""

    def setup_method(self) -> None:
        os.environ["EXTRACTION_STUB_MODE"] = "true"

    def teardown_method(self) -> None:
        os.environ.pop("EXTRACTION_STUB_MODE", None)

    @pytest.mark.asyncio
    async def test_stub_mode_returns_stub_tables(self) -> None:
        """In stub mode, returns pre-canned table results."""
        results = await _extract_tables_from_source("paper.md", 0.0)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "headers" in r
            assert "confidence" in r

    @pytest.mark.asyncio
    async def test_confidence_threshold_filter(self) -> None:
        """Results below threshold are filtered out."""
        results = await _extract_tables_from_source("paper.md", threshold=0.9)
        for r in results:
            assert r["confidence"] >= 0.9

    @pytest.mark.asyncio
    @patch("nfm_db.services.vision_client.is_vlm_configured", return_value=False)
    async def test_vlm_not_configured_returns_empty(self, mock_vlm: Any) -> None:
        """When VLM is not configured, returns empty list (not error)."""
        os.environ.pop("EXTRACTION_STUB_MODE", None)
        results = await _extract_tables_from_source("paper.md", 0.5)
        assert results == []


# ---------------------------------------------------------------------------
# _apply_conflict_resolution tests
# ---------------------------------------------------------------------------


class TestApplyConflictResolution:
    """Verify conflict resolution strategies."""

    def _make_prop(self, name: str, value: float, source: str = "text") -> dict[str, Any]:
        return {"property_name": name, "value": value, "source": source}

    def test_prefer_vlm_vlm_wins(self) -> None:
        """VLM results take precedence on conflict."""
        text = [self._make_prop("density", 10.5, "text")]
        vlm = [self._make_prop("density", 11.0, "vlm")]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "prefer_vlm")
        assert len(final_vlm) == 1
        assert final_vlm[0]["value"] == 11.0
        assert len(final_text) == 0

    def test_prefer_vlm_text_fills_gaps(self) -> None:
        """VLM wins conflicts; text-only props pass through."""
        text = [
            self._make_prop("density", 10.5, "text"),
            self._make_prop("conductivity", 7.0, "text"),
        ]
        vlm = [self._make_prop("density", 11.0, "vlm")]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "prefer_vlm")
        text_names = {p["property_name"] for p in final_text}
        assert "density" not in text_names
        assert "conductivity" in text_names

    def test_prefer_text_text_wins(self) -> None:
        """Text extraction takes precedence; VLM fills gaps."""
        text = [self._make_prop("density", 10.5, "text")]
        vlm = [self._make_prop("density", 11.0, "vlm")]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "prefer_text")
        assert len(final_vlm) == 0
        assert len(final_text) == 1
        assert final_text[0]["value"] == 10.5

    def test_prefer_text_vlm_fills_gaps(self) -> None:
        """Text takes precedence; VLM fills gaps."""
        text = [self._make_prop("density", 10.5, "text")]
        vlm = [
            self._make_prop("density", 11.0, "vlm"),
            self._make_prop("conductivity", 8.0, "vlm"),
        ]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "prefer_text")
        vlm_names = {p["property_name"] for p in final_vlm}
        assert "density" not in vlm_names
        assert "conductivity" in vlm_names

    def test_merge_combines_and_deduplicates(self) -> None:
        """Merge combines both, deduplicates overlapping properties."""
        text = [self._make_prop("density", 10.5, "text")]
        vlm = [
            self._make_prop("density", 10.5, "vlm"),
            self._make_prop("conductivity", 8.0, "vlm"),
        ]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "merge")
        names = {p["property_name"] for p in final_text}
        assert "conductivity" in names
        assert "density" in names

    def test_merge_records_conflicts(self) -> None:
        """Merge keeps conflicting VLM items in final_vlm for review."""
        text = [self._make_prop("density", 10.5, "text")]
        vlm = [self._make_prop("density", 11.0, "vlm")]
        final_text, final_vlm = _apply_conflict_resolution(text, vlm, "merge")
        assert len(final_vlm) == 1
        assert final_vlm[0]["property_name"] == "density"

    def test_empty_inputs(self) -> None:
        """Both empty returns empty with no conflicts."""
        final_text, final_vlm = _apply_conflict_resolution([], [], "prefer_vlm")
        assert final_text == []
        assert final_vlm == []

    def test_unknown_strategy_raises_value_error(self) -> None:
        """Unknown strategy raises ValueError."""
        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            _apply_conflict_resolution([], [], "unknown_strategy")
