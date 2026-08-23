"""Ontology coverage gate for ``property_catalog.STANDARD_PROPERTIES`` (NFM-3582).

For every alias in
``apps.api.src.nfm_db.core.property_catalog.STANDARD_PROPERTIES`` the test
asserts that the alias's resolved standard name appears in the canonical
ontology payload — the same data shape that
``extraction_prompt._build_ontology_standard_names_block`` consumes
(``property_categories[].standard_properties`` union
``entity_types[].required_properties``).

The test is intentionally a *gate*: it fails with a multi-line diff
listing every uncovered alias until NFM-3531-C (or a follow-up) populates
``tests/fixtures/canonical_ontology.json`` with the full 11-category /
~74-property catalog. The companion CLI
``scripts/ontology_property_coverage_report.py`` produces the markdown
report checked in under ``docs/verification/NFM-3582-coverage.md``.

The fixture path is overridable via ``NFM_ONTOLOGY_COVERAGE_FIXTURE`` so
CI / dev environments can point at a published ontology snapshot
(DB-derived or migration-seeded) without touching this file.
"""

from __future__ import annotations

import pytest

from nfm_db.core.property_catalog import STANDARD_PROPERTIES
from tests._helpers.ontology_property_coverage import (
    CoverageStatus,
    canonical_fixture_path,
    collect_ontology_property_names,
    compute_coverage_rows,
    coverage_metrics,
    load_ontology_payload,
)


def _load_or_skip() -> dict:
    """Load the canonical ontology payload, skipping if the fixture is absent."""
    path = canonical_fixture_path()
    if not path.exists():
        pytest.skip(f"Canonical ontology fixture not found at {path}")
    try:
        return load_ontology_payload(path)
    except ValueError as exc:
        pytest.fail(f"Canonical ontology fixture at {path} is not valid JSON: {exc}")


def _render_uncovered_diff(uncovered: list) -> str:
    """Render a multi-line diff of every uncovered alias for the gate failure."""
    if not uncovered:
        return ""
    lines = ["", "Uncovered STANDARD_PROPERTIES aliases (NFM-3582):"]
    lines.append(f"  total uncovered: {len(uncovered)}")
    by_status: dict[str, list] = {s.value: [] for s in CoverageStatus}
    for row in uncovered:
        by_status[row.status.value].append(row)

    for status in (CoverageStatus.MISSING, CoverageStatus.PARTIAL):
        bucket = by_status[status.value]
        if not bucket:
            continue
        lines.append("")
        lines.append(f"  [{status.value}] {len(bucket)} alias(es):")
        for row in bucket[:50]:
            surfaces = []
            if row.in_property_categories:
                surfaces.append("property_categories")
            if row.in_entity_required_properties:
                surfaces.append("entity_required_properties")
            surface_note = (
                f" (in: {', '.join(surfaces)})" if surfaces else " (in: <none>)"
            )
            lines.append(
                f"    - alias={row.alias!r} -> standard_name={row.standard_name!r}"
                f"{surface_note}"
            )
            lines.append(f"        remediation: {row.remediation}")
        if len(bucket) > 50:
            lines.append(f"    ... +{len(bucket) - 50} more")
    return "\n".join(lines)


def test_every_standard_property_alias_resolves_through_canonical_ontology() -> None:
    """Every alias in STANDARD_PROPERTIES MUST resolve through the ontology.

    Failure mode: ``pytest.fail()`` with a multi-line diff listing every
    uncovered alias so engineering can grep the list straight into a
    follow-up patch.
    """
    payload = _load_or_skip()
    rows = compute_coverage_rows(STANDARD_PROPERTIES, payload)
    metrics = coverage_metrics(rows)

    uncovered = [row for row in rows if row.status is not CoverageStatus.MAPPED]
    if not uncovered:
        return

    summary = (
        f"Ontology coverage gate FAILED — {metrics['by_status']['partial']} partial, "
        f"{metrics['by_status']['missing']} missing out of "
        f"{metrics['total_aliases']} aliases ({metrics['unique_standard_names']} "
        f"unique standard names). Populate "
        f"apps/api/tests/fixtures/canonical_ontology.json so every "
        f"STANDARD_PROPERTIES alias resolves through "
        f"property_categories[].standard_properties or "
        f"entity_types[].required_properties."
    )
    pytest.fail(summary + _render_uncovered_diff(uncovered))


def test_coverage_metrics_aggregate_status_counts() -> None:
    """coverage_metrics() must total aliases and split by status."""
    payload = _load_or_skip()
    rows = compute_coverage_rows(STANDARD_PROPERTIES, payload)
    metrics = coverage_metrics(rows)

    assert metrics["total_aliases"] == len(STANDARD_PROPERTIES)
    assert metrics["unique_standard_names"] == len(
        {STANDARD_PROPERTIES[k] for k in STANDARD_PROPERTIES}
    )

    bucket_sum = sum(metrics["by_status"].values())
    assert bucket_sum == metrics["total_aliases"], (
        "by_status counts must sum to total_aliases; "
        f"got {bucket_sum} vs {metrics['total_aliases']}"
    )

    assert metrics["alias_pct"] == pytest.approx(
        100.0 * metrics["by_status"]["mapped"] / metrics["total_aliases"]
    )
    assert metrics["standard_name_pct"] == pytest.approx(
        100.0
        * metrics["covered_standard_names"]
        / max(metrics["unique_standard_names"], 1)
    )


def test_partial_and_missing_rows_carry_remediation() -> None:
    """Every non-mapped row MUST include a remediation suggestion."""
    payload = _load_or_skip()
    rows = compute_coverage_rows(STANDARD_PROPERTIES, payload)

    for row in rows:
        if row.status is CoverageStatus.MAPPED:
            assert row.remediation == "", (
                f"mapped row {row.alias!r} should have empty remediation"
            )
        else:
            assert row.remediation, (
                f"{row.status.value} row {row.alias!r} ({row.standard_name!r}) "
                "is missing a remediation suggestion"
            )
            assert row.standard_name in row.remediation, (
                "remediation must reference the missing standard_name so "
                "a human can grep straight into a fix"
            )


def test_classifier_distinguishes_property_category_and_required_property() -> None:
    """A name only in required_properties must read as 'partial', not 'mapped'."""
    from tests._helpers.ontology_property_coverage import classify_row

    payload = {
        "property_categories": [],
        "entity_types": [
            {"name": "Sample", "required_properties": ["unique-required-name"]}
        ],
    }
    row = classify_row("alias-x", "unique-required-name", payload)
    assert row.status is CoverageStatus.PARTIAL
    assert row.in_entity_required_properties is True
    assert row.in_property_categories is False

    payload_both = {
        "property_categories": [
            {"name": "密度", "standard_properties": ["unique-both-name"]}
        ],
        "entity_types": [
            {"name": "Sample", "required_properties": ["unique-both-name"]}
        ],
    }
    row_both = classify_row("alias-y", "unique-both-name", payload_both)
    assert row_both.status is CoverageStatus.MAPPED
    assert row_both.in_property_categories is True
    assert row_both.in_entity_required_properties is True

    payload_neither = {
        "property_categories": [{"name": "密度", "standard_properties": ["别的"]}],
        "entity_types": [],
    }
    row_neither = classify_row("alias-z", "totally-unknown", payload_neither)
    assert row_neither.status is CoverageStatus.MISSING
    assert row_neither.remediation  # present


def test_helper_matches_extraction_prompt_ontology_union() -> None:
    """collect_ontology_property_names() must mirror extraction_prompt union logic."""
    payload = {
        "property_categories": [
            {"name": "CatA", "standard_properties": ["alpha", "beta"]},
            {"name": "CatB", "standard_properties": ["gamma"]},
        ],
        "entity_types": [
            {"name": "EtA", "required_properties": ["delta", "beta"]},
        ],
        "relation_types": [],
    }
    assert collect_ontology_property_names(payload) == {"alpha", "beta", "gamma", "delta"}
