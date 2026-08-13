"""Tests for scripts/check_schema_compat.py schema compatibility gate.

Covers:
  - Baseline generation from current model
  - Detection of all breaking change types (§4.3)
  - Allowing non-breaking changes
  - Strict mode warnings for optional additions
  - CLI exit codes
  - Edge type narrowing detection
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# scripts/ lives at the project root (three levels up from tests/ file).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest
from scripts.check_schema_compat import (
    ChangeSeverity,
    CompatReport,
    SchemaChange,
    _collect_field_paths,
    _get_enum_values,
    _get_required_paths,
    _get_type_name,
    compare_schemas,
)

# ---------------------------------------------------------------------------
# Minimal baseline schema for testing
# ---------------------------------------------------------------------------

_BASELINE_SCHEMA: dict = {
    "type": "object",
    "title": "OntologyGraphResponse",
    "required": ["schema_version", "corpus_id", "generated_at", "source_ontology"],
    "properties": {
        "schema_version": {
            "type": "string",
            "pattern": "^\\d+\\.\\d+(\\.\\d+)?$",
        },
        "corpus_id": {"type": "string"},
        "generated_at": {"type": "string", "format": "date-time"},
        "source_ontology": {"type": "string", "minLength": 1},
        "source_digest": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
        "stats": {
            "type": "object",
            "properties": {
                "nodes": {"type": "integer", "default": 0},
                "relationships": {"type": "integer", "default": 0},
            },
        },
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["class", "individual"],
                    },
                    "name": {"type": "string"},
                },
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "from", "to", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "HAS_PROPERTY",
                            "MEASURED_BY",
                            "CITED_IN",
                            "RELATED_TO",
                        ],
                    },
                },
            },
        },
    },
}


def _make_current(
    *,
    remove_field: str | None = None,
    add_required: str | None = None,
    add_optional: str | None = None,
    change_type: dict | None = None,
    narrow_enum: dict | None = None,
    remove_edge_types: list[str] | None = None,
    new_edge_types: list[str] | None = None,
) -> dict:
    """Create a modified copy of the baseline schema."""
    schema = json.loads(json.dumps(_BASELINE_SCHEMA))

    if remove_field:
        parts = remove_field.split(".")
        if len(parts) == 1:
            schema["properties"].pop(parts[0], None)
            if parts[0] in schema.get("required", []):
                schema["required"].remove(parts[0])

    if add_required:
        schema["properties"][add_required] = {"type": "string"}
        schema.setdefault("required", []).append(add_required)

    if add_optional:
        schema["properties"][add_optional] = {"type": "string"}

    if change_type:
        field_path, new_type = change_type["path"], change_type["type"]
        parts = field_path.split(".")
        if len(parts) == 1 and parts[0] in schema["properties"]:
            schema["properties"][parts[0]] = {"type": new_type}

    if narrow_enum:
        path_parts = narrow_enum["path"].split(".")
        removed = narrow_enum["remove"]
        if len(path_parts) == 2:
            container = schema["properties"][path_parts[0]]
            if "items" in container:
                field_def = container["items"]["properties"][path_parts[1]]
                if "enum" in field_def:
                    field_def["enum"] = [
                        v for v in field_def["enum"] if v not in removed
                    ]
        elif len(path_parts) == 1:
            field_def = schema["properties"][path_parts[0]]
            if "enum" in field_def:
                field_def["enum"] = [
                    v for v in field_def["enum"] if v not in removed
                ]

    if remove_edge_types or new_edge_types:
        rel_items = schema["properties"]["relationships"]["items"]
        current_enum = list(
            rel_items["properties"]["type"]["enum"]
        )
        if remove_edge_types:
            current_enum = [v for v in current_enum if v not in remove_edge_types]
        if new_edge_types:
            current_enum = current_enum + new_edge_types
        rel_items["properties"]["type"]["enum"] = current_enum

    return schema


# ---------------------------------------------------------------------------
# _get_type_name tests
# ---------------------------------------------------------------------------


class TestGetTypeName:
    def test_string_type(self) -> None:
        assert _get_type_name({"type": "string"}) == "string"

    def test_integer_type(self) -> None:
        assert _get_type_name({"type": "integer"}) == "integer"

    def test_any_of(self) -> None:
        assert _get_type_name({"anyOf": []}) == "anyOf"

    def test_ref(self) -> None:
        assert _get_type_name({"$ref": "#/$defs/Foo"}) == "#/$defs/Foo"

    def test_unknown(self) -> None:
        assert _get_type_name({"minLength": 1}) == "unknown"


# ---------------------------------------------------------------------------
# _get_enum_values tests
# ---------------------------------------------------------------------------


class TestGetEnumValues:
    def test_with_enum(self) -> None:
        assert _get_enum_values({"enum": ["a", "b"]}) == ["a", "b"]

    def test_without_enum(self) -> None:
        assert _get_enum_values({"type": "string"}) == []


# ---------------------------------------------------------------------------
# _get_required_paths tests
# ---------------------------------------------------------------------------


class TestGetRequiredPaths:
    def test_top_level_required(self) -> None:
        schema = {
            "required": ["a", "b"],
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
            },
        }
        assert _get_required_paths(schema) == {"a", "b"}

    def test_nested_required(self) -> None:
        schema = {
            "required": ["top"],
            "properties": {
                "top": {
                    "type": "object",
                    "required": ["nested"],
                    "properties": {
                        "nested": {"type": "string"},
                    },
                },
            },
        }
        assert _get_required_paths(schema) == {"top", "top.nested"}


# ---------------------------------------------------------------------------
# _collect_field_paths tests
# ---------------------------------------------------------------------------


class TestCollectFieldPaths:
    def test_flat_schema(self) -> None:
        schema = {
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        paths = _collect_field_paths(schema)
        assert paths.keys() == {"name", "age"}

    def test_nested_schema(self) -> None:
        schema = {
            "properties": {
                "stats": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                    },
                },
            },
        }
        paths = _collect_field_paths(schema)
        assert "stats" in paths
        assert "stats.count" in paths


# ---------------------------------------------------------------------------
# compare_schemas — breaking changes
# ---------------------------------------------------------------------------


class TestRemovedRequiredField:
    def test_blocks_on_removed_required_field(self) -> None:
        current = _make_current(remove_field="corpus_id")
        report = compare_schemas(_BASELINE_SCHEMA, current)

        blocking = [c for c in report.changes if c.severity == ChangeSeverity.BLOCKING]
        assert len(blocking) >= 1
        assert any(c.kind == "removed_required_field" for c in blocking)
        assert report.is_compatible is False


class TestChangedFieldType:
    def test_blocks_on_type_change(self) -> None:
        current = _make_current(
            change_type={"path": "corpus_id", "type": "integer"},
        )
        report = compare_schemas(_BASELINE_SCHEMA, current)

        blocking = [c for c in report.changes if c.severity == ChangeSeverity.BLOCKING]
        assert any(c.kind == "changed_field_type" for c in blocking)
        assert report.is_compatible is False


class TestNarrowedEnum:
    def test_blocks_on_enum_narrowing(self) -> None:
        current = _make_current(
            narrow_enum={"path": "nodes.type", "remove": ["individual"]},
        )
        report = compare_schemas(_BASELINE_SCHEMA, current)

        blocking = [c for c in report.changes if c.severity == ChangeSeverity.BLOCKING]
        assert any(c.kind == "narrowed_enum_values" for c in blocking)
        assert report.is_compatible is False


class TestAddedRequiredField:
    def test_blocks_on_added_required_field(self) -> None:
        current = _make_current(add_required="new_mandatory")
        report = compare_schemas(_BASELINE_SCHEMA, current)

        blocking = [c for c in report.changes if c.severity == ChangeSeverity.BLOCKING]
        assert any(c.kind == "added_required_field" for c in blocking)
        assert report.is_compatible is False


class TestRemovedEdgeTypes:
    def test_blocks_on_removed_edge_type(self) -> None:
        current = _make_current(remove_edge_types=["RELATED_TO"])
        report = compare_schemas(_BASELINE_SCHEMA, current)

        blocking = [c for c in report.changes if c.severity == ChangeSeverity.BLOCKING]
        assert any(c.kind == "removed_edge_types" for c in blocking)
        assert "RELATED_TO" in blocking[0].baseline_value
        assert report.is_compatible is False


# ---------------------------------------------------------------------------
# compare_schemas — allowed changes
# ---------------------------------------------------------------------------


class TestAddedOptionalField:
    def test_allows_added_optional_field(self) -> None:
        current = _make_current(add_optional="new_optional")
        report = compare_schemas(_BASELINE_SCHEMA, current)

        allowed = [c for c in report.changes if c.severity == ChangeSeverity.ALLOWED]
        assert any(c.kind == "added_optional_field" for c in allowed)
        assert report.is_compatible is True


class TestNewEdgeTypes:
    def test_allows_new_edge_types(self) -> None:
        current = _make_current(new_edge_types=["DERIVED_FROM"])
        report = compare_schemas(_BASELINE_SCHEMA, current)

        allowed = [c for c in report.changes if c.severity == ChangeSeverity.ALLOWED]
        assert any(c.kind == "new_edge_types" for c in allowed)
        assert "DERIVED_FROM" in allowed[0].current_value
        assert report.is_compatible is True


# ---------------------------------------------------------------------------
# compare_schemas — strict mode
# ---------------------------------------------------------------------------


class TestStrictMode:
    def test_strict_mode_warns_on_optional_additions(self) -> None:
        current = _make_current(add_optional="new_optional")
        report = compare_schemas(_BASELINE_SCHEMA, current, strict=True)

        warnings = [c for c in report.changes if c.severity == ChangeSeverity.WARNING]
        assert any(c.kind == "added_optional_field" for c in warnings)
        assert report.is_compatible is True


# ---------------------------------------------------------------------------
# compare_schemas — no changes (identical)
# ---------------------------------------------------------------------------


class TestIdenticalSchemas:
    def test_identical_schemas_report_compatible(self) -> None:
        report = compare_schemas(_BASELINE_SCHEMA, _BASELINE_SCHEMA)

        assert report.is_compatible is True
        assert report.blocking_count == 0
        assert len(report.changes) == 0


# ---------------------------------------------------------------------------
# Frozen dataclass tests
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    def test_schema_change_frozen(self) -> None:
        change = SchemaChange(
            path="test", kind="test", severity=ChangeSeverity.BLOCKING,
        )
        with pytest.raises(AttributeError):
            change.path = "modified"  # type: ignore[misc]

    def test_compat_report_frozen(self) -> None:
        report = CompatReport()
        with pytest.raises(AttributeError):
            report.is_compatible = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
_SCRIPT_PATH = os.path.join(_PROJECT_ROOT, "scripts", "check_schema_compat.py")
_SCHEMA_PATH = os.path.join(
    _PROJECT_ROOT,
    "apps", "api", "src", "nfm_db", "schemas", "ontology.py",
)
_PYTHON = os.path.join(
    Path(__file__).resolve().parents[1], ".venv", "bin", "python3",
)


class TestCLI:
    def test_generate_creates_baseline_file(self) -> None:
        """--generate writes a valid JSON schema baseline."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = os.path.join(tmpdir, "test-baseline.json")
            result = subprocess.run(
                [
                    _PYTHON,
                    _SCRIPT_PATH,
                    "--schema",
                    _SCHEMA_PATH,
                    "--generate",
                    "--baseline",
                    baseline_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0
            assert os.path.exists(baseline_path)

            schema = json.loads(Path(baseline_path).read_text())
            assert "properties" in schema
            assert "OntologyGraphResponse" in schema.get("title", "")
            assert "nodes" in schema["properties"]
            assert "relationships" in schema["properties"]

    def test_check_passes_on_fresh_baseline(self) -> None:
        """Check should pass when baseline was just generated."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = os.path.join(tmpdir, "test-baseline.json")

            subprocess.run(
                [
                    _PYTHON,
                    _SCRIPT_PATH,
                    "--schema",
                    _SCHEMA_PATH,
                    "--generate",
                    "--baseline",
                    baseline_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            result = subprocess.run(
                [
                    _PYTHON,
                    _SCRIPT_PATH,
                    "--schema",
                    _SCHEMA_PATH,
                    "--baseline",
                    baseline_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0
            assert "COMPATIBLE" in result.stdout

    def test_check_fails_on_missing_baseline(self) -> None:
        """Exit code 1 when baseline file doesn't exist."""
        import subprocess

        result = subprocess.run(
            [
                _PYTHON,
                _SCRIPT_PATH,
                "--schema",
                _SCHEMA_PATH,
                "--baseline",
                "/nonexistent/baseline.json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 1
        assert "Baseline not found" in result.stderr

    def test_removed_optional_field_is_warning(self) -> None:
        """Removing an optional field is a warning, not blocking."""
        current = _make_current(remove_field="source_digest")
        report = compare_schemas(_BASELINE_SCHEMA, current)

        warnings = [c for c in report.changes if c.severity == ChangeSeverity.WARNING]
        assert any(c.kind == "removed_optional_field" for c in warnings)
        assert report.is_compatible is True


# ---------------------------------------------------------------------------
# Multiple changes in one check
# ---------------------------------------------------------------------------


class TestMultipleChanges:
    def test_reports_all_changes(self) -> None:
        """Multiple simultaneous changes are all reported."""
        current = _make_current(
            add_optional="extra_field",
            remove_edge_types=["RELATED_TO"],
            new_edge_types=["DERIVED_FROM"],
        )
        report = compare_schemas(_BASELINE_SCHEMA, current)

        kinds = {c.kind for c in report.changes}
        assert "removed_edge_types" in kinds
        assert "new_edge_types" in kinds
        assert "added_optional_field" in kinds
        assert report.is_compatible is False
