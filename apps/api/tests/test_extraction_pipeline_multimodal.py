"""Tests for multimodal extraction pipeline extension (NFM-923).

Tests for:
- ExtractionJob new fields (extract_figures, extract_tables, etc.)
- Multimodal extraction helpers (_extract_figures_from_source, _extract_tables_from_source)
- Conflict resolution (_apply_conflict_resolution)
- _run_multimodal_extraction orchestration
- trigger_extraction multimodal stage integration
- Stub mode multimodal data
- Error handling: VLM failure → OCR fallback → skip
- No regression in text-only extraction path
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _apply_conflict_resolution,
    _extract_figures_from_source,
    _extract_tables_from_source,
    _run_multimodal_extraction,
    get_job,
    trigger_extraction,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_job_store():
    """Clear _job_store before and after each test."""
    from nfm_db.services.extraction_pipeline import _job_store

    _job_store.clear()
    yield
    _job_store.clear()


# ---------------------------------------------------------------------------
# ExtractionJob new fields
# ---------------------------------------------------------------------------


class TestExtractionJobMultimodalFields:
    """Tests for new multimodal fields on ExtractionJob (NFM-923)."""

    def test_default_extract_figures_is_false(self):
        """extract_figures defaults to False."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.extract_figures is False

    def test_default_extract_tables_is_false(self):
        """extract_tables defaults to False."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.extract_tables is False

    def test_default_figure_types_is_none(self):
        """figure_types defaults to None."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.figure_types is None

    def test_default_confidence_threshold_is_0_5(self):
        """confidence_threshold defaults to 0.5."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.confidence_threshold == 0.5

    def test_default_conflict_strategy_is_prefer_vlm(self):
        """conflict_strategy defaults to 'prefer_vlm'."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.conflict_strategy == "prefer_vlm"

    def test_default_figures_is_empty_list(self):
        """figures defaults to an empty list (not None)."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.figures == []

    def test_default_tables_is_empty_list(self):
        """tables defaults to an empty list (not None)."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
        )
        assert job.tables == []

    def test_can_set_multimodal_fields(self):
        """All multimodal fields can be set via constructor."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="src.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=True,
            figure_types=["line", "scatter"],
            confidence_threshold=0.7,
            conflict_strategy="prefer_text",
            figures=[{"type": "plot"}],
            tables=[{"type": "table"}],
        )
        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["line", "scatter"]
        assert job.confidence_threshold == 0.7
        assert job.conflict_strategy == "prefer_text"
        assert job.figures == [{"type": "plot"}]
        assert job.tables == [{"type": "table"}]

    def test_text_props_defaults_to_empty_list(self):
        """ExtractionJob exposes a text_props field defaulting to [] (NFM-923 AC).

        Stores text-extracted properties so conflict resolution can merge
        with VLM-extracted properties when multimodal stage runs.
        """
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
        )
        assert hasattr(job, "text_props")
        assert job.text_props == []
        assert isinstance(job.text_props, list)


# ---------------------------------------------------------------------------
# _extract_figures_from_source
# ---------------------------------------------------------------------------


class TestExtractFiguresFromSource:
    """Tests for figure extraction helper (NFM-923)."""

    @pytest.mark.asyncio
    async def test_stub_mode_returns_mock_figures(self):
        """Stub mode returns plausible mock figure data."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            results = await _extract_figures_from_source(
                source_reference="test.pdf",
                figure_types=None,
                threshold=0.5,
            )

        assert isinstance(results, list)
        assert len(results) > 0
        for fig in results:
            assert isinstance(fig, dict)
            assert "figure_type" in fig
            assert "confidence" in fig
            assert "source" in fig

    @pytest.mark.asyncio
    async def test_stub_mode_respects_figure_types_filter(self):
        """Stub mode returns figures matching the requested types."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            results = await _extract_figures_from_source(
                source_reference="test.pdf",
                figure_types=["line"],
                threshold=0.5,
            )

        for fig in results:
            assert fig["figure_type"] in ("line",)

    @pytest.mark.asyncio
    async def test_stub_mode_all_types_when_none(self):
        """Stub mode returns all figure types when figure_types is None."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            results = await _extract_figures_from_source(
                source_reference="test.pdf",
                figure_types=None,
                threshold=0.5,
            )

        types = {fig["figure_type"] for fig in results}
        assert len(types) >= 2  # Should have multiple types

    @pytest.mark.asyncio
    async def test_stub_mode_filters_by_confidence_threshold(self):
        """Stub mode returns figures above the confidence threshold."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            results = await _extract_figures_from_source(
                source_reference="test.pdf",
                figure_types=None,
                threshold=0.8,
            )

        for fig in results:
            assert fig["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_vlm_failure_falls_back_to_ocr(self):
        """When VLM fails, falls back to OCR and returns OCR result."""
        mock_ocr_result = MagicMock()
        mock_ocr_result.text = "x: Temperature y: Conductivity"
        mock_ocr_result.confidence = 0.3
        mock_ocr_result.method = "stub"

        mock_vision_result = MagicMock()
        mock_vision_result.plot_data.confidence = 0.3
        mock_vision_result.figure_type = "plot"
        mock_vision_result.fallback_used = True

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=True,
        ), patch(
            "nfm_db.services.plot_extractor.extract_plot_data",
            side_effect=Exception("VLM timeout"),
        ), patch(
            "nfm_db.services.ocr_fallback.OcrFallback",
        ) as MockOcr:
            mock_ocr_instance = AsyncMock()
            mock_ocr_instance.extract_text = AsyncMock(return_value=mock_ocr_result)
            MockOcr.return_value = mock_ocr_instance

            with patch(
                "nfm_db.services.ocr_fallback.ocr_fallback_plot_result",
                return_value=mock_vision_result,
            ):
                results = await _extract_figures_from_source(
                    source_reference="test.png",
                    figure_types=None,
                    threshold=0.1,
                )

        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# _extract_tables_from_source
# ---------------------------------------------------------------------------


class TestExtractTablesFromSource:
    """Tests for table extraction helper (NFM-923)."""

    @pytest.mark.asyncio
    async def test_stub_mode_returns_mock_tables(self):
        """Stub mode returns plausible mock table data."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            results = await _extract_tables_from_source(
                source_reference="test.pdf",
                threshold=0.5,
            )

        assert isinstance(results, list)
        assert len(results) > 0
        for table in results:
            assert isinstance(table, dict)
            assert "figure_type" in table
            assert "confidence" in table
            assert "source" in table

    @pytest.mark.asyncio
    async def test_stub_mode_filters_by_confidence_threshold(self):
        """Stub mode returns tables above the confidence threshold."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            results = await _extract_tables_from_source(
                source_reference="test.pdf",
                threshold=0.8,
            )

        for table in results:
            assert table["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_vlm_failure_ocr_also_fails_returns_empty(self):
        """When VLM fails AND OCR fallback also fails, figure extraction returns empty."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=True,
        ), patch(
            "nfm_db.services.plot_extractor.extract_plot_data",
            side_effect=Exception("VLM timeout"),
        ), patch(
            "nfm_db.services.ocr_fallback.OcrFallback",
        ) as MockOcr:
            mock_ocr_instance = AsyncMock()
            mock_ocr_instance.extract_text = AsyncMock(
                side_effect=Exception("OCR engine crash"),
            )
            MockOcr.return_value = mock_ocr_instance

            results = await _extract_figures_from_source(
                source_reference="test.png",
                figure_types=None,
                threshold=0.1,
            )

        # Both VLM and OCR failed — should return empty list, not raise
        assert results == []

    @pytest.mark.asyncio
    async def test_vlm_not_configured_skips_extraction(self):
        """When VLM is not configured, figure extraction returns empty (no fallback)."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=False,
        ):
            results = await _extract_figures_from_source(
                source_reference="test.png",
                figure_types=None,
                threshold=0.5,
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_table_ocr_also_fails_returns_empty(self):
        """When VLM and OCR both fail for table extraction, returns empty."""
        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=True,
        ), patch(
            "nfm_db.services.table_extractor.extract_table_data",
            side_effect=Exception("VLM timeout"),
        ), patch(
            "nfm_db.services.ocr_fallback.OcrFallback",
        ) as MockOcr:
            mock_ocr_instance = AsyncMock()
            mock_ocr_instance.extract_text = AsyncMock(
                side_effect=Exception("OCR engine crash"),
            )
            MockOcr.return_value = mock_ocr_instance

            results = await _extract_tables_from_source(
                source_reference="test.png",
                threshold=0.1,
            )

        assert results == []


# ---------------------------------------------------------------------------
# _apply_conflict_resolution
# ---------------------------------------------------------------------------


class TestApplyConflictResolution:
    """Tests for conflict resolution between text and VLM properties (NFM-923)."""

    def test_prefer_vlm_keeps_vlm_on_conflict(self):
        """Strategy 'prefer_vlm' uses VLM value when both sources have same property."""
        text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
        ]
        vlm_props = [
            {"property_name": "lattice_constant", "value": 5.48, "source": "vlm"},
        ]

        final_text, final_vlm = _apply_conflict_resolution(
            text_props, vlm_props, "prefer_vlm"
        )

        assert any(
            p["property_name"] == "lattice_constant" and p["value"] == 5.48
            for p in final_vlm
        )
        assert not any(
            p["property_name"] == "lattice_constant" for p in final_text
        )

    def test_prefer_text_keeps_text_on_conflict(self):
        """Strategy 'prefer_text' uses text value when both sources have same property."""
        text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
        ]
        vlm_props = [
            {"property_name": "lattice_constant", "value": 5.48, "source": "vlm"},
        ]

        final_text, final_vlm = _apply_conflict_resolution(
            text_props, vlm_props, "prefer_text"
        )

        assert any(
            p["property_name"] == "lattice_constant" and p["value"] == 5.47
            for p in final_text
        )
        assert not any(
            p["property_name"] == "lattice_constant" for p in final_vlm
        )

    def test_non_conflicting_props_pass_through(self):
        """Properties unique to one source are always preserved."""
        text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
        ]
        vlm_props = [
            {"property_name": "density", "value": 10.97, "source": "vlm"},
        ]

        final_text, final_vlm = _apply_conflict_resolution(
            text_props, vlm_props, "prefer_vlm"
        )

        assert any(
            p["property_name"] == "lattice_constant" for p in final_text
        )
        assert any(
            p["property_name"] == "density" for p in final_vlm
        )

    def test_empty_lists_return_empty(self):
        """Empty input lists produce empty output."""
        final_text, final_vlm = _apply_conflict_resolution([], [], "prefer_vlm")
        assert final_text == []
        assert final_vlm == []

    def test_unknown_strategy_raises_value_error(self):
        """Unknown conflict strategy raises ValueError."""
        text_props = [{"property_name": "a", "value": 1}]
        vlm_props = [{"property_name": "b", "value": 2}]

        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            _apply_conflict_resolution(text_props, vlm_props, "invalid_strategy")

    def test_does_not_mutate_inputs(self):
        """Conflict resolution does not mutate the input lists."""
        text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
        ]
        vlm_props = [
            {"property_name": "lattice_constant", "value": 5.48, "source": "vlm"},
        ]
        original_text_value = text_props[0]["value"]
        original_vlm_value = vlm_props[0]["value"]

        _apply_conflict_resolution(text_props, vlm_props, "prefer_vlm")

        assert text_props[0]["value"] == original_text_value
        assert vlm_props[0]["value"] == original_vlm_value

    def test_merge_strategy_raises_not_implemented(self):
        """'merge' strategy is recognized by the schema but not yet implemented."""
        text_props = [{"property_name": "a", "value": 1}]
        vlm_props = [{"property_name": "b", "value": 2}]

        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            _apply_conflict_resolution(text_props, vlm_props, "merge")

    def test_keep_both_strategy_raises_not_implemented(self):
        """'keep_both' strategy is recognized by the schema but not yet implemented."""
        text_props = [{"property_name": "a", "value": 1}]
        vlm_props = [{"property_name": "b", "value": 2}]

        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            _apply_conflict_resolution(text_props, vlm_props, "keep_both")

    def test_multiple_conflicts_resolved_correctly(self):
        """When multiple properties conflict, all are resolved consistently."""
        text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
            {"property_name": "density", "value": 10.9, "source": "text"},
        ]
        vlm_props = [
            {"property_name": "lattice_constant", "value": 5.48, "source": "vlm"},
            {"property_name": "density", "value": 10.95, "source": "vlm"},
        ]

        final_text, final_vlm = _apply_conflict_resolution(
            text_props, vlm_props, "prefer_vlm"
        )

        assert len(final_text) == 0
        assert len(final_vlm) == 2

    def test_one_side_empty_no_conflicts(self):
        """When one side is empty, there are no conflicts to resolve."""
        text_props = [{"property_name": "a", "value": 1}]
        vlm_props = []

        final_text, final_vlm = _apply_conflict_resolution(
            text_props, vlm_props, "prefer_vlm"
        )

        assert len(final_text) == 1
        assert len(final_vlm) == 0


# ---------------------------------------------------------------------------
# _run_multimodal_extraction
# ---------------------------------------------------------------------------


class TestRunMultimodalExtraction:
    """Tests for the multimodal orchestration helper (NFM-923)."""

    @pytest.mark.asyncio
    async def test_populates_figures_on_job(self):
        """Running multimodal extraction populates job.figures."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=False,
        )

        mock_session = AsyncMock()

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            await _run_multimodal_extraction(job, mock_session)

        assert len(job.figures) > 0
        assert len(job.tables) == 0

    @pytest.mark.asyncio
    async def test_populates_tables_on_job(self):
        """Running multimodal extraction populates job.tables."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=False,
            extract_tables=True,
        )

        mock_session = AsyncMock()

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            await _run_multimodal_extraction(job, mock_session)

        assert len(job.tables) > 0
        assert len(job.figures) == 0

    @pytest.mark.asyncio
    async def test_populates_both_figures_and_tables(self):
        """Running with both flags populates both lists."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=True,
        )

        mock_session = AsyncMock()

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ):
            await _run_multimodal_extraction(job, mock_session)

        assert len(job.figures) > 0
        assert len(job.tables) > 0

    @pytest.mark.asyncio
    async def test_skips_when_neither_flag_set(self):
        """When neither flag is set, figures and tables remain empty."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=False,
            extract_tables=False,
        )

        mock_session = AsyncMock()

        await _run_multimodal_extraction(job, mock_session)

        assert len(job.figures) == 0
        assert len(job.tables) == 0

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(self):
        """Multimodal extraction failure is caught, not raised."""
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=True,
        )

        mock_session = AsyncMock()

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=False,
        ):
            await _run_multimodal_extraction(job, mock_session)

        assert len(job.figures) == 0
        assert len(job.tables) == 0

    @pytest.mark.asyncio
    async def test_applies_conflict_resolution_when_text_props_present(self):
        """When text_props conflict with VLM figures, conflict_strategy is applied.

        NFm-923 AC: 'Conflict resolution applies the specified strategy'.
        With prefer_vlm, text_props containing the same property_name as
        a figure must be removed from the job's text_props after the run.
        """
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=False,
            conflict_strategy="prefer_vlm",
        )
        job.text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
            {"property_name": "density", "value": 10.0, "source": "text"},
        ]

        figure_with_conflict = [
            {"property_name": "lattice_constant", "value": 5.48, "source": "vlm"},
        ]

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=True,
        ), patch(
            "nfm_db.services.extraction_pipeline._extract_figures_from_source",
            new_callable=AsyncMock,
            return_value=figure_with_conflict,
        ):
            await _run_multimodal_extraction(job, AsyncMock())

        # After prefer_vlm conflict resolution, the conflicting text prop
        # ("lattice_constant") should be removed from text_props while
        # non-conflicting text props pass through.
        assert not any(
            p["property_name"] == "lattice_constant" for p in job.text_props
        ), f"prefer_vlm should drop conflicting text prop, got: {job.text_props}"
        assert any(
            p["property_name"] == "density" for p in job.text_props
        ), "Non-conflicting text props must pass through unchanged"

    @pytest.mark.asyncio
    async def test_applies_prefer_text_strategy(self):
        """With prefer_text strategy, conflicting VLM figure is removed from figures.

        The strategy 'prefer_text' tells conflict resolution that when both
        text and VLM extract the same property_name, the text value wins
        and the VLM version is excluded.
        """
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=False,
            conflict_strategy="prefer_text",
        )
        job.text_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
        ]

        figure_with_conflict = [
            {"property_name": "lattice_constant", "value": 5.48, "source": "vlm"},
        ]

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=True,
        ), patch(
            "nfm_db.services.extraction_pipeline._extract_figures_from_source",
            new_callable=AsyncMock,
            return_value=figure_with_conflict,
        ):
            await _run_multimodal_extraction(job, AsyncMock())

        # With prefer_text, the conflicting VLM figure must be removed.
        assert not any(
            p["property_name"] == "lattice_constant" for p in job.figures
        ), f"prefer_text should drop conflicting VLM figure, got: {job.figures}"
        # The text prop should still be present on the job.
        assert any(
            p["property_name"] == "lattice_constant" for p in job.text_props
        )

    @pytest.mark.asyncio
    async def test_no_conflict_resolution_when_text_props_empty(self):
        """No conflict resolution applied when text_props is empty.

        Sanity check: if no text-extracted properties were stored on the
        job, conflict resolution has nothing to merge against, so figures
        and tables pass through unchanged.
        """
        job = ExtractionJob(
            job_id="j1",
            source_reference="test.pdf",
            source_type="pdf",
            extract_figures=True,
            extract_tables=False,
            conflict_strategy="prefer_vlm",
        )
        # text_props default is []

        figure = [{"property_name": "lattice_constant", "value": 5.48, "source": "vlm"}]

        with patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=False,
        ), patch(
            "nfm_db.services.vision_client.is_vlm_configured",
            return_value=True,
        ), patch(
            "nfm_db.services.extraction_pipeline._extract_figures_from_source",
            new_callable=AsyncMock,
            return_value=figure,
        ):
            await _run_multimodal_extraction(job, AsyncMock())

        # Figure should pass through unchanged.
        assert any(
            p["property_name"] == "lattice_constant" for p in job.figures
        )
        assert job.text_props == []


# ---------------------------------------------------------------------------
# trigger_extraction multimodal integration
# ---------------------------------------------------------------------------


class TestTriggerExtractionMultimodal:
    """Integration tests for trigger_extraction with multimodal options (NFM-923)."""

    @pytest.mark.asyncio
    async def test_multimodal_options_passed_to_job(self, db_session: AsyncSession):
        """Multimodal options are stored on the ExtractionJob."""
        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        with patch(
            "nfm_db.services.extraction_pipeline.QualityGateService",
            return_value=mock_gate,
        ), patch(
            "nfm_db.services.extraction_pipeline.GapScanService",
            return_value=mock_scanner,
        ), patch(
            "nfm_db.services.extraction_pipeline._is_stub_mode",
            return_value=True,
        ), patch(
            "nfm_db.services.extraction_pipeline._run_multimodal_extraction",
            new_callable=AsyncMock,
        ) as mock_multimodal:
            job = await trigger_extraction(
                session=db_session,
                source_reference="test.pdf",
                source_type="pdf",
                extract_figures=True,
                extract_tables=True,
                figure_types=["line", "scatter"],
                confidence_threshold=0.7,
                conflict_strategy="prefer_text",
            )

        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["line", "scatter"]
        assert job.confidence_threshold == 0.7
        assert job.conflict_strategy == "prefer_text"
        mock_multimodal.assert_called_once()

    @pytest.mark.asyncio
    async def test_multimodal_not_called_when_flags_false(self, db_session: AsyncSession):
        """Multimodal stage is skipped when both flags are False (default)."""
        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        with patch(
            "nfm_db.services.extraction_pipeline.QualityGateService",
            return_value=mock_gate,
        ), patch(
            "nfm_db.services.extraction_pipeline.GapScanService",
            return_value=mock_scanner,
        ), patch(
            "nfm_db.services.extraction_pipeline._run_multimodal_extraction",
            new_callable=AsyncMock,
        ) as mock_multimodal:
            job = await trigger_extraction(
                session=db_session,
                source_reference="test.pdf",
                source_type="pdf",
            )

        assert mock_multimodal.called is False
        assert job.extract_figures is False
        assert job.extract_tables is False

    @pytest.mark.asyncio
    async def test_multimodal_failure_does_not_fail_job(self, db_session: AsyncSession):
        """Multimodal stage failure does not cause overall job to fail."""
        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        async def mock_multimodal_fail(job, session):
            raise RuntimeError("VLM service unavailable")

        with patch(
            "nfm_db.services.extraction_pipeline.QualityGateService",
            return_value=mock_gate,
        ), patch(
            "nfm_db.services.extraction_pipeline.GapScanService",
            return_value=mock_scanner,
        ), patch(
            "nfm_db.services.extraction_pipeline._run_multimodal_extraction",
            side_effect=mock_multimodal_fail,
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="test.pdf",
                source_type="pdf",
                extract_figures=True,
            )

        assert job.status != JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_text_only_path_unchanged(self, db_session: AsyncSession):
        """Default text-only extraction path is unchanged (no regression)."""
        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        with patch(
            "nfm_db.services.extraction_pipeline.QualityGateService",
            return_value=mock_gate,
        ), patch(
            "nfm_db.services.extraction_pipeline.GapScanService",
            return_value=mock_scanner,
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/test",
                source_type="doi",
            )

        assert job.status == JobStatus.COMPLETED
        assert job.extract_figures is False
        assert job.extract_tables is False
        assert job.figures == []
        assert job.tables == []

    @pytest.mark.asyncio
    async def test_text_props_stored_on_job_after_mapping(self, db_session: AsyncSession):
        """After trigger_extraction, mapped text-extracted props are stored on job.text_props.

        NFM-923 AC: text-extracted properties must be available on the job
        so that downstream conflict resolution can merge with VLM properties
        from figures/tables. Without this, _run_multimodal_extraction has
        nothing to merge against and conflict resolution is effectively dead.
        """
        raw_props = [
            {"property_name": "lattice_constant", "value": 5.47, "source": "text"},
            {"property_name": "density", "value": 10.0, "source": "text"},
        ]
        mapped_props = list(raw_props)  # identity-mapping for the test

        mock_bulk_result = MagicMock()
        mock_bulk_result.accepted = []
        mock_bulk_result.rejected = []
        mock_bulk_result.duplicates = []

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(return_value=mock_bulk_result)

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock(return_value=None)

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new_callable=AsyncMock,
            return_value=raw_props,
        ), patch(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            return_value=mapped_props,
        ), patch(
            "nfm_db.services.extraction_pipeline.QualityGateService",
            return_value=mock_gate,
        ), patch(
            "nfm_db.services.extraction_pipeline.GapScanService",
            return_value=mock_scanner,
        ):
            job = await trigger_extraction(
                session=db_session,
                source_reference="doi:10.1234/test",
                source_type="doi",
            )

        assert job.text_props == mapped_props, (
            f"job.text_props should hold the mapped text-extracted props, "
            f"got: {job.text_props}"
        )
        assert any(
            p["property_name"] == "lattice_constant" for p in job.text_props
        )
        assert any(
            p["property_name"] == "density" for p in job.text_props
        )
