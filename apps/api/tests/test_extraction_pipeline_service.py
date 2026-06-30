"""Additional unit tests for extraction pipeline service (NFM-583).

Covers areas not tested in test_extraction_pipeline.py:
- _is_stub_mode (env var detection)
- _load_source_content (file loading, missing file)
- _post_process_extracted (phase normalization, category assignment, defaults)
- _stub_extraction_results (structure validation)
- ExtractionJob dataclass (defaults, field types)
- _update_job (immutable-style updates)
- ontofuel_extract LLM fallback (when not stub mode and LLM not configured)
- trigger_extraction gap scan failure (non-fatal)

See test_extraction_pipeline.py for the main test suite.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _apply_property_mapping,
    _find_matching,
    _is_stub_mode,
    _job_store,
    _load_source_content,
    _post_process_extracted,
    _stub_extraction_results,
    _update_job,
    ontofuel_extract,
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
# _is_stub_mode tests
# ---------------------------------------------------------------------------


class TestIsStubMode:
    """Tests for stub mode environment detection."""

    def test_true_when_env_is_true(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}):
            assert _is_stub_mode() is True

    def test_true_when_env_is_one(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "1"}):
            assert _is_stub_mode() is True

    def test_true_when_env_is_true_uppercase(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "TRUE"}):
            assert _is_stub_mode() is True

    def test_false_when_env_is_false(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}):
            assert _is_stub_mode() is False

    def test_false_when_env_is_zero(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "0"}):
            assert _is_stub_mode() is False

    def test_false_when_env_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _is_stub_mode() is False

    def test_false_when_env_is_empty_string(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": ""}):
            assert _is_stub_mode() is False


# ---------------------------------------------------------------------------
# _load_source_content tests
# ---------------------------------------------------------------------------


class TestLoadSourceContent:
    """Tests for source file loading."""

    def test_loads_existing_file(self, tmp_path: Path) -> None:
        content_file = tmp_path / "source.md"
        content_file.write_text("# Nuclear Fuel Properties\nUO2 density", encoding="utf-8")

        result = _load_source_content(str(content_file))
        assert "# Nuclear Fuel Properties" in result
        assert "UO2 density" in result

    def test_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            _load_source_content("/nonexistent/file.md")

    def test_loads_utf8(self, tmp_path: Path) -> None:
        content_file = tmp_path / "unicode.md"
        content_file.write_text("密度 densité плотность", encoding="utf-8")

        result = _load_source_content(str(content_file))
        assert "密度" in result


# ---------------------------------------------------------------------------
# _stub_extraction_results tests
# ---------------------------------------------------------------------------


class TestStubExtractionResults:
    """Tests for stub extraction result generation."""

    def test_returns_list(self) -> None:
        results = _stub_extraction_results("test_source")
        assert isinstance(results, list)

    def test_returns_three_properties(self) -> None:
        results = _stub_extraction_results("test_source")
        assert len(results) == 3

    def test_source_passed_through(self) -> None:
        results = _stub_extraction_results("custom_source")
        assert all(r["source"] == "custom_source" for r in results)

    def test_high_confidence_first(self) -> None:
        results = _stub_extraction_results("test")
        assert results[0]["confidence"] == "high"

    def test_medium_confidence_second(self) -> None:
        results = _stub_extraction_results("test")
        assert results[1]["confidence"] == "medium"

    def test_low_confidence_third(self) -> None:
        results = _stub_extraction_results("test")
        assert results[2]["confidence"] == "low"

    def test_all_have_values(self) -> None:
        results = _stub_extraction_results("test")
        for r in results:
            assert "value" in r
            assert r["value"] is not None

    def test_all_have_units(self) -> None:
        results = _stub_extraction_results("test")
        for r in results:
            assert "unit" in r
            assert r["unit"] is not None

    def test_cache_levels_present(self) -> None:
        results = _stub_extraction_results("test")
        for r in results:
            assert "cache_level" in r


# ---------------------------------------------------------------------------
# ExtractionJob dataclass tests
# ---------------------------------------------------------------------------


class TestExtractionJob:
    """Tests for the ExtractionJob dataclass."""

    def test_default_status_is_queued(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.status == JobStatus.QUEUED

    def test_counts_default_to_zero(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.extracted_count == 0
        assert job.staged_count == 0
        assert job.rejected_count == 0

    def test_error_message_default_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.error_message is None

    def test_timestamps_default_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.started_at is None
        assert job.completed_at is None

    def test_created_at_auto_set(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.created_at is not None

    def test_optional_fields_nullable(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        assert job.fill_batch_id is None
        assert job.element_systems is None
        assert job.cache_level is None
        assert job.max_confidence is None


# ---------------------------------------------------------------------------
# _update_job tests
# ---------------------------------------------------------------------------


class TestUpdateJob:
    """Tests for immutable-style job update."""

    def test_update_status(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        _update_job(job, status=JobStatus.RUNNING)
        assert job.status == JobStatus.RUNNING

    def test_update_multiple_fields(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        _update_job(
            job,
            status=JobStatus.COMPLETED,
            extracted_count=10,
            staged_count=8,
            rejected_count=2,
        )
        assert job.status == JobStatus.COMPLETED
        assert job.extracted_count == 10
        assert job.staged_count == 8
        assert job.rejected_count == 2

    def test_update_ignores_unknown_field(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        _update_job(job, nonexistent_field="value")  # Should not raise
        assert job.status == JobStatus.QUEUED

    def test_update_started_at(self) -> None:
        from datetime import UTC, datetime

        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        now = datetime.now(UTC)
        _update_job(job, started_at=now)
        assert job.started_at == now

    def test_update_error_message(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="doi")
        _update_job(job, error_message="Connection timeout")
        assert job.error_message == "Connection timeout"


# ---------------------------------------------------------------------------
# _post_process_extracted tests
# ---------------------------------------------------------------------------


class TestPostProcessExtracted:
    """Tests for post-processing of extracted properties."""

    def test_adds_source_file_when_missing(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        processed = _post_process_extracted(raw, "test_source.md")
        assert processed[0]["source_file"] == "test_source.md"

    def test_preserves_existing_source_file(self) -> None:
        raw = [{"property_name": "density", "source_file": "original.md", "value": 10.0}]
        processed = _post_process_extracted(raw, "test_source.md")
        assert processed[0]["source_file"] == "original.md"

    def test_adds_default_confidence(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        processed = _post_process_extracted(raw, "test")
        assert processed[0]["confidence"] == "medium"

    def test_preserves_existing_confidence(self) -> None:
        raw = [{"property_name": "density", "confidence": "high", "value": 10.0}]
        processed = _post_process_extracted(raw, "test")
        assert processed[0]["confidence"] == "high"

    def test_creates_new_dicts(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        original = raw[0]
        processed = _post_process_extracted(raw, "test")
        assert processed[0] is not original
        assert "source_file" not in original

    def test_handles_empty_list(self) -> None:
        processed = _post_process_extracted([], "test")
        assert processed == []

    def test_handles_multiple_properties(self) -> None:
        raw = [
            {"property_name": "density", "value": 10.0},
            {"property_name": "lattice", "value": 5.47},
        ]
        processed = _post_process_extracted(raw, "test")
        assert len(processed) == 2
        assert processed[0]["source_file"] == "test"
        assert processed[1]["source_file"] == "test"


# ---------------------------------------------------------------------------
# ontofuel_extract LLM fallback tests
# ---------------------------------------------------------------------------


class TestOntoFuelExtractLLMFallback:
    """Tests for LLM extraction fallback behavior."""

    @pytest.mark.asyncio
    async def test_falls_back_to_stub_when_llm_not_configured(self) -> None:
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=False),
        ):
            results = await ontofuel_extract("test_source", "doi")
            # Should fall back to stub
            assert len(results) == 3
            assert results[0]["element_system"] == "UO2"

    @pytest.mark.asyncio
    async def test_uses_stub_when_env_set(self) -> None:
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        ):
            results = await ontofuel_extract("test_source", "doi")
            assert len(results) == 3

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty(self) -> None:
        """When real LLM extraction fails, returns empty list."""
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="content"),
            patch("nfm_db.services.extraction_pipeline.build_extraction_system_prompt", return_value="prompt"),
            patch("nfm_db.services.extraction_pipeline.call_llm", side_effect=RuntimeError("API error")),
        ):
            results = await ontofuel_extract("failing.md", "file")
            assert results == []

    @pytest.mark.asyncio
    async def test_llm_returns_list_uses_directly(self) -> None:
        """When LLM returns a list, it's used directly."""
        llm_results = [
            {"property_name": "density", "value": 10.0, "confidence": "high"},
        ]
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="content"),
            patch("nfm_db.services.extraction_pipeline.build_extraction_system_prompt", return_value="prompt"),
            patch("nfm_db.services.extraction_pipeline.call_llm", new_callable=AsyncMock, return_value=llm_results),
        ):
            results = await ontofuel_extract("source.md", "file")
            assert len(results) >= 1
            assert results[0]["property_name"] == "density"

    @pytest.mark.asyncio
    async def test_llm_returns_dict_with_properties_key(self) -> None:
        """When LLM returns {'properties': [...]}, it's unwrapped."""
        llm_result = {"properties": [{"property_name": "mass", "value": 238.0}]}
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="content"),
            patch("nfm_db.services.extraction_pipeline.build_extraction_system_prompt", return_value="prompt"),
            patch("nfm_db.services.extraction_pipeline.call_llm", new_callable=AsyncMock, return_value=llm_result),
        ):
            results = await ontofuel_extract("source.md", "file")
            assert len(results) >= 1
            assert results[0]["property_name"] == "mass"

    @pytest.mark.asyncio
    async def test_llm_returns_dict_with_data_key(self) -> None:
        """When LLM returns {'data': [...]}, it's unwrapped."""
        llm_result = {"data": [{"property_name": "energy", "value": 100.0}]}
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="content"),
            patch("nfm_db.services.extraction_pipeline.build_extraction_system_prompt", return_value="prompt"),
            patch("nfm_db.services.extraction_pipeline.call_llm", new_callable=AsyncMock, return_value=llm_result),
        ):
            results = await ontofuel_extract("source.md", "file")
            assert len(results) >= 1
            assert results[0]["property_name"] == "energy"

    @pytest.mark.asyncio
    async def test_file_not_found_returns_empty(self) -> None:
        """When source file doesn't exist, returns empty list."""
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch("nfm_db.services.extraction_pipeline._load_source_content", side_effect=FileNotFoundError("not found")),
        ):
            results = await ontofuel_extract("missing.md", "file")
            assert results == []


# ---------------------------------------------------------------------------
# _apply_property_mapping tests
# ---------------------------------------------------------------------------


class TestApplyPropertyMapping:
    """Tests for property name mapping with optional nfm-ref-gapfill."""

    def test_identity_mapping_when_no_gapfill(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert len(result) == 1
        assert result[0]["property_name"] == "density"

    def test_adds_property_alias(self) -> None:
        raw = [{"property_name": "lattice_constant", "value": 5.47}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert result[0]["property"] == "lattice_constant"

    def test_preserves_existing_property(self) -> None:
        raw = [{"property_name": "energy", "property": "energy", "value": 100}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert result[0]["property"] == "energy"

    def test_applies_cache_level_override(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _apply_property_mapping(raw, cache_level="L2")
        assert result[0]["cache_level"] == "L2"

    def test_no_cache_level_when_none(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert "cache_level" not in result[0]

    def test_creates_new_dicts(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        original = raw[0]
        result = _apply_property_mapping(raw, cache_level=None)
        assert result[0] is not original

    def test_empty_list_returns_empty(self) -> None:
        result = _apply_property_mapping([], cache_level=None)
        assert result == []

    def test_with_gapfill_mapping(self) -> None:
        with patch.dict("sys.modules", {"nfm_ref_gapfill": MagicMock()}):
            mock_module = MagicMock()
            mock_module.property_mapping = MagicMock()
            mock_module.property_mapping.map_property = lambda name, src: f"MAPPED:{name}"
            import sys

            sys.modules["nfm_ref_gapfill.property_mapping"] = mock_module.property_mapping
            try:
                # The function imports map_property at call time
                raw = [{"property_name": "test", "source": "doi", "value": 1.0}]
                result = _apply_property_mapping(raw, cache_level=None)
                assert len(result) == 1
                assert result[0]["property_name"] == "MAPPED:test"
            finally:
                sys.modules.pop("nfm_ref_gapfill.property_mapping", None)


# ---------------------------------------------------------------------------
# _find_matching tests
# ---------------------------------------------------------------------------


class TestFindMatching:
    """Tests for finding matching raw input by dedup hash."""

    def test_returns_matching_dict(self) -> None:
        raw = [
            {
                "property_name": "density",
                "property": "density",
                "element_system": "UO2",
                "source": "doi:10.0/test",
                "value": 10.0,
            },
        ]
        # Compute the expected hash for this raw entry
        from nfm_db.services.quality_gate import compute_dedup_hash

        expected_hash = compute_dedup_hash(
            element_system="UO2",
            phase=None,
            property_name="density",
            method=None,
            source="doi:10.0/test",
        )
        result = _find_matching(raw, expected_hash)
        assert result is not None
        assert result["property_name"] == "density"

    def test_returns_none_when_no_match(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _find_matching(raw, "nonexistent_hash")
        assert result is None

    def test_returns_none_for_empty_list(self) -> None:
        result = _find_matching([], "any_hash")
        assert result is None


# ---------------------------------------------------------------------------
# trigger_extraction tests (mocked orchestration)
# ---------------------------------------------------------------------------


class TestTriggerExtraction:
    """Tests for the full extraction pipeline orchestration."""

    @pytest.mark.asyncio
    async def test_successful_pipeline_with_empty_results(self) -> None:
        """Pipeline completes when extraction returns empty list."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=[]),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.GapScanService"),
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
            assert job.status == JobStatus.COMPLETED
            assert job.extracted_count == 0
            # Early return path: commit is NOT called when extraction is empty
            mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_failure_sets_failed_status(self) -> None:
        """Pipeline sets FAILED status when extraction raises."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, side_effect=RuntimeError("extraction failed")),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.GapScanService"),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                mock_session,
                source_reference="fail_source",
                source_type="doi",
            )
            assert job.status == JobStatus.FAILED
            assert "extraction failed" in job.error_message
            mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_with_results_and_no_accepted(self) -> None:
        """Pipeline completes when quality gate rejects all properties."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[],
                rejected=[MagicMock()],
                duplicates=[],
            )
        )

        extracted = [{"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"}]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="reject_source",
                source_type="file",
            )
            # Pipeline logic: PARTIAL when rejected > 0
            assert job.status == JobStatus.PARTIAL
            assert job.staged_count == 0
            assert job.rejected_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_with_accepted_results(self) -> None:
        """Pipeline stages accepted results and runs gap scan."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        accepted_result = MagicMock(dedup_hash="test_hash")
        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[accepted_result],
                rejected=[],
                duplicates=[],
            )
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

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="test_hash"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="good_source",
                source_type="doi",
            )
            assert job.staged_count == 1
            assert job.rejected_count == 0
            mock_scanner.scan_gaps.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_gap_scan_failure_is_non_fatal(self) -> None:
        """Pipeline continues when gap scan throws."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[],
                duplicates=[],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = MagicMock()
        mock_scanner.scan_gaps = AsyncMock(side_effect=RuntimeError("scan failed"))

        extracted = [
            {
                "property_name": "energy",
                "value": 100.0,
                "source": "test",
                "confidence": "high",
                "property": "energy",
                "element_system": "UO2",
            },
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="gap_fail_source",
                source_type="doi",
            )
            # Should still complete (gap scan failure is non-fatal)
            assert job.status == JobStatus.COMPLETED
            assert job.staged_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_partial_when_rejected_exist(self) -> None:
        """Pipeline sets PARTIAL status when some results are rejected."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[MagicMock()],
                duplicates=[],
            )
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

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.ontofuel_extract", new_callable=AsyncMock, return_value=extracted),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="partial_source",
                source_type="doi",
            )
            assert job.status == JobStatus.PARTIAL
            assert job.staged_count == 1
            assert job.rejected_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_stores_job_in_store(self) -> None:
        """Pipeline stores job in the global job store."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", new_callable=MagicMock),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="store_test",
                source_type="doi",
            )
            assert job.job_id in _job_store
            assert _job_store[job.job_id] is job
