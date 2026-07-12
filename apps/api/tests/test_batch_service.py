"""Unit tests for batch import/export service (NFM-1085).

Tests CSV/JSON parsing, validation, summary generation, and export
serialization — all without touching the database.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nfm_db.schemas.batch import (
    BatchImportResult,
    BatchRowError,
)

# ── CSV parsing tests ──────────────────────────────────────────────


class TestParseCsvContent:
    """Tests for parse_csv_content function."""

    def test_valid_csv_parses_to_row_dicts(self) -> None:
        content = b"name,formula,crystal_structure\ncubic,UO2,FCC\nBCC,Fe,BCC\n"
        from nfm_db.services.batch_service import parse_csv_content

        rows = parse_csv_content(content)
        assert len(rows) == 2
        assert rows[0]["name"] == "cubic"
        assert rows[0]["formula"] == "UO2"
        assert rows[1]["name"] == "BCC"

    def test_csv_strips_bom(self) -> None:
        content = b"\xef\xbb\xbfname,formula\nUO2,UO2\n"
        from nfm_db.services.batch_service import parse_csv_content

        rows = parse_csv_content(content)
        assert rows[0]["name"] == "UO2"

    def test_csv_skips_empty_rows(self) -> None:
        content = b"name,formula\nUO2,UO2\n,\n\nFe,Fe2\n"
        from nfm_db.services.batch_service import parse_csv_content

        rows = parse_csv_content(content)
        assert len(rows) == 2

    def test_csv_strips_whitespace(self) -> None:
        content = b"name,formula\ncubic,UO2\nBCC,Fe\n"
        from nfm_db.services.batch_service import parse_csv_content

        rows = parse_csv_content(content)
        assert rows[0]["name"] == "cubic"
        assert rows[0]["formula"] == "UO2"
        assert rows[1]["name"] == "BCC"

    def test_csv_empty_content_returns_empty_list(self) -> None:
        content = b"name,formula\n"
        from nfm_db.services.batch_service import parse_csv_content

        rows = parse_csv_content(content)
        assert rows == []


# ── JSON parsing tests ─────────────────────────────────────────────


class TestParseJsonContent:
    """Tests for parse_json_content function."""

    def test_valid_json_array(self) -> None:
        content = json.dumps([{"name": "UO2"}, {"name": "Fe"}]).encode()
        from nfm_db.services.batch_service import parse_json_content

        rows = parse_json_content(content)
        assert len(rows) == 2
        assert rows[0]["name"] == "UO2"

    def test_json_must_be_array(self) -> None:
        content = b'{"name": "UO2"}'
        from nfm_db.services.batch_service import parse_json_content

        with pytest.raises(ValueError, match="array"):
            parse_json_content(content)

    def test_json_empty_array(self) -> None:
        content = b"[]"
        from nfm_db.services.batch_service import parse_json_content

        rows = parse_json_content(content)
        assert rows == []


# ── Row validation tests ───────────────────────────────────────────


class TestValidateMaterialRow:
    """Tests for validate_material_row function."""

    def test_valid_row_returns_material_create(self) -> None:
        from nfm_db.services.batch_service import validate_material_row

        result, error = validate_material_row({"name": "UO2", "formula": "UO2"})
        assert result is not None
        assert result.name == "UO2"
        assert error is None

    def test_missing_name_returns_error(self) -> None:
        from nfm_db.services.batch_service import validate_material_row

        result, error = validate_material_row({"formula": "UO2"})
        assert result is None
        assert error is not None
        assert error.field == "name"

    def test_is_active_true_coerced(self) -> None:
        from nfm_db.services.batch_service import validate_material_row

        result, _ = validate_material_row({"name": "UO2", "is_active": "true"})
        assert result is not None
        assert result.is_active is True

    def test_is_active_false_coerced(self) -> None:
        from nfm_db.services.batch_service import validate_material_row

        result, _ = validate_material_row({"name": "UO2", "is_active": "0"})
        assert result is not None
        assert result.is_active is False

    def test_invalid_boolean_returns_error(self) -> None:
        from nfm_db.services.batch_service import validate_material_row

        result, error = validate_material_row({"name": "UO2", "is_active": "maybe"})
        assert result is None
        assert error is not None
        assert "boolean" in error.message.lower()

    def test_long_name_exceeds_limit(self) -> None:
        from nfm_db.services.batch_service import validate_material_row

        result, error = validate_material_row({"name": "x" * 501})
        assert result is None
        assert error is not None


# ── Reference value row validation ──────────────────────────────────


class TestValidateReferenceValueRow:
    """Tests for validate_reference_value_row function."""

    def test_valid_row(self) -> None:
        from nfm_db.services.batch_service import validate_reference_value_row

        row = {
            "element_system": "UO2",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "source": "Smirnov2014",
        }
        result, error = validate_reference_value_row(row, 1)
        assert result is not None
        assert error is None

    def test_missing_element_system(self) -> None:
        from nfm_db.services.batch_service import validate_reference_value_row

        row = {
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "source": "Smirnov2014",
        }
        result, error = validate_reference_value_row(row, 1)
        assert result is None
        assert error is not None
        assert "element_system" in error.field

    def test_missing_source(self) -> None:
        from nfm_db.services.batch_service import validate_reference_value_row

        row = {
            "element_system": "UO2",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
        }
        result, error = validate_reference_value_row(row, 1)
        assert result is None
        assert error is not None


# ── Export serialization tests ────────────────────────────────────


class TestExportSerializers:
    """Tests for CSV and JSON export serialization."""

    def test_materials_to_csv(self) -> None:
        from nfm_db.services.batch_service import serialize_materials_to_csv

        rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "UO2",
                "formula": "UO2",
                "crystal_structure": "FCC",
                "category_id": None,
                "description": None,
                "is_active": True,
            },
        ]
        output = serialize_materials_to_csv(rows)
        reader = csv.DictReader(io.StringIO(output))
        parsed = list(reader)
        assert len(parsed) == 1
        assert parsed[0]["name"] == "UO2"
        assert parsed[0]["formula"] == "UO2"

    def test_materials_to_json(self) -> None:
        from nfm_db.services.batch_service import serialize_materials_to_json

        rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "UO2",
                "formula": "UO2",
            },
        ]
        output = serialize_materials_to_json(rows)
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["name"] == "UO2"

    def test_reference_values_to_csv(self) -> None:
        from nfm_db.services.batch_service import serialize_reference_values_to_csv

        rows = [
            {
                "element_system": "UO2",
                "phase": None,
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
                "source": "Smirnov2014",
                "source_doi": None,
                "uncertainty": None,
                "temperature": None,
            },
        ]
        output = serialize_reference_values_to_csv(rows)
        reader = csv.DictReader(io.StringIO(output))
        parsed = list(reader)
        assert len(parsed) == 1
        assert parsed[0]["element_system"] == "UO2"

    def test_reference_values_to_json(self) -> None:
        from nfm_db.services.batch_service import serialize_reference_values_to_json

        rows = [
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "source": "Smirnov2014",
            },
        ]
        output = serialize_reference_values_to_json(rows)
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["element_system"] == "UO2"

    def test_properties_to_csv(self) -> None:
        from nfm_db.services.batch_service import serialize_properties_to_csv

        rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "dataset_id": "00000000-0000-0000-0000-000000000002",
                "property_type_id": "00000000-0000-0000-0000-000000000003",
                "value_scalar": 5.47,
                "value_min": None,
                "value_max": None,
                "uncertainty": None,
                "unit_id": None,
                "notes": None,
            },
        ]
        output = serialize_properties_to_csv(rows)
        reader = csv.DictReader(io.StringIO(output))
        parsed = list(reader)
        assert len(parsed) == 1
        assert float(parsed[0]["value_scalar"]) == pytest.approx(5.47)

    def test_properties_to_json(self) -> None:
        from nfm_db.services.batch_service import serialize_properties_to_json

        rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "value_scalar": 5.47,
            },
        ]
        output = serialize_properties_to_json(rows)
        data = json.loads(output)
        assert len(data) == 1


# ── Export filename generation ─────────────────────────────────────


class TestExportFilename:
    """Tests for generate_export_filename function."""

    def test_materials_csv_filename(self) -> None:
        from nfm_db.services.batch_service import generate_export_filename

        now = datetime(2026, 7, 11, tzinfo=ZoneInfo("UTC"))
        filename = generate_export_filename("materials", "csv", now)
        assert filename == "materials_20260711.csv"

    def test_properties_json_filename(self) -> None:
        from nfm_db.services.batch_service import generate_export_filename

        now = datetime(2026, 7, 11, tzinfo=ZoneInfo("UTC"))
        filename = generate_export_filename("properties", "json", now)
        assert filename == "properties_20260711.json"

    def test_reference_values_filename(self) -> None:
        from nfm_db.services.batch_service import generate_export_filename

        now = datetime(2026, 7, 11, tzinfo=ZoneInfo("UTC"))
        filename = generate_export_filename("reference_values", "csv", now)
        assert filename == "reference_values_20260711.csv"


# ── Batch import result tests ──────────────────────────────────────


class TestBatchImportResult:
    """Tests for BatchImportResult schema."""

    def test_default_values(self) -> None:
        result = BatchImportResult()
        assert result.imported == 0
        assert result.skipped == 0
        assert result.failed == 0
        assert result.errors == []

    def test_with_values(self) -> None:
        errors = [BatchRowError(row=3, field="name", message="Missing")]
        result = BatchImportResult(imported=5, skipped=1, failed=2, errors=errors)
        assert result.imported == 5
        assert result.skipped == 1
        assert result.failed == 2
        assert len(result.errors) == 1

    def test_json_serialization(self) -> None:
        result = BatchImportResult(imported=10, skipped=0, failed=1)
        data = result.model_dump()
        assert "imported" in data
        assert "skipped" in data
