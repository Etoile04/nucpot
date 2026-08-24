"""Ontology coverage gate for ``property_catalog.STANDARD_PROPERTIES`` (NFM-3531-A).

Iterates every alias key in
``apps.api.src.nfm_db.core.property_catalog.STANDARD_PROPERTIES`` and asserts
that each one resolves through the canonical ontology payload — the same
data shape that ``extraction_prompt._build_ontology_standard_names_block``
consumes (``property_categories[].standard_properties`` union
``entity_types[].required_properties``).

The test is intentionally a *gate*: it fails with a clear diff listing every
uncovered alias until NFM-3531-C (or later) populates
``tests/fixtures/canonical_ontology.json`` with the full 11-category /
~74-property catalog. The diff drives the team to fill gaps; the
``scripts/ontology_coverage_report.py`` companion CLI produces the markdown
report checked in under ``docs/verification/NFM-3531-coverage.md``.

The fixture path is overridable via ``NFM_ONTOLOGY_COVERAGE_FIXTURE`` so
CI / dev environments can point at a published ontology snapshot
(DB-derived or migration-seeded) without touching this file.
"""

from __future__ import annotations

import pytest

from nfm_db.core.property_catalog import STANDARD_PROPERTIES
from tests._helpers.ontology_coverage import (
    canonical_fixture_path,
    collect_ontology_property_names,
    coverage_metrics,
    load_ontology_payload,
)


def _load_or_skip() -> dict:
    """Load the canonical ontology payload, skipping if the fixture is absent.

    The loader is a deployment-time artifact — a missing file should not
    produce a confusing ``FileNotFoundError`` during coverage evaluation.
    """
    path = canonical_fixture_path()
    if not path.exists():
        pytest.skip(f"Canonical ontology fixture not found at {path}")
    try:
        return load_ontology_payload(path)
    except ValueError as exc:
        pytest.fail(f"Canonical ontology fixture at {path} is not valid JSON: {exc}")


def test_every_standard_property_key_resolves_through_canonical_ontology() -> None:
    """Every alias in STANDARD_PROPERTIES MUST appear in the canonical ontology.

    Failure mode: pytest.fail() with a multi-line diff listing every
    uncovered key so the engineering team can grep the list straight into
    the NFM-3531-C patch.
    """
    payload = _load_or_skip()
    ontology_names = collect_ontology_property_names(payload)
    metrics = coverage_metrics(STANDARD_PROPERTIES.keys(), ontology_names)

    if metrics["missing_count"]:
        sample = metrics["missing"][:25]
        sample_block = "\n".join(f"  - {name!r}" for name in sample)
        suffix = (
            f"\n  ... and {metrics['missing_count'] - len(sample)} more"
            if metrics["missing_count"] > len(sample)
            else ""
        )
        pytest.fail(
            "Ontology coverage gate failed for STANDARD_PROPERTIES.\n"
            f"  total:     {metrics['total']}\n"
            f"  covered:   {metrics['covered_count']} ({metrics['pct']:.1f}%)\n"
            f"  missing:   {metrics['missing_count']}\n"
            f"  fixture:   {canonical_fixture_path()}\n"
            "  first missing keys:\n"
            f"{sample_block}{suffix}"
        )


def test_canonical_ontology_has_property_categories_block() -> None:
    """Sanity check — ontology 0.2.0+ must declare the property_categories block.

    Catches the regression where the property_categories key is dropped or
    renamed; without it, ``_build_ontology_standard_names_block`` silently
    falls back to entity-types-only and the coverage gate above stops
    reflecting reality.
    """
    payload = _load_or_skip()
    assert "property_categories" in payload, (
        "Canonical ontology payload must declare 'property_categories' "
        "(NFM-3531-A gate; extraction_prompt._build_ontology_standard_names_block "
        "reads this key directly)."
    )
    assert isinstance(payload["property_categories"], list), (
        "'property_categories' must be a list of category dicts, got "
        f"{type(payload['property_categories']).__name__}"
    )


def test_canonical_ontology_payload_is_well_formed() -> None:
    """Structural sanity check on the fixture.

    Catches malformed entries before they silently break the coverage
    gate. Mirrors the loader's defensive isinstance checks so any future
    fixture edit that breaks the shape fails here instead of in
    ``collect_ontology_property_names``.
    """
    payload = _load_or_skip()
    for idx, pc in enumerate(payload.get("property_categories", []) or []):
        assert isinstance(pc, dict), f"property_categories[{idx}] must be dict"
        assert "name" in pc, f"property_categories[{idx}] missing 'name'"
        assert isinstance(pc.get("standard_properties", []), list), (
            f"property_categories[{idx}].standard_properties must be list"
        )
        for prop in pc.get("standard_properties", []) or []:
            assert isinstance(prop, str), (
                f"property_categories[{idx}].standard_properties entries must be str"
            )

    for idx, et in enumerate(payload.get("entity_types", []) or []):
        assert isinstance(et, dict), f"entity_types[{idx}] must be dict"
        assert "name" in et, f"entity_types[{idx}] missing 'name'"
        assert isinstance(et.get("required_properties", []), list), (
            f"entity_types[{idx}].required_properties must be list"
        )


def coverage_metrics_snapshot() -> dict[str, object]:
    """Reusable helper for the report CLI.

    Lives in the test module so the CLI script
    (``scripts/ontology_coverage_report.py``) and the pytest gate read the
    same canonical loader. Skips the ``pytest.skip`` guard so the CLI
    fails loudly if the fixture is missing rather than silently exiting.
    """
    payload = load_ontology_payload(canonical_fixture_path())
    ontology_names = collect_ontology_property_names(payload)
    return coverage_metrics(STANDARD_PROPERTIES.keys(), ontology_names)
