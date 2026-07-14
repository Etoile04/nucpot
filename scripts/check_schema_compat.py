"""CI schema compatibility gate for OntologyGraphResponse.

Validates that the current ``OntologyGraphResponse`` Pydantic model has not
introduced breaking changes relative to a stored JSON schema baseline.

Breaking changes (per CTO Spec §4.3):

  - Removed required fields      → BLOCK
  - Changed field types          → BLOCK
  - Narrowed enum values         → BLOCK
  - Added required fields        → BLOCK
  - Removed edge (relationship) types → BLOCK

Allowed changes:

  - Added optional fields        → ALLOW
  - New edge types               → ALLOW

Usage::

  # Generate baseline from current model
  python scripts/check_schema_compat.py --generate --baseline .schema-baseline.json

  # Check compatibility against baseline
  python scripts/check_schema_compat.py --schema apps/api/src/nfm_db/schemas/ontology.py --baseline .schema-baseline.json

  # Strict mode: treat added optional fields as warnings
  python scripts/check_schema_compat.py --schema apps/api/src/nfm_db/schemas/ontology.py --baseline .schema-baseline.json --strict
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ChangeSeverity(Enum):
    BLOCKING = "BLOCKING"
    ALLOWED = "ALLOWED"
    WARNING = "WARNING"


@dataclass(frozen=True)
class SchemaChange:
    """A single schema diff entry."""

    path: str
    kind: str
    severity: ChangeSeverity
    baseline_value: str = ""
    current_value: str = ""
    detail: str = ""


@dataclass(frozen=True)
class CompatReport:
    """Full compatibility report."""

    changes: tuple[SchemaChange, ...] = ()
    blocking_count: int = 0
    allowed_count: int = 0
    warning_count: int = 0
    is_compatible: bool = True


# ---------------------------------------------------------------------------
# Pydantic model → JSON Schema
# ---------------------------------------------------------------------------


def _import_model_class(schema_path: str) -> type:
    """Import the OntologyGraphResponse class from the given module path."""
    module_path = str(Path(schema_path).parent)
    module_name = Path(schema_path).stem

    if module_path not in sys.path:
        sys.path.insert(0, module_path)

    import importlib

    module = importlib.import_module(module_name)

    cls = getattr(module, "OntologyGraphResponse", None)
    if cls is None:
        print(
            f"ERROR: OntologyGraphResponse not found in {schema_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    return cls


def generate_schema_from_model(model_class: type) -> dict[str, Any]:
    """Generate a JSON Schema dict from a Pydantic model class."""
    try:
        from pydantic_core import to_json_schema

        return to_json_schema(model_class)
    except ImportError:
        pass

    try:
        return model_class.model_json_schema()
    except AttributeError:
        pass

    return model_class.schema()


# ---------------------------------------------------------------------------
# Schema comparison
# ---------------------------------------------------------------------------


def _get_type_name(schema: dict[str, Any]) -> str:
    """Extract a human-readable type name from a JSON Schema type entry."""
    if "type" in schema:
        return str(schema["type"])
    if "anyOf" in schema:
        return "anyOf"
    if "allOf" in schema:
        return "allOf"
    if "$ref" in schema:
        return schema["$ref"]
    return "unknown"


def _get_enum_values(schema: dict[str, Any]) -> list[str]:
    """Extract enum values from a schema entry."""
    return list(schema.get("enum", []))


def _collect_field_paths(
    schema: dict[str, Any],
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    """Recursively collect all field paths from a JSON Schema."""
    fields: dict[str, dict[str, Any]] = {}
    props = schema.get("properties", {})

    for name, prop_schema in props.items():
        path = f"{prefix}.{name}" if prefix else name
        fields[path] = prop_schema

        # Recurse into nested objects (type=object or explicit properties)
        if prop_schema.get("type") == "object" or "properties" in prop_schema:
            nested = _collect_field_paths(prop_schema, path)
            fields.update(nested)

        # Recurse into array items (type=array with items.properties)
        if prop_schema.get("type") == "array" and "items" in prop_schema:
            items = prop_schema["items"]
            if isinstance(items, dict) and "properties" in items:
                nested = _collect_field_paths(items, path)
                fields.update(nested)

    return fields


def _get_required_paths(schema: dict[str, Any]) -> set[str]:
    """Extract all required field paths from a JSON Schema."""
    required: set[str] = set()
    _required_recursive(schema, "", required)
    return required


def _required_recursive(
    schema: dict[str, Any],
    prefix: str,
    required: set[str],
) -> None:
    """Recursively collect required paths."""
    for field_name in schema.get("required", []):
        path = f"{prefix}.{field_name}" if prefix else field_name
        required.add(path)

    props = schema.get("properties", {})
    for name, prop_schema in props.items():
        if prop_schema.get("type") == "object" or "properties" in prop_schema:
            path = f"{prefix}.{name}" if prefix else name
            _required_recursive(prop_schema, path, required)


def _check_edge_types(
    baseline: dict[str, Any],
    current: dict[str, Any],
    changes: list[SchemaChange],
) -> None:
    """Check if relationship edge types have been narrowed or removed."""
    baseline_rel = (
        baseline.get("properties", {}).get("relationships", {}).get("items", {})
    )
    current_rel = (
        current.get("properties", {}).get("relationships", {}).get("items", {})
    )

    baseline_rel_props = baseline_rel.get("properties", {})
    current_rel_props = current_rel.get("properties", {})

    type_field = "type"
    if type_field in baseline_rel_props and type_field in current_rel_props:
        baseline_enum = set(_get_enum_values(baseline_rel_props[type_field]))
        current_enum = set(_get_enum_values(current_rel_props[type_field]))

        if baseline_enum and current_enum:
            removed = baseline_enum - current_enum
            if removed:
                changes.append(
                    SchemaChange(
                        path="relationships.type",
                        kind="removed_edge_types",
                        severity=ChangeSeverity.BLOCKING,
                        baseline_value=str(sorted(baseline_enum)),
                        current_value=str(sorted(current_enum)),
                        detail=f"Edge types removed: {sorted(removed)}",
                    )
                )

            added = current_enum - baseline_enum
            if added:
                changes.append(
                    SchemaChange(
                        path="relationships.type",
                        kind="new_edge_types",
                        severity=ChangeSeverity.ALLOWED,
                        current_value=str(sorted(added)),
                        detail=f"New edge types added: {sorted(added)}",
                    )
                )


def compare_schemas(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    strict: bool = False,
) -> CompatReport:
    """Compare two JSON schemas and detect breaking changes.

    Args:
        baseline: The stored baseline schema.
        current: The current model schema.
        strict: Treat added optional fields as warnings.

    Returns:
        A CompatReport with all detected changes.
    """
    changes: list[SchemaChange] = []

    baseline_fields = _collect_field_paths(baseline)
    current_fields = _collect_field_paths(current)

    baseline_keys = set(baseline_fields.keys())
    current_keys = set(current_fields.keys())

    # --- Removed fields ---
    for path in sorted(baseline_keys - current_keys):
        was_required = path in _get_required_paths(baseline)
        if was_required:
            changes.append(
                SchemaChange(
                    path=path,
                    kind="removed_required_field",
                    severity=ChangeSeverity.BLOCKING,
                    detail=f"Required field '{path}' was removed",
                )
            )
        else:
            changes.append(
                SchemaChange(
                    path=path,
                    kind="removed_optional_field",
                    severity=ChangeSeverity.WARNING,
                    detail=f"Optional field '{path}' was removed",
                )
            )

    # --- Added fields ---
    for path in sorted(current_keys - baseline_keys):
        is_required = path in _get_required_paths(current)
        if is_required:
            changes.append(
                SchemaChange(
                    path=path,
                    kind="added_required_field",
                    severity=ChangeSeverity.BLOCKING,
                    detail=f"New required field '{path}' blocks consumers",
                )
            )
        elif strict:
            changes.append(
                SchemaChange(
                    path=path,
                    kind="added_optional_field",
                    severity=ChangeSeverity.WARNING,
                    detail=f"New optional field '{path}' (strict mode)",
                )
            )
        else:
            changes.append(
                SchemaChange(
                    path=path,
                    kind="added_optional_field",
                    severity=ChangeSeverity.ALLOWED,
                    detail=f"New optional field '{path}'",
                )
            )

    # --- Changed fields (type + enum changes) ---
    for path in sorted(baseline_keys & current_keys):
        baseline_field = baseline_fields[path]
        current_field = current_fields[path]

        baseline_type = _get_type_name(baseline_field)
        current_type = _get_type_name(current_field)

        if baseline_type != current_type:
            changes.append(
                SchemaChange(
                    path=path,
                    kind="changed_field_type",
                    severity=ChangeSeverity.BLOCKING,
                    baseline_value=baseline_type,
                    current_value=current_type,
                    detail=(
                        f"Field '{path}' type changed from "
                        f"{baseline_type} to {current_type}"
                    ),
                )
            )

        baseline_enums = set(_get_enum_values(baseline_field))
        current_enums = set(_get_enum_values(current_field))

        if baseline_enums and current_enums:
            removed_enums = baseline_enums - current_enums
            if removed_enums:
                changes.append(
                    SchemaChange(
                        path=path,
                        kind="narrowed_enum_values",
                        severity=ChangeSeverity.BLOCKING,
                        baseline_value=str(sorted(baseline_enums)),
                        current_value=str(sorted(current_enums)),
                        detail=(
                            f"Enum values removed from '{path}': "
                            f"{sorted(removed_enums)}"
                        ),
                    )
                )

    # --- Check for removed edge types in relationships ---
    _check_edge_types(baseline, current, changes)

    blocking = sum(1 for c in changes if c.severity == ChangeSeverity.BLOCKING)
    allowed = sum(1 for c in changes if c.severity == ChangeSeverity.ALLOWED)
    warnings = sum(1 for c in changes if c.severity == ChangeSeverity.WARNING)

    return CompatReport(
        changes=tuple(changes),
        blocking_count=blocking,
        allowed_count=allowed,
        warning_count=warnings,
        is_compatible=(blocking == 0),
    )


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------


def _print_report(report: CompatReport) -> None:
    """Print the compatibility report to stdout."""
    for change in report.changes:
        severity = change.severity.value
        icon = {"BLOCKING": "✗", "ALLOWED": "✓", "WARNING": "⚠"}[severity]
        print(f"  {icon} [{severity}] {change.kind}: {change.detail}")
        if change.baseline_value:
            print(f"    baseline: {change.baseline_value}")
        if change.current_value:
            print(f"    current:  {change.current_value}")

    print()
    print(
        f"Summary: {report.blocking_count} blocking, "
        f"{report.allowed_count} allowed, "
        f"{report.warning_count} warnings"
    )

    if report.is_compatible:
        print("Result: COMPATIBLE ✓")
    else:
        print("Result: INCOMPATIBLE ✗")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check OntologyGraphResponse schema backward compatibility",
    )
    parser.add_argument(
        "--schema",
        help="Path to ontology.py schema module",
        default="apps/api/src/nfm_db/schemas/ontology.py",
    )
    parser.add_argument(
        "--baseline",
        help="Path to .schema-baseline.json",
        default=".schema-baseline.json",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate baseline from current model and exit",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat added optional fields as warnings",
    )
    args = parser.parse_args()

    model_class = _import_model_class(args.schema)
    current_schema = generate_schema_from_model(model_class)

    if args.generate:
        output_path = Path(args.baseline)
        output_path.write_text(
            json.dumps(current_schema, indent=2, sort_keys=True) + "\n",
        )
        print(f"Generated baseline: {output_path}")
        return 0

    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(
            f"ERROR: Baseline not found at {baseline_path}",
            file=sys.stderr,
        )
        print(
            "Run with --generate to create the initial baseline.",
            file=sys.stderr,
        )
        return 1

    baseline_schema = json.loads(baseline_path.read_text())

    report = compare_schemas(
        baseline_schema,
        current_schema,
        strict=args.strict,
    )

    _print_report(report)

    return 0 if report.is_compatible else 1


if __name__ == "__main__":
    sys.exit(main())
