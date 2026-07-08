"""Tests for V4 multimodal extraction schema extensions (NFM-922).

Covers:
- V4ExtractionSubmitRequest multimodal fields
- V4ResultResponse figures/tables arrays
- V4FigureResult, V4TableResult, V4MultimodalSummary new schemas
- Backward compatibility (existing clients without multimodal options)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.schemas.extraction import (
    V4ExtractionSubmitRequest,
    V4FigureResult,
    V4MultimodalSummary,
    V4ResultResponse,
    V4TableResult,
)
from nfm_db.schemas.vision_extraction import (
    PlotData,
    TableData,
    VisionExtractionResult,
)


# ---------------------------------------------------------------------------
# V4ExtractionSubmitRequest — multimodal fields
# ---------------------------------------------------------------------------


class TestV4ExtractionSubmitMultimodal:
    """Tests for new multimodal fields on V4ExtractionSubmitRequest."""

    def test_extract_figures_defaults_false(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
        )
        assert req.extract_figures is False

    def test_extract_tables_defaults_false(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
        )
        assert req.extract_tables is False

    def test_extract_figures_can_be_enabled(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
            extract_figures=True,
        )
        assert req.extract_figures is True

    def test_extract_tables_can_be_enabled(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
            extract_tables=True,
        )
        assert req.extract_tables is True

    def test_figure_types_defaults_none(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
        )
        assert req.figure_types is None

    def test_figure_types_accepts_valid_list(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
            figure_types=["line", "scatter", "bar", "heatmap", "contour"],
        )
        assert req.figure_types == ["line", "scatter", "bar", "heatmap", "contour"]

    def test_confidence_threshold_defaults_0_5(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
        )
        assert req.confidence_threshold == 0.5

    def test_confidence_threshold_rejects_below_zero(self):
        with pytest.raises(ValidationError):
            V4ExtractionSubmitRequest(
                source_reference="10.1016/test",
                source_type="doi",
                confidence_threshold=-0.1,
            )

    def test_confidence_threshold_rejects_above_one(self):
        with pytest.raises(ValidationError):
            V4ExtractionSubmitRequest(
                source_reference="10.1016/test",
                source_type="doi",
                confidence_threshold=1.1,
            )

    def test_conflict_strategy_defaults_prefer_vlm(self):
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
        )
        assert req.conflict_strategy == "prefer_vlm"

    def test_conflict_strategy_accepts_all_values(self):
        for strategy in ("prefer_vlm", "prefer_text", "merge", "keep_both"):
            req = V4ExtractionSubmitRequest(
                source_reference="10.1016/test",
                source_type="doi",
                conflict_strategy=strategy,
            )
            assert req.conflict_strategy == strategy

    def test_backward_compat_minimal_payload(self):
        """Existing clients without multimodal options must still work."""
        req = V4ExtractionSubmitRequest(
            source_reference="10.1016/test",
            source_type="doi",
            element_systems=["U"],
            cache_level="L2",
            priority="high",
        )
        assert req.extract_figures is False
        assert req.extract_tables is False
        assert req.figure_types is None
        assert req.confidence_threshold == 0.5
        assert req.conflict_strategy == "prefer_vlm"
        assert req.element_systems == ["U"]
        assert req.priority == "high"


# ---------------------------------------------------------------------------
# V4ResultResponse — figures and tables arrays
# ---------------------------------------------------------------------------


class TestV4ResultResponseMultimodal:
    """Tests for figures/tables arrays on V4ResultResponse."""

    def test_figures_defaults_empty_list(self):
        resp = V4ResultResponse(
            source_reference="10.1016/test",
            job_status="completed",
            total_extracted=5,
        )
        assert resp.figures == []

    def test_tables_defaults_empty_list(self):
        resp = V4ResultResponse(
            source_reference="10.1016/test",
            job_status="completed",
            total_extracted=5,
        )
        assert resp.tables == []

    def test_figures_accepts_list(self):
        figure = V4FigureResult(
            page_number=3,
            source_file="doc.pdf",
            vision_result=VisionExtractionResult(figure_type="plot"),
        )
        resp = V4ResultResponse(
            source_reference="10.1016/test",
            job_status="completed",
            total_extracted=5,
            figures=[figure],
        )
        assert len(resp.figures) == 1

    def test_tables_accepts_list(self):
        table = V4TableResult(
            page_number=5,
            source_file="doc.pdf",
            table_data=TableData(title="Properties"),
        )
        resp = V4ResultResponse(
            source_reference="10.1016/test",
            job_status="completed",
            total_extracted=5,
            tables=[table],
        )
        assert len(resp.tables) == 1

    def test_backward_compat_without_figures_or_tables(self):
        """Existing response construction without figures/tables must work."""
        resp = V4ResultResponse(
            source_reference="10.1016/test",
            job_status="completed",
            total_extracted=5,
            properties=[],
        )
        assert resp.figures == []
        assert resp.tables == []


# ---------------------------------------------------------------------------
# V4FigureResult
# ---------------------------------------------------------------------------


class TestV4FigureResult:
    """Tests for V4FigureResult schema."""

    def test_constructs_with_required_fields(self):
        result = V4FigureResult(
            page_number=1,
            source_file="paper.pdf",
            vision_result=VisionExtractionResult(figure_type="plot"),
        )
        assert result.page_number == 1
        assert result.source_file == "paper.pdf"
        assert result.vision_result.figure_type == "plot"

    def test_constructs_with_plot_data(self):
        plot = PlotData(title="Thermal Conductivity", plot_type="line")
        vr = VisionExtractionResult(
            figure_type="plot",
            plot_data=plot,
        )
        result = V4FigureResult(
            page_number=2,
            source_file="paper.pdf",
            vision_result=vr,
        )
        assert result.vision_result.plot_data is not None
        assert result.vision_result.plot_data.title == "Thermal Conductivity"

    def test_page_number_required(self):
        with pytest.raises(ValidationError):
            V4FigureResult(
                source_file="paper.pdf",
                vision_result=VisionExtractionResult(figure_type="plot"),
            )

    def test_source_file_required(self):
        with pytest.raises(ValidationError):
            V4FigureResult(
                page_number=1,
                vision_result=VisionExtractionResult(figure_type="plot"),
            )


# ---------------------------------------------------------------------------
# V4TableResult
# ---------------------------------------------------------------------------


class TestV4TableResult:
    """Tests for V4TableResult schema."""

    def test_constructs_with_required_fields(self):
        table = TableData(title="Material Properties", num_columns=4, num_rows=10)
        result = V4TableResult(
            page_number=3,
            source_file="paper.pdf",
            table_data=table,
        )
        assert result.page_number == 3
        assert result.source_file == "paper.pdf"
        assert result.table_data.title == "Material Properties"

    def test_page_number_required(self):
        with pytest.raises(ValidationError):
            V4TableResult(
                source_file="paper.pdf",
                table_data=TableData(),
            )

    def test_source_file_required(self):
        with pytest.raises(ValidationError):
            V4TableResult(
                page_number=3,
                table_data=TableData(),
            )


# ---------------------------------------------------------------------------
# V4MultimodalSummary
# ---------------------------------------------------------------------------


class TestV4MultimodalSummary:
    """Tests for V4MultimodalSummary schema."""

    def test_constructs_with_defaults(self):
        summary = V4MultimodalSummary()
        assert summary.total_figures == 0
        assert summary.total_tables == 0
        assert summary.fallback_count == 0
        assert summary.avg_confidence == 0.0

    def test_constructs_with_values(self):
        summary = V4MultimodalSummary(
            total_figures=5,
            total_tables=3,
            fallback_count=1,
            avg_confidence=0.85,
        )
        assert summary.total_figures == 5
        assert summary.total_tables == 3
        assert summary.fallback_count == 1
        assert summary.avg_confidence == 0.85

    def test_avg_confidence_clamped(self):
        with pytest.raises(ValidationError):
            V4MultimodalSummary(avg_confidence=1.5)
