"""Golden test regression suite for the extraction pipeline (NFM-553).

Ensures rule changes do not break existing extraction behavior by testing
deterministic pipeline stages against pre-approved fixture data.

Each golden fixture is a JSON file in tests/fixtures/golden/ representing
a representative paper's extraction results. Fixtures cover:
- LaTeX patterns (subscripts, superscripts, scientific notation)
- Range formats (to, between, dash-separated, ±)
- Extractability filters (trends, comparisons, references)
- Confidence assessment levels (high/medium/low)
- Diverse property categories (structural, thermal, mechanical, irradiation)

Regeneration:
    pytest tests/test_golden_regression.py --regenerate
    # Overwrites expected outputs with current pipeline behavior
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nfm_db.core.extraction_rules import (
    assess_confidence,
    clean_latex,
    is_extractable,
    parse_value,
)
from nfm_db.services.quality_gate import compute_dedup_hash
from nfm_db.services.v4_mapper import v4_record_to_staging

# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"


def _load_golden_fixtures() -> list[dict[str, Any]]:
    """Load all golden fixture files from the golden/ directory.

    Returns:
        List of fixture dicts, each containing 'id', 'description', 'records'.
        Returns empty list if directory does not exist (tests will skip).
    """
    if not GOLDEN_DIR.exists():
        return []

    fixtures: list[dict[str, Any]] = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            fixture = json.load(f)
        fixture["_source_path"] = str(path)
        fixtures.append(fixture)

    return fixtures


# Load once at collection time
_GOLDEN_FIXTURES = _load_golden_fixtures()


def _generate_golden_test_ids() -> list[str]:
    """Generate test IDs for parametrize."""
    ids: list[str] = []
    for fixture in _GOLDEN_FIXTURES:
        fixture_id = fixture.get("id", "unknown")
        for record in fixture.get("records", []):
            record_id = record.get("id", "unknown")
            ids.append(f"{fixture_id}::{record_id}")
    return ids


def _collect_test_cases() -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Collect (fixture, record, fixture_path) tuples for parametrization."""
    cases: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for fixture in _GOLDEN_FIXTURES:
        source_path = fixture.get("_source_path", "")
        for record in fixture.get("records", []):
            cases.append((fixture, record, source_path))
    return cases


# ---------------------------------------------------------------------------
# Parametrized golden regression tests
# ---------------------------------------------------------------------------


class TestGoldenRegression:
    """Regression tests that compare pipeline behavior against golden fixtures.

    Each test case represents a single extraction record from a representative
    paper. The test runs the record through multiple pipeline stages and
    compares actual output to the expected baseline.
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_fixtures(self) -> None:
        """Skip all tests in this class if no golden fixtures exist."""
        if not _GOLDEN_FIXTURES:
            pytest.skip("No golden fixtures available")

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture,record,source_path",
        _collect_test_cases(),
        ids=_generate_golden_test_ids(),
    )
    def test_parse_value_against_golden(
        self,
        fixture: dict[str, Any],
        record: dict[str, Any],
        source_path: str,
    ) -> None:
        """Assert parse_value() matches golden baseline."""
        input_record = record["input"]
        expected = record["expected"]
        raw_value = str(input_record["value"])

        # Should not raise
        parsed = parse_value(raw_value)

        expected_parsed = expected["parsed_value"]
        assert parsed.main_value == pytest.approx(
            expected_parsed["main_value"],
        ), (
            f"main_value mismatch in {source_path}: "
            f"expected {expected_parsed['main_value']}, got {parsed.main_value}"
        )

        if expected_parsed.get("uncertainty") is not None:
            assert parsed.uncertainty == pytest.approx(
                expected_parsed["uncertainty"],
            ), (
                f"uncertainty mismatch in {source_path}: "
                f"expected {expected_parsed['uncertainty']}, got {parsed.uncertainty}"
            )
        else:
            assert parsed.uncertainty is None, (
                f"expected no uncertainty in {source_path}, "
                f"got {parsed.uncertainty}"
            )

        if expected_parsed.get("range") is not None:
            expected_range = expected_parsed["range"]
            assert parsed.range is not None, (
                f"expected range in {source_path}, got None"
            )
            assert parsed.range[0] == pytest.approx(expected_range[0]), (
                f"range min mismatch in {source_path}: "
                f"expected {expected_range[0]}, got {parsed.range[0]}"
            )
            assert parsed.range[1] == pytest.approx(expected_range[1]), (
                f"range max mismatch in {source_path}: "
                f"expected {expected_range[1]}, got {parsed.range[1]}"
            )
        else:
            assert parsed.range is None, (
                f"expected no range in {source_path}, got {parsed.range}"
            )

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture,record,source_path",
        _collect_test_cases(),
        ids=_generate_golden_test_ids(),
    )
    def test_clean_latex_against_golden(
        self,
        fixture: dict[str, Any],
        record: dict[str, Any],
        source_path: str,
    ) -> None:
        """Assert clean_latex() matches golden baseline."""
        input_record = record["input"]
        expected = record["expected"]
        raw_value = str(input_record["value"])

        cleaned = clean_latex(raw_value)
        assert cleaned == expected["cleaned_value"], (
            f"clean_latex mismatch in {source_path}: "
            f"expected {expected['cleaned_value']!r}, got {cleaned!r}"
        )

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture,record,source_path",
        _collect_test_cases(),
        ids=_generate_golden_test_ids(),
    )
    def test_confidence_against_golden(
        self,
        fixture: dict[str, Any],
        record: dict[str, Any],
        source_path: str,
    ) -> None:
        """Assert assess_confidence() matches golden baseline."""
        input_record = record["input"]
        expected = record["expected"]

        # Build a dict suitable for assess_confidence
        confidence_record: dict[str, Any] = {
            "source_file": input_record.get("source_file", ""),
            "material_name": input_record.get("material_name", ""),
            "property_category": input_record.get("property_category", ""),
            "property": input_record.get("property", ""),
            "value": input_record.get("value", ""),
            "unit": input_record.get("unit", ""),
            "reference": input_record.get("reference", ""),
        }
        if input_record.get("phase"):
            confidence_record["phase"] = input_record["phase"]
        if input_record.get("conditions"):
            confidence_record["conditions"] = input_record["conditions"]

        confidence = assess_confidence(confidence_record)
        assert confidence.value == expected["confidence"], (
            f"confidence mismatch in {source_path}: "
            f"expected {expected['confidence']!r}, got {confidence.value!r}"
        )

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture,record,source_path",
        _collect_test_cases(),
        ids=_generate_golden_test_ids(),
    )
    def test_dedup_hash_against_golden(
        self,
        fixture: dict[str, Any],
        record: dict[str, Any],
        source_path: str,
    ) -> None:
        """Assert dedup hash computation matches golden baseline."""
        input_record = record["input"]
        expected = record["expected"]

        dedup_hash = compute_dedup_hash(
            element_system=input_record.get("material_name", ""),
            phase=input_record.get("phase"),
            property_name=input_record.get("property", ""),
            method=None,
            source=input_record.get("reference", ""),
        )
        assert dedup_hash == expected["dedup_hash"], (
            f"dedup_hash mismatch in {source_path}: "
            f"expected {expected['dedup_hash']}, got {dedup_hash}"
        )

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture,record,source_path",
        _collect_test_cases(),
        ids=_generate_golden_test_ids(),
    )
    def test_v4_staging_mapping_against_golden(
        self,
        fixture: dict[str, Any],
        record: dict[str, Any],
        source_path: str,
    ) -> None:
        """Assert v4_record_to_staging() matches golden baseline."""
        input_record = record["input"]
        expected = record["expected"]

        staging = v4_record_to_staging(input_record)

        expected_staging = expected["staging"]
        for key, expected_val in expected_staging.items():
            actual_val = staging.get(key)
            if isinstance(expected_val, (int, float)):
                assert actual_val == pytest.approx(
                    float(expected_val),
                ), (
                    f"staging.{key} mismatch in {source_path}: "
                    f"expected {expected_val}, got {actual_val}"
                )
            else:
                assert actual_val == expected_val, (
                    f"staging.{key} mismatch in {source_path}: "
                    f"expected {expected_val!r}, got {actual_val!r}"
                )


# ---------------------------------------------------------------------------
# Fixture integrity tests
# ---------------------------------------------------------------------------


class TestGoldenFixtureIntegrity:
    """Validate that golden fixtures are well-formed."""

    def test_golden_directory_exists(self) -> None:
        """Golden fixtures directory must exist."""
        assert GOLDEN_DIR.exists(), f"Missing: {GOLDEN_DIR}"

    def test_minimum_fixture_count(self) -> None:
        """At least 10 golden fixture files must exist."""
        json_files = list(GOLDEN_DIR.glob("*.json"))
        assert len(json_files) >= 10, (
            f"Expected >= 10 golden fixtures, found {len(json_files)}"
        )

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture",
        _GOLDEN_FIXTURES,
        ids=[f.get("id", "unknown") for f in _GOLDEN_FIXTURES],
    )
    def test_fixture_schema(self, fixture: dict[str, Any]) -> None:
        """Each fixture must have required top-level fields."""
        assert "id" in fixture, "Missing 'id' field"
        assert "description" in fixture, "Missing 'description' field"
        assert "source_file" in fixture, "Missing 'source_file' field"
        assert "property_category" in fixture, "Missing 'property_category' field"
        assert "records" in fixture, "Missing 'records' field"
        assert isinstance(fixture["records"], list), "'records' must be a list"
        assert len(fixture["records"]) > 0, "'records' must not be empty"

    @pytest.mark.golden
    @pytest.mark.parametrize(
        "fixture",
        _GOLDEN_FIXTURES,
        ids=[f.get("id", "unknown") for f in _GOLDEN_FIXTURES],
    )
    def test_record_schema(self, fixture: dict[str, Any]) -> None:
        """Each record must have input and expected sections."""
        for record in fixture["records"]:
            assert "id" in record, "Record missing 'id'"
            assert "input" in record, "Record missing 'input'"
            assert "expected" in record, "Record missing 'expected'"

            expected = record["expected"]
            required_expected_keys = [
                "parsed_value",
                "cleaned_value",
                "confidence",
                "dedup_hash",
                "staging",
            ]
            for key in required_expected_keys:
                assert key in expected, f"Expected missing key: {key}"


# ---------------------------------------------------------------------------
# Fixture regeneration (pytest --regenerate)
# ---------------------------------------------------------------------------


def _regenerate_golden_output(
    input_record: dict[str, Any],
) -> dict[str, Any]:
    """Regenerate expected output from current pipeline behavior.

    Used by the --regenerate flag to update golden baselines.
    """
    raw_value = str(input_record["value"])

    # parse_value
    parsed = parse_value(raw_value)
    parsed_value_out: dict[str, Any] = {
        "main_value": parsed.main_value,
    }
    if parsed.uncertainty is not None:
        parsed_value_out["uncertainty"] = parsed.uncertainty
    if parsed.range is not None:
        parsed_value_out["range"] = list(parsed.range)

    # clean_latex
    cleaned_value = clean_latex(raw_value)

    # assess_confidence
    confidence_record: dict[str, Any] = {
        "source_file": input_record.get("source_file", ""),
        "material_name": input_record.get("material_name", ""),
        "property_category": input_record.get("property_category", ""),
        "property": input_record.get("property", ""),
        "value": input_record.get("value", ""),
        "unit": input_record.get("unit", ""),
        "reference": input_record.get("reference", ""),
    }
    if input_record.get("phase"):
        confidence_record["phase"] = input_record["phase"]
    if input_record.get("conditions"):
        confidence_record["conditions"] = input_record["conditions"]
    confidence = assess_confidence(confidence_record)

    # dedup_hash
    dedup_hash = compute_dedup_hash(
        element_system=input_record.get("material_name", ""),
        phase=input_record.get("phase"),
        property_name=input_record.get("property", ""),
        method=None,
        source=input_record.get("reference", ""),
    )

    # v4 staging mapping
    staging = v4_record_to_staging(input_record)

    return {
        "parsed_value": parsed_value_out,
        "cleaned_value": cleaned_value,
        "confidence": confidence.value,
        "dedup_hash": dedup_hash,
        "staging": staging,
    }


def regenerate_all_fixtures() -> None:
    """Regenerate all golden fixture expected outputs from current pipeline.

    Call this with: pytest tests/test_golden_regression.py --regenerate
    """
    for fixture in _GOLDEN_FIXTURES:
        source_path = fixture["_source_path"]
        for record in fixture["records"]:
            record["expected"] = _regenerate_golden_output(record["input"])

        # Remove internal metadata before writing
        output_fixture = {
            k: v for k, v in fixture.items() if not k.startswith("_")
        }
        with open(source_path, "w", encoding="utf-8") as f:
            json.dump(output_fixture, f, indent=2, ensure_ascii=False)
            f.write("\n")
