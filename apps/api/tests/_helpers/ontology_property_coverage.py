"""Neutral loader + coverage classifier for the STANDARD_PROPERTIES audit (NFM-3582).

Kept pytest-free so ``apps/api/scripts/ontology_property_coverage_report.py``
can import the same classifier without dragging ``pytest`` into a runtime
CLI dependency graph. The classifier mirrors the union that
``extraction_prompt._build_ontology_standard_names_block`` consumes:

    property_categories[].standard_properties
    union entity_types[].required_properties

A row is ``mapped`` when the alias resolves to a standard name that
appears in *both* ontology surfaces, ``partial`` when it appears in
exactly one surface, and ``missing`` when it appears in neither.

NFM-3582 stands behind NFM-3580 (C1 fix): with
``STANDARD_PROPERTIES`` reduced to a backward-compat shim that pulls
standard names from the ontology, every legacy alias MUST keep a valid
mapping. Gaps here mean callers silently lose coverage when they
migrate from the alias dictionary to the ontology loader.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixture resolution
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_FIXTURE = (
    _REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "canonical_ontology.json"
)
_FIXTURE_ENV = "NFM_ONTOLOGY_COVERAGE_FIXTURE"


def canonical_fixture_path() -> Path:
    """Return the active canonical-ontology fixture path (env override wins)."""
    override = os.environ.get(_FIXTURE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_FIXTURE


def load_ontology_payload(path: Path | None = None) -> dict:
    """Load the canonical ontology payload from disk.

    Raises ``FileNotFoundError`` if the fixture is missing — callers decide
    whether to skip, fail, or report.
    """
    fixture = path if path is not None else canonical_fixture_path()
    return json.loads(fixture.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Ontology surfaces
# ---------------------------------------------------------------------------


def _iter_property_category_names(payload: Mapping[str, object]) -> Iterable[str]:
    for pc in payload.get("property_categories", []) or []:
        if not isinstance(pc, Mapping):
            continue
        for prop in pc.get("standard_properties", []) or []:
            if isinstance(prop, str) and prop:
                yield prop


def _iter_required_property_names(payload: Mapping[str, object]) -> Iterable[str]:
    for et in payload.get("entity_types", []) or []:
        if not isinstance(et, Mapping):
            continue
        for prop in et.get("required_properties", []) or []:
            if isinstance(prop, str) and prop:
                yield prop


def collect_ontology_property_names(payload: Mapping[str, object]) -> set[str]:
    """Replicate extraction_prompt._build_ontology_standard_names_block input.

    Mirrors the helper at
    ``apps/api/src/nfm_db/services/extraction_prompt.py`` so the coverage
    check tracks the same union of names that the extraction prompt sees.
    """
    return set(_iter_property_category_names(payload)) | set(
        _iter_required_property_names(payload)
    )


def collect_ontology_property_category_names(payload: Mapping[str, object]) -> set[str]:
    """Names contributed by property_categories[].standard_properties only."""
    return set(_iter_property_category_names(payload))


def collect_ontology_required_property_names(payload: Mapping[str, object]) -> set[str]:
    """Names contributed by entity_types[].required_properties only."""
    return set(_iter_required_property_names(payload))


# ---------------------------------------------------------------------------
# Status enum + coverage row
# ---------------------------------------------------------------------------


class CoverageStatus(str, Enum):
    """How an alias resolves against the canonical ontology."""

    MAPPED = "mapped"
    PARTIAL = "partial"
    MISSING = "missing"


@dataclass(frozen=True)
class CoverageRow:
    """One alias (key in STANDARD_PROPERTIES) classified against the ontology."""

    alias: str
    standard_name: str
    status: CoverageStatus
    in_property_categories: bool
    in_entity_required_properties: bool
    remediation: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Per-row classification
# ---------------------------------------------------------------------------


_REMEDIATION_TEMPLATES: dict[CoverageStatus, str] = {
    CoverageStatus.MAPPED: "",
    CoverageStatus.PARTIAL: (
        "Add the standard name to the missing ontology surface "
        "(property_categories[].standard_properties or "
        "entity_types[].required_properties) so the alias is reachable "
        "from both the categories block and the standard names block."
    ),
    CoverageStatus.MISSING: (
        "Add a new ontology entry whose standard name equals "
        "'{standard_name}' — either a row under property_categories or "
        "under an entity_type's required_properties — so the alias "
        "resolves through the ontology loader."
    ),
}


def classify_row(
    alias: str,
    standard_name: str,
    payload: Mapping[str, object],
) -> CoverageRow:
    """Classify a single alias by where its standard_name appears in the ontology."""
    in_categories = standard_name in collect_ontology_property_category_names(payload)
    in_required = standard_name in collect_ontology_required_property_names(payload)

    if in_categories and in_required:
        status = CoverageStatus.MAPPED
    elif in_categories or in_required:
        status = CoverageStatus.PARTIAL
    else:
        status = CoverageStatus.MISSING

    remediation = _REMEDIATION_TEMPLATES[status].format(standard_name=standard_name)
    return CoverageRow(
        alias=alias,
        standard_name=standard_name,
        status=status,
        in_property_categories=in_categories,
        in_entity_required_properties=in_required,
        remediation=remediation,
    )


# ---------------------------------------------------------------------------
# Full-table classifier
# ---------------------------------------------------------------------------


def compute_coverage_rows(
    standard_properties: Mapping[str, str],
    payload: Mapping[str, object],
) -> list[CoverageRow]:
    """Classify every alias in STANDARD_PROPERTIES against the ontology."""
    return [
        classify_row(alias, std_name, payload)
        for alias, std_name in sorted(standard_properties.items())
    ]


def coverage_metrics(rows: Iterable[CoverageRow]) -> dict[str, object]:
    """Aggregate per-row classifications into pass/fail metrics."""
    rows_list = list(rows)
    total = len(rows_list)
    by_status: dict[str, int] = {s.value: 0 for s in CoverageStatus}
    missing_standard_names: list[str] = []
    partial_standard_names: list[str] = []

    for row in rows_list:
        by_status[row.status.value] += 1
        if row.status is CoverageStatus.MISSING:
            missing_standard_names.append(row.standard_name)
        elif row.status is CoverageStatus.PARTIAL:
            partial_standard_names.append(row.standard_name)

    unique_standard_names_total = len({row.standard_name for row in rows_list})
    covered_standard_names = {
        row.standard_name
        for row in rows_list
        if row.status is CoverageStatus.MAPPED
    }

    return {
        "total_aliases": total,
        "unique_standard_names": unique_standard_names_total,
        "covered_standard_names": len(covered_standard_names),
        "by_status": by_status,
        "missing_standard_names": sorted(set(missing_standard_names)),
        "partial_standard_names": sorted(set(partial_standard_names)),
        "alias_pct": (100.0 * by_status[CoverageStatus.MAPPED.value] / total)
        if total
        else 0.0,
        "standard_name_pct": (
            100.0 * len(covered_standard_names) / unique_standard_names_total
        )
        if unique_standard_names_total
        else 0.0,
        "gate_passed": (
            by_status[CoverageStatus.PARTIAL.value] == 0
            and by_status[CoverageStatus.MISSING.value] == 0
        ),
    }
