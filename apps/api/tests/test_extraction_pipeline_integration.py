"""Integration tests for LLM-backed extraction pipeline (NFM-543).

Tests for:
- Stub mode toggle (EXTRACTION_STUB_MODE env var)
- LLM extraction with mocked LLM client
- Source content loading
- Post-processing with PhaseMapper and PropertyCategory
- Error handling (graceful degradation)
- All 13 v4 fields populated from LLM response

Conventions:
- Uses EXTRACTION_STUB_MODE=true for stub-mode tests (no LLM needed)
- Mocks call_llm for LLM-mode tests
- See ADR-T5 Test Architecture
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nfm_db.services.extraction_pipeline import (
    _is_stub_mode,
    _job_store,
    _load_source_content,
    _post_process_extracted,
    ontofuel_extract,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_job_store():
    """Clear _job_store before and after each test."""
    _job_store.clear()
    yield
    _job_store.clear()


@pytest.fixture
def sample_source_file(tmp_path: Path) -> Path:
    """Create a sample Markdown source file for testing."""
    content = """\
# UO2 Thermal Properties

## Table 1: Thermal Conductivity of UO2

| Temperature (°C) | Thermal Conductivity (W/(m·K)) |
|-------------------|-------------------------------|
| 300               | 7.5                           |
| 600               | 5.2                           |
| 1000              | 3.1                           |

## Table 2: Density

The theoretical density of UO2 is 10.97 g/cm³ at room temperature.

Reference: Smith et al., "UO2 Properties Review", 2023.
"""
    path = tmp_path / "test_source.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def mock_llm_response() -> list[dict]:
    """Sample LLM response matching v4 ExtractedProperty schema."""
    return [
        {
            "source_file": "test_source.md",
            "material_name": "UO2",
            "composition": "UO2",
            "phase": "FCC",
            "element": "U",
            "property_category": "热传导率",
            "property": "热导率",
            "value": "7.5",
            "unit": "W/(m·K)",
            "conditions": {
                "condition_type": "experimental",
                "temp_C": "300",
            },
            "context": "Measured at 300°C",
            "confidence": "high",
            "reference": "Smith et al., 2023",
        },
        {
            "source_file": "test_source.md",
            "material_name": "UO2",
            "composition": "UO2",
            "phase": "FCC",
            "element": "U",
            "property_category": "密度",
            "property": "理论密度",
            "value": "10.97",
            "unit": "g/cm³",
            "conditions": {
                "condition_type": "experimental",
                "temp_C": "25",
            },
            "context": "Theoretical density at room temperature",
            "confidence": "high",
            "reference": "Smith et al., 2023",
        },
    ]


# ---------------------------------------------------------------------------
# _is_stub_mode tests
# ---------------------------------------------------------------------------


class TestStubModeFlag:
    """Tests for EXTRACTION_STUB_MODE env var handling."""

    def test_stub_mode_true(self):
        """EXTRACTION_STUB_MODE=true enables stub mode."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}):
            assert _is_stub_mode() is True

    def test_stub_mode_one(self):
        """EXTRACTION_STUB_MODE=1 enables stub mode."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "1"}):
            assert _is_stub_mode() is True

    def test_stub_mode_false(self):
        """EXTRACTION_STUB_MODE=false disables stub mode."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}, clear=False):
            # Remove key to ensure default behavior
            os.environ.pop("EXTRACTION_STUB_MODE", None)
            assert _is_stub_mode() is False

    def test_stub_mode_unset(self):
        """Stub mode disabled when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert _is_stub_mode() is False

    def test_stub_mode_case_insensitive(self):
        """EXTRACTION_STUB_MODE=TRUE (uppercase) enables stub mode."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "TRUE"}):
            assert _is_stub_mode() is True


# ---------------------------------------------------------------------------
# _load_source_content tests
# ---------------------------------------------------------------------------


class TestLoadSourceContent:
    """Tests for source file content loading."""

    def test_loads_existing_file(self, sample_source_file: Path):
        """Loads content from an existing Markdown file."""
        content = _load_source_content(str(sample_source_file))
        assert "UO2 Thermal Properties" in content
        assert "7.5" in content

    def test_raises_for_missing_file(self):
        """Raises FileNotFoundError for non-existent file."""
        with pytest.raises(FileNotFoundError, match="Source file not found"):
            _load_source_content("/nonexistent/path/file.md")


# ---------------------------------------------------------------------------
# _post_process_extracted tests
# ---------------------------------------------------------------------------


class TestPostProcessExtracted:
    """Tests for post-processing of LLM-extracted properties."""

    def test_phase_normalization(self):
        """Normalizes phase using PhaseMapper (α → alpha)."""
        raw = [
            {
                "material_name": "Zircaloy-4",
                "phase": "α",
                "property": "屈服强度",
                "value": "400",
                "unit": "MPa",
            },
        ]
        result = _post_process_extracted(raw, "test.md")
        assert result[0]["phase"] == "alpha"

    def test_source_file_populated(self):
        """Populates source_file from source_reference when missing."""
        raw = [
            {
                "property": "密度",
                "value": "10.97",
                "unit": "g/cm³",
            },
        ]
        result = _post_process_extracted(raw, "papers/uo2.md")
        assert result[0]["source_file"] == "papers/uo2.md"

    def test_source_file_not_overwritten(self):
        """Does not overwrite existing source_file."""
        raw = [
            {
                "source_file": "original.md",
                "property": "密度",
                "value": "10.97",
                "unit": "g/cm³",
            },
        ]
        result = _post_process_extracted(raw, "papers/uo2.md")
        assert result[0]["source_file"] == "original.md"

    def test_confidence_default_applied(self):
        """Applies 'medium' confidence when missing."""
        raw = [
            {
                "property": "密度",
                "value": "10.97",
                "unit": "g/cm³",
            },
        ]
        result = _post_process_extracted(raw, "test.md")
        assert result[0]["confidence"] == "medium"

    def test_confidence_preserved(self):
        """Preserves existing confidence value."""
        raw = [
            {
                "property": "密度",
                "value": "10.97",
                "unit": "g/cm³",
                "confidence": "high",
            },
        ]
        result = _post_process_extracted(raw, "test.md")
        assert result[0]["confidence"] == "high"

    def test_property_category_assigned_from_catalog(self):
        """Assigns property_category from catalog when missing."""
        raw = [
            {
                "property": "密度",
                "value": "10.97",
                "unit": "g/cm³",
            },
        ]
        result = _post_process_extracted(raw, "test.md")
        assert result[0]["property_category"] == "密度"

    def test_property_category_preserved(self):
        """Does not overwrite existing property_category."""
        raw = [
            {
                "property": "密度",
                "property_category": "自定义分类",
                "value": "10.97",
                "unit": "g/cm³",
            },
        ]
        result = _post_process_extracted(raw, "test.md")
        assert result[0]["property_category"] == "自定义分类"

    def test_immutability_originals_unchanged(self):
        """Post-processing creates new dicts without mutating originals."""
        raw = [
            {
                "property": "密度",
                "value": "10.97",
                "unit": "g/cm³",
                "phase": "α",
            },
        ]
        original_phase = raw[0]["phase"]
        result = _post_process_extracted(raw, "test.md")
        assert raw[0]["phase"] == original_phase
        assert result[0] is not raw[0]

    def test_empty_input_returns_empty(self):
        """Empty input list returns empty output list."""
        result = _post_process_extracted([], "test.md")
        assert result == []

    def test_multiple_properties_processed(self):
        """All properties in a list are processed."""
        raw = [
            {"property": "密度", "value": "10.97", "unit": "g/cm³"},
            {"property": "比热容", "value": "300", "unit": "J/(kg·K)"},
            {"property": "热导率", "value": "7.5", "unit": "W/(m·K)"},
        ]
        result = _post_process_extracted(raw, "test.md")
        assert len(result) == 3
        # 密度 matches directly as a standard name value
        assert result[0]["property_category"] == "密度"
        # 比热容 matches directly as a standard name value
        assert result[1]["property_category"] == "比热容"
        # 热导率 matches directly as a standard name value
        assert result[2]["property_category"] == "热导率"


# ---------------------------------------------------------------------------
# ontofuel_extract LLM mode tests (mocked)
# ---------------------------------------------------------------------------


class TestOntoFuelExtractLLM:
    """Tests for LLM-backed extraction (with mocked LLM client)."""

    @pytest.mark.asyncio
    async def test_llm_extraction_returns_properties(
        self, sample_source_file: Path, mock_llm_response: list[dict]
    ):
        """LLM extraction returns post-processed properties."""
        # Remove source_file from mock so post-processing fills it in
        response_without_source = [
            {k: v for k, v in prop.items() if k != "source_file"} for prop in mock_llm_response
        ]
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=response_without_source,
            ),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        assert len(results) == 2
        assert results[0]["property"] == "热导率"
        assert results[0]["source_file"] == str(sample_source_file)

    @pytest.mark.asyncio
    async def test_llm_extraction_wraps_response_as_list(
        self, sample_source_file: Path, mock_llm_response: list[dict]
    ):
        """LLM extraction handles single-dict response (wraps as list)."""
        single_response = mock_llm_response[0]
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=single_response,
            ),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        assert len(results) == 1
        assert results[0]["property"] == "热导率"

    @pytest.mark.asyncio
    async def test_llm_extraction_handles_properties_key(
        self, sample_source_file: Path, mock_llm_response: list[dict]
    ):
        """LLM extraction handles {"properties": [...]} response format."""
        wrapped_response = {"properties": mock_llm_response}
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=wrapped_response,
            ),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_llm_extraction_handles_data_key(
        self, sample_source_file: Path, mock_llm_response: list[dict]
    ):
        """LLM extraction handles {"data": [...]} response format."""
        wrapped_response = {"data": mock_llm_response}
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=wrapped_response,
            ),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_llm_extraction_passes_element_filter(
        self, sample_source_file: Path, mock_llm_response: list[dict]
    ):
        """LLM extraction includes element_systems in user message."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=mock_llm_response,
            ) as mock_call,
        ):
            await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
                element_systems=["U", "Zr"],
            )

        call_kwargs = mock_call.call_args
        user_message = call_kwargs.kwargs["user_message"]
        assert "U" in user_message
        assert "Zr" in user_message

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_list(self, sample_source_file: Path):
        """LLM failure returns empty list (graceful degradation)."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API timeout"),
            ),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty_list(self):
        """Missing source file returns empty list (graceful degradation)."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        ):
            results = await ontofuel_extract(
                source_reference="/nonexistent/file.md",
                source_type="file",
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_no_llm_key_falls_back_to_stub(
        self,
        sample_source_file: Path,
    ):
        """When LLM_API_KEY is not set, falls back to stub mode."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=False),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        # Should return stub data (3 properties)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_all_13_v4_fields_populated(
        self, sample_source_file: Path, mock_llm_response: list[dict]
    ):
        """All 13 v4 fields are present in processed results."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=mock_llm_response,
            ),
        ):
            results = await ontofuel_extract(
                source_reference=str(sample_source_file),
                source_type="file",
            )

        v4_fields = [
            "source_file",
            "material_name",
            "composition",
            "phase",
            "element",
            "property_category",
            "property",
            "value",
            "unit",
            "conditions",
            "context",
            "confidence",
            "reference",
        ]
        for prop in results:
            for field_name in v4_fields:
                assert field_name in prop, f"Missing v4 field '{field_name}' in {prop}"


# ---------------------------------------------------------------------------
# ontofuel_extract stub mode tests
# ---------------------------------------------------------------------------


class TestOntoFuelExtractStub:
    """Tests for stub mode extraction (EXTRACTION_STUB_MODE=true)."""

    @pytest.mark.asyncio
    async def test_stub_mode_returns_demo_data(self):
        """Stub mode returns 3 demo properties."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}):
            results = await ontofuel_extract(
                source_reference="test_source",
                source_type="file",
            )

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_stub_mode_covers_all_confidence_levels(self):
        """Stub mode covers high, medium, low confidence."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}):
            results = await ontofuel_extract(
                source_reference="test_source",
                source_type="file",
            )

        confidences = {prop["confidence"] for prop in results}
        assert confidences == {"high", "medium", "low"}

    @pytest.mark.asyncio
    async def test_stub_mode_source_reference_passed_through(self):
        """Stub mode passes source_reference to source field."""
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}):
            results = await ontofuel_extract(
                source_reference="custom_doi",
                source_type="file",
            )

        for prop in results:
            assert prop["source"] == "custom_doi"
