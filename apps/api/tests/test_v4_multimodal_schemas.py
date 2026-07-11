"""Tests for VLM-based extraction schemas (NFM-851, NFM-853.4).

Covers Pydantic models in nfm_db.schemas.vision_extraction:
- AxisInfo, SeriesData, PlotData (B1.2 — plot/chart extraction)
- TableCell, TableHeader, TableData (B1.3 — table extraction)
- VisionExtractionResult (unified wrapper)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.schemas.vision_extraction import (
    AxisInfo,
    PlotData,
    SeriesData,
    TableCell,
    TableData,
    TableHeader,
    VisionExtractionResult,
)

# ---------------------------------------------------------------------------
# AxisInfo
# ---------------------------------------------------------------------------


class TestAxisInfo:
    """Tests for AxisInfo schema."""

    def test_default_values(self) -> None:
        axis = AxisInfo()
        assert axis.label == ""
        assert axis.unit == ""
        assert axis.values == []
        assert axis.scale == "linear"

    def test_custom_values(self) -> None:
        axis = AxisInfo(label="Temperature", unit="K", values=[300, 400, 500])
        assert axis.label == "Temperature"
        assert axis.unit == "K"
        assert axis.values == [300, 400, 500]

    def test_log_scale(self) -> None:
        axis = AxisInfo(scale="log")
        assert axis.scale == "log"

    def test_values_accepts_floats(self) -> None:
        axis = AxisInfo(values=[0.1, 1.0, 10.0])
        assert axis.values == [0.1, 1.0, 10.0]


# ---------------------------------------------------------------------------
# SeriesData
# ---------------------------------------------------------------------------


class TestSeriesData:
    """Tests for SeriesData schema."""

    def test_default_values(self) -> None:
        series = SeriesData()
        assert series.name == ""
        assert series.values == []
        assert series.color == ""
        assert series.marker_style == ""

    def test_custom_values(self) -> None:
        series = SeriesData(
            name="UO2",
            values=[10.5, 11.2, 12.0],
            color="blue",
            marker_style="circle",
        )
        assert series.name == "UO2"
        assert len(series.values) == 3
        assert series.marker_style == "circle"


# ---------------------------------------------------------------------------
# PlotData
# ---------------------------------------------------------------------------


class TestPlotData:
    """Tests for PlotData schema (B1.2)."""

    def test_default_values(self) -> None:
        plot = PlotData()
        assert plot.title == ""
        assert plot.plot_type == "unknown"
        assert plot.series == []
        assert plot.legend_entries == []
        assert plot.annotations == []
        assert plot.confidence == 0.0
        assert plot.raw_response is None

    def test_custom_plot(self) -> None:
        plot = PlotData(
            title="Conductivity vs Temperature",
            plot_type="line",
            confidence=0.85,
        )
        assert plot.title == "Conductivity vs Temperature"
        assert plot.plot_type == "line"
        assert plot.confidence == 0.85

    def test_axes_default_factory(self) -> None:
        plot = PlotData()
        assert isinstance(plot.x_axis, AxisInfo)
        assert isinstance(plot.y_axis, AxisInfo)
        assert plot.y2_axis is None

    def test_y2_axis_optional(self) -> None:
        plot = PlotData(y2_axis=AxisInfo(label="Stress", unit="MPa"))
        assert plot.y2_axis is not None
        assert plot.y2_axis.label == "Stress"

    def test_series_list(self) -> None:
        plot = PlotData(
            series=[
                SeriesData(name="Series A", values=[1.0, 2.0]),
                SeriesData(name="Series B", values=[3.0, 4.0]),
            ],
        )
        assert len(plot.series) == 2

    def test_confidence_bounds(self) -> None:
        PlotData(confidence=0.0)
        PlotData(confidence=1.0)
        with pytest.raises(ValidationError):
            PlotData(confidence=-0.1)
        with pytest.raises(ValidationError):
            PlotData(confidence=1.1)

    def test_raw_response_dict(self) -> None:
        plot = PlotData(raw_response={"key": "value"})
        assert plot.raw_response == {"key": "value"}


# ---------------------------------------------------------------------------
# TableCell
# ---------------------------------------------------------------------------


class TestTableCell:
    """Tests for TableCell schema."""

    def test_default_values(self) -> None:
        cell = TableCell()
        assert cell.value == ""
        assert cell.row_span == 1
        assert cell.col_span == 1
        assert cell.is_header is False
        assert cell.confidence == 1.0

    def test_header_cell(self) -> None:
        cell = TableCell(value="Material", is_header=True)
        assert cell.value == "Material"
        assert cell.is_header is True

    def test_span_constraints(self) -> None:
        cell = TableCell(row_span=2, col_span=3)
        assert cell.row_span == 2
        assert cell.col_span == 3

    def test_span_minimum(self) -> None:
        with pytest.raises(ValidationError):
            TableCell(row_span=0)
        with pytest.raises(ValidationError):
            TableCell(col_span=0)

    def test_confidence_bounds(self) -> None:
        TableCell(confidence=0.0)
        TableCell(confidence=1.0)
        with pytest.raises(ValidationError):
            TableCell(confidence=1.5)


# ---------------------------------------------------------------------------
# TableHeader
# ---------------------------------------------------------------------------


class TestTableHeader:
    """Tests for TableHeader schema."""

    def test_default_values(self) -> None:
        header = TableHeader()
        assert header.columns == []
        assert header.sub_headers is None

    def test_columns(self) -> None:
        header = TableHeader(columns=["Material", "Property", "Value"])
        assert len(header.columns) == 3

    def test_sub_headers(self) -> None:
        header = TableHeader(
            columns=["Group A", "Group A", "Group B"],
            sub_headers=["Sub 1", "Sub 2", "Sub 3"],
        )
        assert header.sub_headers == ["Sub 1", "Sub 2", "Sub 3"]


# ---------------------------------------------------------------------------
# TableData
# ---------------------------------------------------------------------------


class TestTableData:
    """Tests for TableData schema (B1.3)."""

    def test_default_values(self) -> None:
        table = TableData()
        assert table.title == ""
        assert isinstance(table.headers, TableHeader)
        assert table.rows == []
        assert table.num_columns == 0
        assert table.num_rows == 0
        assert table.has_merged_cells is False
        assert table.notes == []
        assert table.confidence == 0.0

    def test_custom_table(self) -> None:
        table = TableData(
            title="Measured Properties",
            num_columns=4,
            num_rows=5,
            confidence=0.9,
        )
        assert table.title == "Measured Properties"
        assert table.num_columns == 4
        assert table.num_rows == 5

    def test_rows_with_cells(self) -> None:
        row = [TableCell(value="UO2"), TableCell(value="density")]
        table = TableData(rows=[row])
        assert len(table.rows) == 1
        assert len(table.rows[0]) == 2

    def test_confidence_bounds(self) -> None:
        TableData(confidence=0.0)
        TableData(confidence=1.0)
        with pytest.raises(ValidationError):
            TableData(confidence=-0.1)

    def test_notes_list(self) -> None:
        table = TableData(notes=["* Estimated value"])
        assert table.notes == ["* Estimated value"]


# ---------------------------------------------------------------------------
# VisionExtractionResult
# ---------------------------------------------------------------------------


class TestVisionExtractionResult:
    """Tests for the unified VisionExtractionResult wrapper."""

    def test_plot_result(self) -> None:
        result = VisionExtractionResult(
            figure_type="plot",
            plot_data=PlotData(title="Test Plot"),
        )
        assert result.figure_type == "plot"
        assert result.plot_data is not None
        assert result.plot_data.title == "Test Plot"
        assert result.table_data is None

    def test_table_result(self) -> None:
        result = VisionExtractionResult(
            figure_type="table",
            table_data=TableData(title="Test Table"),
        )
        assert result.figure_type == "table"
        assert result.table_data is not None
        assert result.plot_data is None

    def test_defaults(self) -> None:
        result = VisionExtractionResult(figure_type="plot")
        assert result.source_image_path is None
        assert result.provider == "openai"
        assert result.model == ""
        assert result.extraction_time_ms == 0.0
        assert result.fallback_used is False

    def test_custom_provider(self) -> None:
        result = VisionExtractionResult(
            figure_type="plot",
            provider="anthropic",
            model="claude-3",
            extraction_time_ms=150.0,
        )
        assert result.provider == "anthropic"
        assert result.model == "claude-3"
        assert result.extraction_time_ms == 150.0

    def test_fallback_used(self) -> None:
        result = VisionExtractionResult(
            figure_type="plot",
            fallback_used=True,
        )
        assert result.fallback_used is True

    def test_extraction_time_minimum(self) -> None:
        VisionExtractionResult(figure_type="plot", extraction_time_ms=0.0)
        with pytest.raises(ValidationError):
            VisionExtractionResult(figure_type="plot", extraction_time_ms=-1.0)

    def test_serialization_round_trip(self) -> None:
        result = VisionExtractionResult(
            figure_type="plot",
            plot_data=PlotData(
                title="Phase Diagram",
                plot_type="contour",
                confidence=0.75,
            ),
            provider="openai",
            model="gpt-4o",
            extraction_time_ms=200.0,
        )
        data = result.model_dump(mode="json")
        restored = VisionExtractionResult.model_validate(data)
        assert restored.figure_type == "plot"
        assert restored.plot_data is not None
        assert restored.plot_data.title == "Phase Diagram"
        assert restored.plot_data.confidence == 0.75

    def test_serialization_with_table(self) -> None:
        result = VisionExtractionResult(
            figure_type="table",
            table_data=TableData(
                title="Properties Summary",
                headers=TableHeader(columns=["Material", "Value"]),
                num_rows=3,
            ),
        )
        data = result.model_dump(mode="json")
        restored = VisionExtractionResult.model_validate(data)
        assert restored.figure_type == "table"
        assert restored.table_data is not None
        assert restored.table_data.num_rows == 3
