"""Tests for figure detection pipeline service (NFM-850).

Covers: VLM detection parsing, bbox parsing, confidence filtering,
area filtering, NMS deduplication, IoU computation, clamping,
and full pipeline integration with mocked VLM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from nfm_db.schemas.figure import (
    BoundingBox,
    DetectedFigure,
    FigureDetectionResult,
    FigureType,
    PageDetectionResult,
)
from nfm_db.services.figure_detector import (
    FigureDetector,
    FigureDetectorError,
    _build_detection_prompt,
    _clamp_bbox,
    _compute_iou,
    _parse_bounding_box,
)
from nfm_db.services.page_splitter import PageImage, PageSplitter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_page_image(
    width: int = 200,
    height: int = 300,
    page_index: int = 0,
) -> PageImage:
    """Create a PageImage with a blank PIL Image."""
    image = Image.new("RGB", (width, height), color="white")
    return PageImage(
        index=page_index,
        image=image,
        width=width,
        height=height,
    )


@pytest.fixture()
def sample_page() -> PageImage:
    """A sample 200x300 page image."""
    return _make_page_image()


@pytest.fixture()
def mock_vision_client() -> AsyncMock:
    """A mock VisionClient that returns two detected figures."""
    client = AsyncMock()
    client.extract = AsyncMock(
        return_value={
            "figures": [
                {
                    "type": "plot",
                    "bbox": [10, 20, 100, 80],
                    "confidence": 0.9,
                    "caption": "Fig 1. Stress curve",
                },
                {
                    "type": "table",
                    "bbox": [10, 150, 120, 60],
                    "confidence": 0.8,
                    "caption": "Table 1. Properties",
                },
            ],
        }
    )
    return client


@pytest.fixture()
def mock_splitter() -> MagicMock:
    """A mock PageSplitter returning one page."""
    page = _make_page_image()
    splitter = MagicMock(spec=PageSplitter)
    splitter.split = MagicMock(return_value=[page])
    return splitter


# ---------------------------------------------------------------------------
# Tests: Detection prompt
# ---------------------------------------------------------------------------


class TestBuildDetectionPrompt:
    """Tests for the VLM detection prompt builder."""

    def test_returns_non_empty_string(self) -> None:
        """Should return a non-empty prompt string."""
        prompt = _build_detection_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    def test_mentions_figure_types(self) -> None:
        """Should mention all four figure types."""
        prompt = _build_detection_prompt()
        assert "plot" in prompt
        assert "table" in prompt
        assert "microstructure" in prompt
        assert "diagram" in prompt

    def test_requests_json_output(self) -> None:
        """Should request JSON output format."""
        prompt = _build_detection_prompt()
        assert "JSON" in prompt


# ---------------------------------------------------------------------------
# Tests: Bounding box parsing
# ---------------------------------------------------------------------------


class TestParseBoundingBox:
    """Tests for _parse_bounding_box."""

    def test_parse_list(self) -> None:
        """Should parse a list [x, y, w, h]."""
        bbox = _parse_bounding_box([10, 20, 100, 80])
        assert bbox.x == 10
        assert bbox.y == 20
        assert bbox.width == 100
        assert bbox.height == 80

    def test_parse_dict(self) -> None:
        """Should parse a dict with x, y, width, height keys."""
        bbox = _parse_bounding_box({"x": 5, "y": 10, "width": 50, "height": 60})
        assert bbox.x == 5
        assert bbox.y == 10
        assert bbox.width == 50
        assert bbox.height == 60

    def test_negative_values_clamped(self) -> None:
        """Should clamp negative values to minimums."""
        bbox = _parse_bounding_box([-5, -10, 0, 0])
        assert bbox.x == 0
        assert bbox.y == 0
        assert bbox.width == 1
        assert bbox.height == 1

    def test_invalid_input_falls_back(self) -> None:
        """Should return default box for invalid input."""
        bbox = _parse_bounding_box("invalid")
        assert bbox.x == 0
        assert bbox.y == 0
        assert bbox.width == 1
        assert bbox.height == 1

    def test_empty_list_falls_back(self) -> None:
        """Should return default box for empty list."""
        bbox = _parse_bounding_box([])
        assert bbox.width == 1


# ---------------------------------------------------------------------------
# Tests: IoU computation
# ---------------------------------------------------------------------------


class TestComputeIoU:
    """Tests for _compute_iou."""

    def test_no_overlap(self) -> None:
        """Should return 0 for non-overlapping boxes."""
        a = BoundingBox(x=0, y=0, width=10, height=10)
        b = BoundingBox(x=20, y=20, width=10, height=10)
        assert _compute_iou(a, b) == 0.0

    def test_identical_boxes(self) -> None:
        """Should return 1.0 for identical boxes."""
        bbox = BoundingBox(x=0, y=0, width=10, height=10)
        assert _compute_iou(bbox, bbox) == 1.0

    def test_partial_overlap(self) -> None:
        """Should compute IoU correctly for partial overlap."""
        a = BoundingBox(x=0, y=0, width=10, height=10)
        b = BoundingBox(x=5, y=5, width=10, height=10)
        iou = _compute_iou(a, b)
        assert 0.0 < iou < 1.0

    def test_contained_box(self) -> None:
        """Should handle one box containing another."""
        a = BoundingBox(x=0, y=0, width=100, height=100)
        b = BoundingBox(x=10, y=10, width=20, height=20)
        iou = _compute_iou(a, b)
        assert 0.0 < iou < 1.0

    def test_touching_edges(self) -> None:
        """Should return 0 for boxes that only touch edges."""
        a = BoundingBox(x=0, y=0, width=10, height=10)
        b = BoundingBox(x=10, y=0, width=10, height=10)
        assert _compute_iou(a, b) == 0.0


# ---------------------------------------------------------------------------
# Tests: Bounding box clamping
# ---------------------------------------------------------------------------


class TestClampBbox:
    """Tests for _clamp_bbox."""

    def test_within_bounds_unchanged(self) -> None:
        """Should leave box unchanged when within bounds."""
        bbox = BoundingBox(x=10, y=10, width=50, height=60)
        clamped = _clamp_bbox(bbox, 200, 300)
        assert clamped.x == 10
        assert clamped.y == 10
        assert clamped.width == 50
        assert clamped.height == 60

    def test_exceeds_width_clamped(self) -> None:
        """Should clamp box that exceeds page width."""
        bbox = BoundingBox(x=150, y=10, width=100, height=50)
        clamped = _clamp_bbox(bbox, 200, 300)
        assert clamped.width == 50  # 200 - 150

    def test_exceeds_height_clamped(self) -> None:
        """Should clamp box that exceeds page height."""
        bbox = BoundingBox(x=10, y=250, width=50, height=100)
        clamped = _clamp_bbox(bbox, 200, 300)
        assert clamped.height == 50  # 300 - 250


# ---------------------------------------------------------------------------
# Tests: FigureDetector init
# ---------------------------------------------------------------------------


class TestFigureDetectorInit:
    """Tests for FigureDetector constructor."""

    def test_default_min_confidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should default to 0.5."""
        monkeypatch.delenv("FIGURE_DETECTION_MIN_CONFIDENCE", raising=False)
        detector = FigureDetector(vision_client=None)
        assert detector.min_confidence == 0.5

    def test_custom_min_confidence(self) -> None:
        """Should accept custom min_confidence."""
        detector = FigureDetector(vision_client=None, min_confidence=0.7)
        assert detector.min_confidence == 0.7

    def test_custom_min_area(self) -> None:
        """Should accept custom min_area."""
        detector = FigureDetector(vision_client=None, min_area=5000)
        assert detector.min_area == 5000

    def test_custom_overlap_threshold(self) -> None:
        """Should accept custom overlap_threshold."""
        detector = FigureDetector(vision_client=None, overlap_threshold=0.3)
        assert detector.overlap_threshold == 0.3


# ---------------------------------------------------------------------------
# Tests: Detect with mocked VLM
# ---------------------------------------------------------------------------


class TestFigureDetectorDetect:
    """Tests for the detect method with mocked VLM."""

    @pytest.mark.asyncio
    async def test_detect_returns_result_with_figures(
        self,
        mock_vision_client: AsyncMock,
        mock_splitter: MagicMock,
    ) -> None:
        """Should return FigureDetectionResult with detected figures."""
        detector = FigureDetector(
            vision_client=mock_vision_client,
            page_splitter=mock_splitter,
        )

        result = await detector.detect(b"fake pdf", source_path="test.pdf")

        assert isinstance(result, FigureDetectionResult)
        assert result.source_path == "test.pdf"
        assert result.total_pages == 1
        assert result.total_figures == 2
        assert result.provider == "vlm"
        assert result.processing_time_ms > 0

    @pytest.mark.asyncio
    async def test_detect_empty_pdf(
        self,
        mock_splitter: MagicMock,
    ) -> None:
        """Should return empty result for 0-page PDF."""
        mock_splitter.split.return_value = []
        detector = FigureDetector(
            vision_client=None,
            page_splitter=mock_splitter,
        )

        result = await detector.detect(b"empty pdf")

        assert result.total_figures == 0
        assert result.pages == []

    @pytest.mark.asyncio
    async def test_detect_no_vlm_client(
        self,
        mock_splitter: MagicMock,
    ) -> None:
        """Should return empty figures when no VLM client configured."""
        detector = FigureDetector(
            vision_client=None,
            page_splitter=mock_splitter,
        )

        result = await detector.detect(b"fake pdf")

        assert result.total_figures == 0

    @pytest.mark.asyncio
    async def test_detect_page_convenience(
        self,
        mock_vision_client: AsyncMock,
        sample_page: PageImage,
    ) -> None:
        """detect_page should detect figures on a single page."""
        detector = FigureDetector(vision_client=mock_vision_client)

        page_result = await detector.detect_page(sample_page)

        assert isinstance(page_result, PageDetectionResult)
        assert page_result.page_index == 0
        assert page_result.page_width == 200
        assert page_result.page_height == 300


# ---------------------------------------------------------------------------
# Tests: Filtering and NMS
# ---------------------------------------------------------------------------


class TestFilteringAndNMS:
    """Tests for confidence/area filtering and non-max suppression."""

    def test_low_confidence_filtered(self) -> None:
        """Should filter out figures below min_confidence."""
        detector = FigureDetector(vision_client=None, min_confidence=0.6)
        figures = [
            DetectedFigure(
                figure_type=FigureType.PLOT,
                bounding_box=BoundingBox(x=0, y=0, width=100, height=100),
                confidence=0.4,
                page_index=0,
            ),
            DetectedFigure(
                figure_type=FigureType.TABLE,
                bounding_box=BoundingBox(x=0, y=0, width=200, height=200),
                confidence=0.9,
                page_index=0,
            ),
        ]

        result = detector._filter_figures(figures, 500, 500)
        assert len(result) == 1
        assert result[0].figure_type == FigureType.TABLE

    def test_small_area_filtered(self) -> None:
        """Should filter out figures below min_area."""
        detector = FigureDetector(vision_client=None, min_area=5000)
        figures = [
            DetectedFigure(
                figure_type=FigureType.PLOT,
                bounding_box=BoundingBox(x=0, y=0, width=10, height=10),
                confidence=0.9,
                page_index=0,
            ),
            DetectedFigure(
                figure_type=FigureType.TABLE,
                bounding_box=BoundingBox(x=0, y=0, width=100, height=100),
                confidence=0.8,
                page_index=0,
            ),
        ]

        result = detector._filter_figures(figures, 500, 500)
        assert len(result) == 1
        assert result[0].bounding_box.width == 100

    def test_nms_removes_overlapping(self) -> None:
        """Should suppress overlapping detections."""
        detector = FigureDetector(vision_client=None, overlap_threshold=0.3)
        figures = [
            DetectedFigure(
                figure_type=FigureType.PLOT,
                bounding_box=BoundingBox(x=0, y=0, width=100, height=100),
                confidence=0.9,
                page_index=0,
            ),
            DetectedFigure(
                figure_type=FigureType.PLOT,
                bounding_box=BoundingBox(x=5, y=5, width=95, height=95),
                confidence=0.7,
                page_index=0,
            ),
        ]

        result = detector._non_max_suppression(figures)
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_nms_keeps_non_overlapping(self) -> None:
        """Should keep non-overlapping detections."""
        detector = FigureDetector(vision_client=None, overlap_threshold=0.5)
        figures = [
            DetectedFigure(
                figure_type=FigureType.PLOT,
                bounding_box=BoundingBox(x=0, y=0, width=50, height=50),
                confidence=0.9,
                page_index=0,
            ),
            DetectedFigure(
                figure_type=FigureType.TABLE,
                bounding_box=BoundingBox(x=200, y=200, width=50, height=50),
                confidence=0.8,
                page_index=0,
            ),
        ]

        result = detector._non_max_suppression(figures)
        assert len(result) == 2

    def test_nms_empty_input(self) -> None:
        """Should return empty list for empty input."""
        detector = FigureDetector(vision_client=None)
        assert detector._non_max_suppression([]) == []


# ---------------------------------------------------------------------------
# Tests: Figure parsing
# ---------------------------------------------------------------------------


class TestParseDetectedFigure:
    """Tests for _parse_detected_figure."""

    def test_parse_valid_figure(self) -> None:
        """Should parse a valid VLM figure dict."""
        detector = FigureDetector(vision_client=None)
        raw = {
            "type": "plot",
            "bbox": [10, 20, 100, 80],
            "confidence": 0.85,
            "caption": "Figure 1",
        }

        fig = detector._parse_detected_figure(raw, page_index=2)

        assert fig.figure_type == FigureType.PLOT
        assert fig.bounding_box.x == 10
        assert fig.confidence == 0.85
        assert fig.caption == "Figure 1"
        assert fig.page_index == 2

    def test_parse_unknown_type(self) -> None:
        """Should use UNKNOWN for unrecognized types."""
        detector = FigureDetector(vision_client=None)
        raw = {
            "type": "flowchart",
            "bbox": [0, 0, 50, 50],
            "confidence": 0.5,
            "caption": "",
        }

        fig = detector._parse_detected_figure(raw, page_index=0)
        assert fig.figure_type == FigureType.UNKNOWN

    def test_parse_clamps_confidence(self) -> None:
        """Should clamp confidence to [0, 1]."""
        detector = FigureDetector(vision_client=None)
        raw = {
            "type": "plot",
            "bbox": [0, 0, 50, 50],
            "confidence": 1.5,
            "caption": "",
        }

        fig = detector._parse_detected_figure(raw, page_index=0)
        assert fig.confidence == 1.0

    def test_parse_defaults(self) -> None:
        """Should use defaults for missing fields."""
        detector = FigureDetector(vision_client=None)
        raw = {}

        fig = detector._parse_detected_figure(raw, page_index=0)
        assert fig.figure_type == FigureType.UNKNOWN
        assert fig.confidence == 0.0
        assert fig.caption == ""


# ---------------------------------------------------------------------------
# Tests: VLM error handling
# ---------------------------------------------------------------------------


class TestVLMErrorHandling:
    """Tests for graceful VLM failure handling."""

    @pytest.mark.asyncio
    async def test_vlm_exception_returns_empty(
        self,
        sample_page: PageImage,
    ) -> None:
        """Should return empty figures when VLM raises an exception."""
        client = AsyncMock()
        client.extract = AsyncMock(side_effect=RuntimeError("VLM unavailable"))

        detector = FigureDetector(vision_client=client)
        result = await detector._call_vlm_detection(sample_page)

        assert result == []

    @pytest.mark.asyncio
    async def test_vlm_non_list_figures(
        self,
        sample_page: PageImage,
    ) -> None:
        """Should return empty list when VLM returns non-list figures."""
        client = AsyncMock()
        client.extract = AsyncMock(return_value={"figures": "not a list"})

        detector = FigureDetector(vision_client=client)
        result = await detector._call_vlm_detection(sample_page)

        assert result == []

    @pytest.mark.asyncio
    async def test_vlm_empty_figures(
        self,
        sample_page: PageImage,
    ) -> None:
        """Should return empty list when VLM finds no figures."""
        client = AsyncMock()
        client.extract = AsyncMock(return_value={"figures": []})

        detector = FigureDetector(vision_client=client)
        result = await detector._call_vlm_detection(sample_page)

        assert result == []


# ---------------------------------------------------------------------------
# Tests: PDF split failure propagation
# ---------------------------------------------------------------------------


class TestSplitFailure:
    """Tests for page splitter failure during detection."""

    @pytest.mark.asyncio
    async def test_split_failure_raises_detector_error(self) -> None:
        """Should raise FigureDetectorError when PDF splitting fails."""
        from nfm_db.services.page_splitter import PageSplitterError

        splitter = MagicMock(spec=PageSplitter)
        splitter.split = MagicMock(side_effect=PageSplitterError("bad PDF"))

        detector = FigureDetector(
            vision_client=None,
            page_splitter=splitter,
        )

        with pytest.raises(FigureDetectorError, match="Failed to split PDF"):
            await detector.detect(b"bad pdf")
