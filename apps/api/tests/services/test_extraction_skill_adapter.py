"""Tests for the skill-output adapter (NFM-4547 / AC-1).

The recall regression floor for this skill path is Beeler 2018 ≥ 4/4
numeric. Because we cannot run the upstream skill (it lives in an
external repo pinned via EXTRACTION_SKILL_REPO_PIN), the fixture here
mimics the skill output that Beeler 2018 produced during wayfinder
#1264 — four numeric-domain property measurements with the exact 13
fields ``nuclear-property-extraction-v4`` emits.

The fixture is intentionally hand-crafted from the #1264 recall report
so the regression test fails loudly if the adapter drops or mangles any
of the four rows. §3.2 of the G1 spec lists the 13→20 mapping.
"""

from __future__ import annotations

from typing import Any

import pytest

from nfm_db.services.extraction_skill_adapter import (
    AdaptedPropertyMeasurement,
    AdapterContext,
    adapt_skill_output,
    compute_dedupe_key,
    derive_source_span,
    parse_value_to_numeric,
)

# ---------------------------------------------------------------------------
# Beeler 2018 fixture — 4 numeric-domain rows (the AC-1 floor)
# ---------------------------------------------------------------------------


# Synthetic content_md slice that the source_span heuristic is allowed
# to anchor on. Anchors must be unique substrings of the relevant
# property name to keep the heuristic deterministic.
_BEELER_2018_CONTENT_MD = """\
# Formation energy and elastic constants of point defects in bcc iron

## Abstract

We compute formation energies and elastic constants for vacancy and
self-interstitial atom defects in body-centred cubic iron using
density-functional theory.

## Results

### Vacancy formation energy

The vacancy formation energy at 0 K is 2.07 eV.

### Vacancy migration energy

The vacancy migration energy is 0.65 eV.

### Di-vacancy binding energy

The di-vacancy binding energy is 0.30 eV.

### Self-interstitial formation energy

The self-interstitial atom formation energy at 0 K is 3.64 eV.
"""


def _beeler_2018_skill_records() -> list[dict[str, Any]]:
    """Return the 4-row fixture derived from wayfinder #1264.

    Field order matches the ``ExtractedProperty`` schema (13 fields).
    All rows use property_category='physical' which maps cleanly onto
    the DB slug 'physical'.
    """
    return [
        {
            "source_file": "literature/Beeler2018.md",
            "material_name": "bcc Fe",
            "composition": "Fe",
            "phase": "alpha",
            "element": "Fe",
            "property_category": "physical",
            "property": "vacancy formation energy",
            "value": "2.07",
            "unit": "eV",
            "conditions": {"temp_K": 0, "method": "DFT"},
            "context": "monovacancy, 0 K",
            "confidence": "high",
            "reference": "Beeler et al., J. Phys. Condens. Matter (2018)",
        },
        {
            "source_file": "literature/Beeler2018.md",
            "material_name": "bcc Fe",
            "composition": "Fe",
            "phase": "alpha",
            "element": "Fe",
            "property_category": "physical",
            "property": "vacancy migration energy",
            "value": "0.65",
            "unit": "eV",
            "conditions": {"temp_K": 0, "method": "DFT"},
            "context": "monovacancy migration",
            "confidence": "high",
            "reference": "Beeler et al., J. Phys. Condens. Matter (2018)",
        },
        {
            "source_file": "literature/Beeler2018.md",
            "material_name": "bcc Fe",
            "composition": "Fe",
            "phase": "alpha",
            "element": "Fe",
            "property_category": "physical",
            "property": "di-vacancy binding energy",
            "value": "0.30",
            "unit": "eV",
            "conditions": {"temp_K": 0, "method": "DFT"},
            "context": "divacancy binding",
            "confidence": "high",
            "reference": "Beeler et al., J. Phys. Condens. Matter (2018)",
        },
        {
            "source_file": "literature/Beeler2018.md",
            "material_name": "bcc Fe",
            "composition": "Fe",
            "phase": "alpha",
            "element": "Fe",
            "property_category": "physical",
            "property": "self-interstitial formation energy",
            "value": "3.64",
            "unit": "eV",
            "conditions": {"temp_K": 0, "method": "DFT"},
            "context": "SIA, dumbbell configuration",
            "confidence": "high",
            "reference": "Beeler et al., J. Phys. Condens. Matter (2018)",
        },
    ]


def _beeler_ctx(*, file_content: str | None = _BEELER_2018_CONTENT_MD) -> AdapterContext:
    return AdapterContext(
        dataset_id="d-1234",
        dataset_version_id="dv-5678",
        source_id="s-abcd",
        extraction_skill_version="v1.7.2",
        file_content=file_content,
        page_number=1,
    )


# ---------------------------------------------------------------------------
# AC-1: Beeler 2018 numeric recall ≥ 4/4
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_beeler_2018_recall_floor_4_of_4() -> None:
    """AC-1: Beeler 2018 numeric recall = 4/4.

    The adapter must surface all four numeric-domain rows as candidates
    ready for the DB mapper. We assert on shape (1:1 mapping), not on
    persistence — the mapper is downstream.
    """
    ctx = _beeler_ctx()
    adapted = adapt_skill_output(_beeler_2018_skill_records(), ctx)

    assert len(adapted) == 4, (
        f"AC-1 violated: expected 4 rows, got {len(adapted)}. "
        "The adapter must roundtrip every Beeler 2018 numeric row."
    )

    # Every row should be a numeric measurement, not text-only.
    for row in adapted:
        assert isinstance(row, AdaptedPropertyMeasurement)
        assert row.value_numeric is not None, (
            f"Beeler row {row.property!r} lost its numeric value"
        )
        assert row.value_numeric > 0
        assert row.unit == "eV"
        assert row.property_category == "physical"

    # The four expected numerics, in fixture order.
    expected_values = [2.07, 0.65, 0.30, 3.64]
    actual_values = [row.value_numeric for row in adapted]
    assert actual_values == pytest.approx(expected_values, rel=1e-9)

    # Property names should roundtrip cleanly.
    expected_properties = {
        "vacancy formation energy",
        "vacancy migration energy",
        "di-vacancy binding energy",
        "self-interstitial formation energy",
    }
    assert {row.property for row in adapted} == expected_properties


@pytest.mark.unit
def test_beeler_2018_each_row_has_dedupe_key_and_skill_version() -> None:
    """§3.1 + §3.2 of the G1 spec — every adapted row carries the
    composite dedupe key plus the pinned skill version.
    """
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output(_beeler_2018_skill_records(), ctx)

    assert len(adapted) == 4
    for row in adapted:
        assert row.dedupe_key, "dedupe_key must be non-empty"
        assert "|" in row.dedupe_key
        assert row.dataset_id == "d-1234"
        assert row.dataset_version_id == "dv-5678"
        assert row.extraction_skill_version == "v1.7.2"


@pytest.mark.unit
def test_beeler_2018_phase_routed_into_conditions_jsonb() -> None:
    """§2.5 of ADR-016 — phase lives in conditions.phase, not material."""
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output(_beeler_2018_skill_records(), ctx)

    for row in adapted:
        assert row.phase == "alpha"
        assert row.conditions is not None
        assert row.conditions.get("phase") == "alpha"


@pytest.mark.unit
def test_beeler_2018_high_freq_conditions_projected() -> None:
    """§2.4 — method + temp_K land in the fixed-column projection; the
    adapter stashes them alongside the row for the mapper.
    """
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output(_beeler_2018_skill_records(), ctx)

    for row in adapted:
        assert row.fixed_condition_columns.get("method") == "DFT"
        assert row.fixed_condition_columns.get("temp_K") == 0


# ---------------------------------------------------------------------------
# Source span heuristic — §2.6 of ADR-016
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_source_span_heuristic_anchors_on_property_name() -> None:
    """When upstream does not emit ``source_span``, the heuristic
    locates the property-name keyword and returns a char window +
    snippet hash.
    """
    record = _beeler_2018_skill_records()[0]
    span = derive_source_span(
        skill_record=record,
        file_content=_BEELER_2018_CONTENT_MD,
        page_number=1,
    )
    assert span is not None
    assert span["file"] == "literature/Beeler2018.md"
    assert span["page"] == 1
    assert isinstance(span["char_start"], int)
    assert isinstance(span["char_end"], int)
    assert span["char_end"] > span["char_start"]
    assert len(span["snippet_hash"]) == 40
    # The window should contain the anchor keyword.
    window = _BEELER_2018_CONTENT_MD[span["char_start"] : span["char_end"]]
    assert "vacancy formation energy" in window.lower()


@pytest.mark.unit
def test_source_span_heuristic_returns_none_when_no_content() -> None:
    """Heuristic returns ``None`` when no file content is supplied —
    the mapper persists JSON null and AC-1 (numeric recall) is
    unaffected."""
    record = _beeler_2018_skill_records()[0]
    span = derive_source_span(
        skill_record=record,
        file_content=None,
        page_number=None,
    )
    assert span is None


@pytest.mark.unit
def test_source_span_uses_explicit_field_when_present() -> None:
    """Once the upstream skill emits ``source_span`` natively, the
    adapter should pass it through verbatim — no heuristic.
    """
    record = {
        **_beeler_2018_skill_records()[0],
        "source_span": {
            "file": "literature/Beeler2018.md",
            "page": 7,
            "char_start": 1234,
            "char_end": 1500,
            "snippet_hash": "a" * 40,
        },
    }
    span = derive_source_span(
        skill_record=record,
        file_content=None,
        page_number=1,
    )
    assert span is not None
    assert span["page"] == 7
    assert span["char_start"] == 1234


# ---------------------------------------------------------------------------
# Value routing — §3.1 of the G1 spec
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_value_numeric_routing_simple_number() -> None:
    numeric, text = parse_value_to_numeric("2.07")
    assert numeric == pytest.approx(2.07)
    assert text == "2.07"


@pytest.mark.unit
def test_value_numeric_routing_compound_value_drops_to_text() -> None:
    """Compound values like '3 to 4' must not silently truncate."""
    numeric, text = parse_value_to_numeric("3 to 4")
    assert numeric is None
    assert text == "3 to 4"


@pytest.mark.unit
def test_value_numeric_routing_expression_routed_to_expression() -> None:
    """Expressions like 'exp(-Ea/kT)' → value_expression, not text."""
    record = {
        **_beeler_2018_skill_records()[0],
        "value": "exp(-Ea/kT)",
    }
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output([record], ctx)
    row = adapted[0]
    assert row.value_numeric is None
    assert row.value_expression == "exp(-Ea/kT)"
    assert row.value_text is None


# ---------------------------------------------------------------------------
# Confidence + review_status routing — §3.4 + §5
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_high_confidence_record_lands_in_pending_status() -> None:
    """§3.4 — the initial review_status is 'pending'; domain_expert
    actions move rows into the other states. We assert the literal
    mapping: high → confidence=0.95, review_status=pending.
    """
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output(_beeler_2018_skill_records()[:1], ctx)
    row = adapted[0]
    assert row.confidence == pytest.approx(0.95)
    assert row.review_status == "pending"


@pytest.mark.unit
def test_low_confidence_below_threshold_routes_to_pending() -> None:
    """§5 — confidence < 0.7 → review_status='pending'. The literal
    'low' maps to 0.5 which is below 0.7, so the heuristic must
    flag it.
    """
    record = {
        **_beeler_2018_skill_records()[0],
        "confidence": "low",
    }
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output([record], ctx)
    row = adapted[0]
    assert row.confidence == pytest.approx(0.5)
    assert row.review_status == "pending"


@pytest.mark.unit
def test_out_of_catalog_flag_routes_to_pending() -> None:
    """§2.3 of ADR-016 — out-of-catalog rows go to pending review,
    never to the verification main table on first pass.
    """
    record = {
        **_beeler_2018_skill_records()[0],
        "out_of_catalog": True,
    }
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output([record], ctx)
    row = adapted[0]
    assert row.review_status == "pending"


# ---------------------------------------------------------------------------
# Dedupe key composition — §3.1
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dedupe_key_is_stable_across_runs() -> None:
    """Same input → same dedupe key across two adapter calls."""
    key_a = compute_dedupe_key(
        dataset_id="d-1",
        property_type_id="pt-1",
        source_id="s-1",
        value_hash="abc",
    )
    key_b = compute_dedupe_key(
        dataset_id="d-1",
        property_type_id="pt-1",
        source_id="s-1",
        value_hash="abc",
    )
    assert key_a == key_b
    assert key_a == "d-1|pt-1|s-1|abc"


@pytest.mark.unit
def test_beeler_rows_have_distinct_dedupe_keys() -> None:
    """Distinct values must produce distinct dedupe keys — otherwise
    AC-9 (dedupe_key unique) is broken by construction.
    """
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output(_beeler_2018_skill_records(), ctx)
    keys = [row.dedupe_key for row in adapted]
    assert len(set(keys)) == len(keys), "Beeler 4 rows must yield 4 distinct dedupe keys"


# ---------------------------------------------------------------------------
# Robustness — adapter should never silently drop a record
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_adapter_skips_non_mapping_records() -> None:
    """Non-mapping entries (stray ints, None, lists) are logged and
    skipped, not raised — protects the seam from LLM output drift.
    """
    ctx = _beeler_ctx(file_content=None)
    records = [*_beeler_2018_skill_records(), None, "stray", [1, 2, 3]]
    adapted = adapt_skill_output(records, ctx)
    assert len(adapted) == 4


@pytest.mark.unit
def test_adapter_marks_record_without_property_as_invalid() -> None:
    """A record missing the 'property' field must surface as
    review_status='invalid' so the mapper can drop it without
    silently emitting empty property names.
    """
    record = {
        "source_file": "literature/x.md",
        "value": "5.0",
        "unit": "eV",
        "confidence": "high",
    }
    ctx = _beeler_ctx(file_content=None)
    adapted = adapt_skill_output([record], ctx)
    assert len(adapted) == 1
    assert adapted[0].review_status == "invalid"
    assert adapted[0].property == "(unknown)"
