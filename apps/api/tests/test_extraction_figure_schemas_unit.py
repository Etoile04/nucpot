"""Unit tests for nfm_db.schemas.extraction_figure — Pydantic schema validation."""

import uuid
from datetime import datetime, timezone

import pytest

from nfm_db.schemas.extraction_figure import (
    ExtractionFigureCreate,
    ExtractionFigureListResponse,
    ExtractionFigureResponse,
)


class TestExtractionFigureCreate:
    def test_minimal(self) -> None:
        fig = ExtractionFigureCreate(page_number=1, figure_type="plot")
        assert fig.page_number == 1
        assert fig.figure_type == "plot"
        assert fig.source_id is None
        assert fig.bounding_box is None
        assert fig.caption is None
        assert fig.image_path is None
        assert fig.extracted_data == {}
        assert fig.confidence == 0.0
        assert fig.extraction_method is None

    def test_full(self) -> None:
        sid = uuid.uuid4()
        fig = ExtractionFigureCreate(
            source_id=sid,
            page_number=5,
            figure_type="table",
            bounding_box={"x": 10, "y": 20, "width": 300, "height": 200},
            caption="Figure 1: Thermal conductivity",
            image_path="/figures/fig1.png",
            extracted_data={"values": [1.0, 2.0]},
            confidence=0.95,
            extraction_method="vlm",
        )
        assert fig.source_id == sid
        assert fig.confidence == 0.95
        assert fig.extracted_data == {"values": [1.0, 2.0]}

    def test_invalid_page_number_zero(self) -> None:
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=0, figure_type="plot")

    def test_invalid_confidence_above_one(self) -> None:
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=1, figure_type="plot", confidence=1.5)

    def test_invalid_confidence_negative(self) -> None:
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=1, figure_type="plot", confidence=-0.1)

    def test_caption_max_length(self) -> None:
        long_caption = "x" * 5001
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=1, figure_type="plot", caption=long_caption)

    def test_figure_type_max_length(self) -> None:
        long_type = "a" * 51
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=1, figure_type=long_type)

    def test_extraction_method_max_length(self) -> None:
        long_method = "a" * 51
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=1, figure_type="plot", extraction_method=long_method)

    def test_image_path_max_length(self) -> None:
        long_path = "a" * 501
        with pytest.raises(Exception):
            ExtractionFigureCreate(page_number=1, figure_type="plot", image_path=long_path)


class TestExtractionFigureResponse:
    def test_full_construction(self) -> None:
        fid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        resp = ExtractionFigureResponse(
            id=fid,
            source_id=sid,
            page_number=1,
            figure_type="plot",
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 100},
            caption="Test",
            image_path="/img.png",
            extracted_data={"k": "v"},
            confidence=0.8,
            extraction_method="ocr",
            created_at=now,
            updated_at=now,
        )
        assert resp.id == fid
        assert resp.confidence == 0.8

    def test_defaults(self) -> None:
        resp = ExtractionFigureResponse(
            id=uuid.uuid4(),
            page_number=1,
            figure_type="diagram",
            confidence=0.5,
        )
        assert resp.source_id is None
        assert resp.bounding_box is None
        assert resp.caption is None
        assert resp.extracted_data == {}
        assert resp.created_at is None


class TestExtractionFigureListResponse:
    def test_defaults(self) -> None:
        resp = ExtractionFigureListResponse()
        assert resp.figures == []
        assert resp.total == 0
        assert resp.page == 1
        assert resp.page_size == 20

    def test_with_figures(self) -> None:
        fig = ExtractionFigureResponse(
            id=uuid.uuid4(),
            page_number=1,
            figure_type="plot",
            confidence=0.9,
        )
        resp = ExtractionFigureListResponse(
            figures=[fig],
            total=1,
            page=1,
            page_size=10,
        )
        assert len(resp.figures) == 1
        assert resp.total == 1