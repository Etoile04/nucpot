"""Tests for V4 extraction schema multimodal extensions (NFM-922).

Covers new multimodal fields on V4ExtractionSubmitRequest, V4ResultResponse
figures/tables arrays, and new V4FigureResult / V4TableResult /
V4MultimodalSummary schemas in nfm_db.schemas.extraction.
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
# V4ExtractionSubmitRequest — new multimodal fields
# ---------------------------------------------------------------------------


class TestV4ExtractionSubmitRequestMultimodalDefaults:
    """Multimodal fields default to off/standard values for backward compat."""

    def test_extract_figures_defaults_false(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
        )
        assert req.extract_figures is False

    def test_extract_tables_defaults_false(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
        )
        assert req.extract_tables is False

    def test_figure_types_defaults_none(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
        )
        assert req.figure_types is None

    def test_confidence_threshold_defaults_0_5(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
        )
        assert req.confidence_threshold == 0.5

    def test_conflict_strategy_defaults_prefer_vlm(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
        )
        assert req.conflict_strategy == "prefer_vlm"


class TestV4ExtractionSubmitRequestMultimodalOptIn:
    """Clients can explicitly enable multimodal extraction."""

    def test_enable_figures(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            extract_figures=True,
        )
        assert req.extract_figures is True

    def test_enable_tables(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            extract_tables=True,
        )
        assert req.extract_tables is True

    def test_enable_both(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            extract_figures=True,
            extract_tables=True,
        )
        assert req.extract_figures is True
        assert req.extract_tables is True

    def test_figure_types_filter(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            extract_figures=True,
            figure_types=["line", "scatter"],
        )
        assert req.figure_types == ["line", "scatter"]

    def test_custom_confidence_threshold(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            confidence_threshold=0.8,
        )
        assert req.confidence_threshold == 0.8

    def test_conflict_strategy_merge(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            conflict_strategy="merge",
        )
        assert req.conflict_strategy == "merge"

    def test_conflict_strategy_prefer_text(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            conflict_strategy="prefer_text",
        )
        assert req.conflict_strategy == "prefer_text"

    def test_conflict_strategy_keep_both(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            conflict_strategy="keep_both",
        )
        assert req.conflict_strategy == "keep_both"


class TestV4ExtractionSubmitRequestConstraints:
    """Validation constraints on multimodal fields."""

    def test_confidence_threshold_minimum(self) -> None:
        V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            confidence_threshold=0.0,
        )

    def test_confidence_threshold_maximum(self) -> None:
        V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
            confidence_threshold=1.0,
        )

    def test_confidence_threshold_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            V4ExtractionSubmitRequest(
                source_reference="10.1234/test",
                source_type="doi",
                confidence_threshold=-0.1,
            )

    def test_confidence_threshold_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            V4ExtractionSubmitRequest(
                source_reference="10.1234/test",
                source_type="doi",
                confidence_threshold=1.1,
            )


class TestV4ExtractionSubmitRequestBackwardCompat:
    """Existing clients without multimodal fields still work."""

    def test_minimal_request_still_valid(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="10.1234/test",
            source_type="doi",
        )
        assert req.source_reference == "10.1234/test"
        assert req.source_type == "doi"
        assert req.priority == "normal"

    def test_existing_fields_unchanged(self) -> None:
        req = V4ExtractionSubmitRequest(
            source_reference="/data/paper.pdf",
            source_type="file",
            element_systems=["U", "Pu"],
            cache_level="L2",
            max_confidence="high",
            priority="high",
        )
        assert req.element_systems == ["U", "Pu"]
        assert req.cache_level == "L2"
        assert req.max_confidence == "high"
        assert req.priority == "high"


# ---------------------------------------------------------------------------
# V4ResultResponse — figures and tables arrays
# ---------------------------------------------------------------------------


class TestV4ResultResponseFiguresTables:
    """V4ResultResponse includes figures[] and tables[] with backward compat."""

    def test_defaults_empty_lists(self) -> None:
        resp = V4ResultResponse(
            source_reference="10.1234/test",
            job_status="completed",
            total_extracted=5,
        )
        assert resp.figures == []
        assert resp.tables == []

    def test_with_figures(self) -> None:
        figure = V4FigureResult(
            page_number=3,
            source_file="paper.pdf",
            extraction=VisionExtractionResult(
                figure_type="plot",
                plot_data=PlotData(title="Phase Diagram", confidence=0.9),
            ),
        )
        resp = V4ResultResponse(
            source_reference="10.1234/test",
            job_status="completed",
            total_extracted=5,
            figures=[figure],
        )
        assert len(resp.figures) == 1
        assert resp.figures[0].page_number == 3

    def test_with_tables(self) -> None:
        table = V4TableResult(
            page_number=5,
            source_file="paper.pdf",
            table_data=TableData(title="Properties", num_rows=10),
        )
        resp = V4ResultResponse(
            source_reference="10.1234/test",
            job_status="completed",
            total_extracted=5,
            tables=[table],
        )
        assert len(resp.tables) == 1
        assert resp.tables[0].page_number == 5

    def test_backward_compat_properties_unchanged(self) -> None:
        resp = V4ResultResponse(
            source_reference="10.1234/test",
            job_status="completed",
            total_extracted=3,
        )
        assert resp.properties == []
        assert resp.total_extracted == 3
        assert resp.job_status == "completed"


# ---------------------------------------------------------------------------
# V4FigureResult
# ---------------------------------------------------------------------------


class TestV4FigureResult:
    """V4FigureResult wraps VisionExtractionResult with page/source context."""

    def test_minimal(self) -> None:
        fig = V4FigureResult(
            page_number=1,
            source_file="test.pdf",
            extraction=VisionExtractionResult(figure_type="plot"),
        )
        assert fig.page_number == 1
        assert fig.source_file == "test.pdf"
        assert fig.extraction.figure_type == "plot"

    def test_with_full_extraction(self) -> None:
        fig = V4FigureResult(
            page_number=7,
            source_file="paper.pdf",
            extraction=VisionExtractionResult(
                figure_type="plot",
                plot_data=PlotData(
                    title="Conductivity vs Temp",
                    plot_type="line",
                    confidence=0.85,
                ),
                provider="anthropic",
                model="claude-3",
                extraction_time_ms=120.0,
                fallback_used=False,
            ),
        )
        assert fig.page_number == 7
        assert fig.extraction.plot_data is not None
        assert fig.extraction.plot_data.confidence == 0.85

    def test_serialization_round_trip(self) -> None:
        fig = V4FigureResult(
            page_number=2,
            source_file="doc.pdf",
            extraction=VisionExtractionResult(
                figure_type="plot",
                plot_data=PlotData(title="T", confidence=0.7),
            ),
        )
        data = fig.model_dump(mode="json")
        restored = V4FigureResult.model_validate(data)
        assert restored.page_number == 2
        assert restored.source_file == "doc.pdf"
        assert restored.extraction.figure_type == "plot"

    def test_page_number_non_negative(self) -> None:
        V4FigureResult(
            page_number=0,
            source_file="test.pdf",
            extraction=VisionExtractionResult(figure_type="plot"),
        )

    def test_page_number_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            V4FigureResult(
                page_number=-1,
                source_file="test.pdf",
                extraction=VisionExtractionResult(figure_type="plot"),
            )


# ---------------------------------------------------------------------------
# V4TableResult
# ---------------------------------------------------------------------------


class TestV4TableResult:
    """V4TableResult wraps TableData with page/source context."""

    def test_minimal(self) -> None:
        tbl = V4TableResult(
            page_number=1,
            source_file="test.pdf",
            table_data=TableData(),
        )
        assert tbl.page_number == 1
        assert tbl.source_file == "test.pdf"
        assert isinstance(tbl.table_data, TableData)

    def test_with_full_table_data(self) -> None:
        tbl = V4TableResult(
            page_number=4,
            source_file="paper.pdf",
            table_data=TableData(
                title="Measured Values",
                num_columns=5,
                num_rows=20,
                confidence=0.92,
            ),
        )
        assert tbl.page_number == 4
        assert tbl.table_data.num_rows == 20
        assert tbl.table_data.confidence == 0.92

    def test_serialization_round_trip(self) -> None:
        tbl = V4TableResult(
            page_number=3,
            source_file="doc.pdf",
            table_data=TableData(title="Summary", num_columns=2, num_rows=5),
        )
        data = tbl.model_dump(mode="json")
        restored = V4TableResult.model_validate(data)
        assert restored.page_number == 3
        assert restored.table_data.title == "Summary"

    def test_page_number_non_negative(self) -> None:
        V4TableResult(
            page_number=0,
            source_file="test.pdf",
            table_data=TableData(),
        )

    def test_page_number_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            V4TableResult(
                page_number=-1,
                source_file="test.pdf",
                table_data=TableData(),
            )


# ---------------------------------------------------------------------------
# V4MultimodalSummary
# ---------------------------------------------------------------------------


class TestV4MultimodalSummary:
    """Aggregate statistics for multimodal extraction results."""

    def test_defaults(self) -> None:
        summary = V4MultimodalSummary()
        assert summary.total_figures == 0
        assert summary.total_tables == 0
        assert summary.fallback_count == 0
        assert summary.avg_confidence == 0.0

    def test_with_values(self) -> None:
        summary = V4MultimodalSummary(
            total_figures=5,
            total_tables=3,
            fallback_count=1,
            avg_confidence=0.78,
        )
        assert summary.total_figures == 5
        assert summary.total_tables == 3
        assert summary.fallback_count == 1
        assert summary.avg_confidence == 0.78

    def test_avg_confidence_bounds(self) -> None:
        V4MultimodalSummary(avg_confidence=0.0)
        V4MultimodalSummary(avg_confidence=1.0)
        with pytest.raises(ValidationError):
            V4MultimodalSummary(avg_confidence=-0.01)
        with pytest.raises(ValidationError):
            V4MultimodalSummary(avg_confidence=1.01)

    def test_counts_non_negative(self) -> None:
        V4MultimodalSummary(total_figures=0, total_tables=0, fallback_count=0)

    def test_serialization_round_trip(self) -> None:
        summary = V4MultimodalSummary(
            total_figures=2,
            total_tables=1,
            fallback_count=0,
            avg_confidence=0.85,
        )
        data = summary.model_dump(mode="json")
        restored = V4MultimodalSummary.model_validate(data)
        assert restored.total_figures == 2
        assert restored.avg_confidence == 0.85
