"""V1 vs V2 extraction parity harness (NFM-2922, ADR-0007).

This is a *staging-only* test that drives the V1 (``trigger_extraction``
in stub mode) and V2 (``ExtractionOrchestratorV2`` 5-step pipeline)
extraction paths over the same set of text fixtures and reports a
per-fixture pass/fail summary.

Scope notes (read before modifying)
-----------------------------------
* Per CTO decision (NFM-2890 option 2 + NFM-2891 PR #786), the V1
  path is exercised in *stub* mode on staging because no LLM keys are
  configured.  Stub mode returns three canned UO2 records regardless
  of input (see ``_stub_extraction_results`` in
  ``extraction_pipeline.py``).  Most fixtures will therefore *not*
  achieve entity-level parity with V2; that is **expected** and is
  what the harness is designed to surface.
* The harness is the test artifact — it must GREEN (the harness
  itself runs and produces output) even when individual fixtures
  report ``fail``.  The pass/fail count is the deliverable signal,
  not pytest's exit code.
* Numeric tolerance is the +/-5 % relative bound from ADR-0007
  (NFM-2916-AC#3, structural equivalence on KEntity / KRelation,
  numeric +/-5 %).
* This is a backend unit concern; UI/E2E flow is out of scope
  (NFM-2922 AC#handoff).

Fixture set
-----------
* Four curated text fixtures from ``tests/fixtures/parity/baseline/*``
  (NFM-2891, UO2 / MOX / ThO2 / Zircaloy-4).
* Fifty derived text fixtures synthesized deterministically from the
  multimodal ``ground_truth.json`` files under
  ``apps/api/tests/fixtures/extraction/{diagram,microstructure,plot,table}/*``
  (the AC-mandated >=50-doc set, including >=10 high-entity-density
  plot/table cases).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nfm_db.services.extraction.steps.chunk_builder import ChunkBuilder
from nfm_db.services.extraction.steps.entity_extractor import EntityExtractor
from nfm_db.services.extraction.steps.property_normalizer import (
    PropertyNormalizer,
)
from nfm_db.services.extraction.steps.raw_text_loader import RawTextLoader
from nfm_db.services.extraction.steps.section_segmenter import (
    SectionSegmenter,
)
from nfm_db.services.extraction.types import ExtractionChunk
from nfm_db.services.extraction_pipeline import _stub_extraction_results

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_DIR = _REPO_ROOT / "tests" / "fixtures" / "parity" / "baseline"
_EXTRACTION_FIXTURE_DIR = (
    _REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "extraction"
)

# Numeric relative tolerance per ADR-0007 (+/-5 %).
NUMERIC_RELATIVE_TOLERANCE = 0.05

# ---------------------------------------------------------------------------
# Canonical projection: KEntity-like (formula, property, value, unit)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalProperty:
    """A material-science property record in the parity canonical form.

    The shape matches the V1 stub dict (element_system / phase /
    property_name / value / unit) and is what the V2 metadata
    extraction collapses into for diffing.
    """

    formula: str
    property_name: str
    value: float | None
    unit: str | None
    confidence: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula,
            "property_name": self.property_name,
            "value": self.value,
            "unit": self.unit,
            "confidence": self.confidence,
            "source": self.source,
        }


def _project_v1(record: dict[str, Any]) -> CanonicalProperty:
    """Project a V1 stub dict into the canonical property shape."""
    return CanonicalProperty(
        formula=record.get("element_system") or "UNKNOWN",
        property_name=record.get("property_name") or "unknown",
        value=_to_float(record.get("value")),
        unit=record.get("unit"),
        confidence=record.get("confidence"),
        source=record.get("source", ""),
    )


def _project_v2(chunk: ExtractionChunk) -> list[CanonicalProperty]:
    """Project a V2 final chunk's metadata into the canonical shape.

    The V2 ``ChunkBuilder`` stamps ``metadata['entities']`` with three
    lists: ``formulas``, ``properties``, ``measurements``.  We pair
    each formula with each property and each numeric measurement
    (best-effort arity match) so the diff has the same shape as the
    V1 side.
    """
    entities = chunk.metadata.get("entities", {}) if chunk.metadata else {}
    formulas: list[str] = list(entities.get("formulas", []))
    properties: list[str] = list(entities.get("properties", []))
    measurements: list[str] = list(entities.get("measurements", []))

    if not formulas:
        formulas = ["UNKNOWN"]
    if not properties:
        properties = ["unknown"]

    out: list[CanonicalProperty] = []
    for formula in formulas:
        for prop in properties:
            value: float | None = None
            unit: str | None = None
            for meas in measurements:
                parsed = _parse_measurement(meas)
                if parsed is not None:
                    value, unit = parsed
                    break
            out.append(
                CanonicalProperty(
                    formula=formula,
                    property_name=prop,
                    value=value,
                    unit=unit,
                    confidence=None,
                    source=chunk.content[:80],
                )
            )
    return out


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_MEAS_TOKEN = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(.*?)\s*$")


def _parse_measurement(token: str) -> tuple[float, str] | None:
    """Parse ``"7.5 GPa"`` -> (7.5, "GPa"); returns None on no-match."""
    m = _MEAS_TOKEN.match(token)
    if not m:
        return None
    raw_value, raw_unit = m.group(1), m.group(2).strip()
    try:
        return float(raw_value), raw_unit
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------


def _run_v1_stub(text: str) -> list[CanonicalProperty]:
    """Run V1 in stub mode (no LLM keys on staging) and project output."""
    return [_project_v1(r) for r in _stub_extraction_results(text)]


def _run_v2_steps(text: str) -> list[CanonicalProperty]:
    """Drive the 5 V2 steps over ``text`` and project the final output.

    Mirrors ``ExtractionOrchestratorV2.run`` minus the AsyncSession
    persistence layer (unit-test scope).
    """
    loader = RawTextLoader()
    segmenter = SectionSegmenter()
    extractor = EntityExtractor()
    normalizer = PropertyNormalizer()
    builder = ChunkBuilder()

    initial = ExtractionChunk(
        content=text,
        chunk_type="raw_text",
        _source_span=(0, len(text)),
        metadata={},
        parent_chunk_id=None,
    )
    normalized = loader.execute(initial)
    sections = segmenter.execute_many(normalized)
    canonical: list[CanonicalProperty] = []
    for section in sections:
        with_entities = extractor.execute(section)
        with_normalized = normalizer.execute(with_entities)
        final = builder.execute(with_normalized)
        canonical.extend(_project_v2(final))
    return canonical


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@dataclass
class ParityDiff:
    """Result of comparing V1 and V2 canonical projections on one fixture."""

    fixture_name: str
    v1_count: int
    v2_count: int
    matched: int
    numeric_within_tolerance: int
    numeric_outside_tolerance: int
    missing_in_v2: int
    extra_in_v2: int
    pass_: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture_name,
            "v1_count": self.v1_count,
            "v2_count": self.v2_count,
            "matched": self.matched,
            "numeric_within_tolerance": self.numeric_within_tolerance,
            "numeric_outside_tolerance": self.numeric_outside_tolerance,
            "missing_in_v2": self.missing_in_v2,
            "extra_in_v2": self.extra_in_v2,
            "pass": self.pass_,
            "notes": self.notes,
        }


def _diff_one(
    fixture_name: str,
    v1: list[CanonicalProperty],
    v2: list[CanonicalProperty],
) -> ParityDiff:
    """Compare V1 and V2 canonical projections per ADR-0007.

    Pair records by (formula, property_name).  Structural equivalence
    is required (same key, same arity).  Numeric values use the
    +/-5 % relative tolerance.  Missing entities fail the fixture.
    """
    v1_keys = {(r.formula, r.property_name): r for r in v1}
    v2_keys = {(r.formula, r.property_name): r for r in v2}

    matched = 0
    within = 0
    outside = 0
    notes: list[str] = []

    for key, v1_rec in v1_keys.items():
        v2_rec = v2_keys.get(key)
        if v2_rec is None:
            notes.append(f"missing in v2: {key}")
            continue
        matched += 1
        v1_val = v1_rec.value
        v2_val = v2_rec.value
        if v1_val is None and v2_val is None:
            within += 1
            continue
        if v1_val is None or v2_val is None:
            notes.append(
                f"value-shape mismatch {key}: v1={v1_val!r} v2={v2_val!r}"
            )
            outside += 1
            continue
        if v1_val == 0:
            if abs(v2_val) < 1e-9:
                within += 1
            else:
                outside += 1
                notes.append(f"v1 zero vs v2 {v2_val} for {key}")
            continue
        rel = abs(v2_val - v1_val) / abs(v1_val)
        if rel <= NUMERIC_RELATIVE_TOLERANCE:
            within += 1
        else:
            outside += 1
            notes.append(
                f"numeric {rel:.1%} > {NUMERIC_RELATIVE_TOLERANCE:.0%} "
                f"for {key}: v1={v1_val} v2={v2_val}"
            )

    v1_key_set = set(v1_keys.keys())
    v2_key_set = set(v2_keys.keys())
    extra_in_v2 = len(v2_key_set - v1_key_set)
    missing_in_v2 = len(v1_key_set - v2_key_set)

    # Per ADR-0007: missing entities FAIL the test.
    pass_ = missing_in_v2 == 0 and outside == 0
    return ParityDiff(
        fixture_name=fixture_name,
        v1_count=len(v1),
        v2_count=len(v2),
        matched=matched,
        numeric_within_tolerance=within,
        numeric_outside_tolerance=outside,
        missing_in_v2=missing_in_v2,
        extra_in_v2=extra_in_v2,
        pass_=pass_,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Fixture discovery: 4 baseline + 50 synthesized multimodal = 54
# ---------------------------------------------------------------------------


def _synth_text_from_ground_truth(gt: dict[str, Any]) -> str:
    """Deterministic text synthesis from a multimodal ground_truth.json.

    Produces a short paragraph that names the figure, materials,
    axes, units, and the first three numeric pairs -- enough for the
    V2 entity extractor to find at least one formula/property when
    applicable.
    """
    title = gt.get("title", "Figure")
    figure_type = gt.get("figure_type", "figure")
    parts: list[str] = [f"{title}.", f"Figure type: {figure_type}."]

    plot_data = gt.get("plot_data") or {}
    if plot_data:
        x_axis = plot_data.get("x_axis") or {}
        y_axis = plot_data.get("y_axis") or {}
        x_label = x_axis.get("label", "x")
        x_unit = x_axis.get("unit", "")
        y_label = y_axis.get("label", "y")
        y_unit = y_axis.get("unit", "")
        x_vals = (x_axis.get("values") or [])[:3]
        y_vals = (y_axis.get("values") or [])[:3]
        x_unit_str = f" {x_unit}" if x_unit else ""
        y_unit_str = f" {y_unit}" if y_unit else ""
        if x_vals:
            parts.append(
                f"{x_label}{x_unit_str} values: "
                + ", ".join(f"{v}" for v in x_vals)
                + "."
            )
        if y_vals:
            parts.append(
                f"{y_label}{y_unit_str} values: "
                + ", ".join(f"{v}" for v in y_vals)
                + "."
            )

    table_data = gt.get("table_data") or {}
    if table_data:
        headers = (table_data.get("headers") or {}).get("columns") or []
        rows = (table_data.get("rows") or [])
        if headers:
            parts.append("Table columns: " + ", ".join(headers) + ".")
        if rows:
            first = rows[0]
            if isinstance(first, list):
                sample = ", ".join(
                    str(c.get("value", c) if isinstance(c, dict) else c)
                    for c in first[:5]
                )
                parts.append(f"First row: {sample}.")
    return " ".join(parts)


def _gather_text_fixtures() -> list[tuple[str, str]]:
    """Return [(fixture_name, text), ...] sorted.

    Always includes the 4 NFM-2891 baseline text fixtures, then
    synthesizes text from every multimodal ground_truth.json under
    ``apps/api/tests/fixtures/extraction/{diagram,microstructure,
    plot,table}/*``.
    """
    out: list[tuple[str, str]] = []
    if _BASELINE_DIR.exists():
        for d in sorted(_BASELINE_DIR.iterdir()):
            if not d.is_dir():
                continue
            source = d / "source.txt"
            if source.exists():
                out.append((f"baseline/{d.name}", source.read_text(encoding="utf-8")))

    if _EXTRACTION_FIXTURE_DIR.exists():
        for sub in ("diagram", "microstructure", "plot", "table"):
            sub_dir = _EXTRACTION_FIXTURE_DIR / sub
            if not sub_dir.exists():
                continue
            for d in sorted(sub_dir.iterdir()):
                if not d.is_dir():
                    continue
                gt = d / "ground_truth.json"
                if not gt.exists():
                    continue
                try:
                    payload = json.loads(gt.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                out.append(
                    (f"extraction/{sub}/{d.name}", _synth_text_from_ground_truth(payload))
                )
    return out


_FIXTURES = _gather_text_fixtures()


# Session-scoped summary collector (printed once at the end).
_SESSION_SUMMARY: list[ParityDiff] = []


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Print the per-fixture parity summary after the run finishes."""
    if not _SESSION_SUMMARY:
        return
    total = len(_SESSION_SUMMARY)
    passed = sum(1 for d in _SESSION_SUMMARY if d.pass_)
    failed = total - passed
    print("\n" + "=" * 72)
    print(f"NFM-2922 V1/V2 parity summary: total={total} pass={passed} fail={failed}")
    print("=" * 72)
    for d in _SESSION_SUMMARY:
        marker = "PASS" if d.pass_ else "FAIL"
        print(
            f"  [{marker}] {d.fixture_name:<48} "
            f"v1={d.v1_count:>2} v2={d.v2_count:>2} "
            f"matched={d.matched:>2} "
            f"within={d.numeric_within_tolerance:>2} "
            f"outside={d.numeric_outside_tolerance:>2} "
            f"miss={d.missing_in_v2:>2}"
        )
    # Top 3 failure modes
    note_counter: dict[str, int] = {}
    for d in _SESSION_SUMMARY:
        for note in d.notes:
            key = note.split(":", 1)[0]
            note_counter[key] = note_counter.get(key, 0) + 1
    top = sorted(note_counter.items(), key=lambda x: -x[1])[:3]
    if top:
        print("\nTop failure modes:")
        for k, n in top:
            print(f"  {n:>3}x  {k}")
    print("=" * 72)


@pytest.mark.parity
@pytest.mark.parametrize(
    "fixture_name,text",
    _FIXTURES,
    ids=[name for (name, _) in _FIXTURES],
)
def test_v1_v2_parity(fixture_name: str, text: str) -> None:
    """Drive V1 (stub) and V2 (5-step) over ``text`` and diff per ADR-0007.

    The test *runs the harness* on each fixture and records the diff
    into the session-scoped summary.  It does NOT fail when an
    individual fixture is non-parity -- the harness is the deliverable
    artifact and the per-fixture pass/fail list is the signal.
    """
    v1 = _run_v1_stub(text)
    v2 = _run_v2_steps(text)
    diff = _diff_one(fixture_name, v1, v2)
    _SESSION_SUMMARY.append(diff)
    # Per-fixture pass/fail line (visible under -s, useful in CI logs).
    print(
        f"[parity] {fixture_name}: v1={diff.v1_count} v2={diff.v2_count} "
        f"matched={diff.matched} within={diff.numeric_within_tolerance} "
        f"outside={diff.numeric_outside_tolerance} "
        f"miss={diff.missing_in_v2} -> "
        f"{'PASS' if diff.pass_ else 'FAIL'}"
    )


def test_fixture_set_size_meets_ac() -> None:
    """AC#2: at least 50 docs from the extraction fixtures set."""
    assert len(_FIXTURES) >= 50, (
        f"AC#2 requires >=50 fixtures; discovered {len(_FIXTURES)}. "
        f"Add more ground_truth.json files under "
        f"apps/api/tests/fixtures/extraction/."
    )


def test_high_entity_density_subset_present() -> None:
    """AC#2: at least 10 high-entity-density cases (plot/table fixtures)."""
    high_density = [
        name for (name, _) in _FIXTURES
        if "/plot/" in name or "/table/" in name
    ]
    assert len(high_density) >= 10, (
        f"AC#2 requires >=10 high-entity-density cases; found {len(high_density)}."
    )


def test_materials_mix_present() -> None:
    """AC#2: mix of UO2, MOX, Zr-alloy materials in the fixture set."""
    names = " ".join(name for (name, _) in _FIXTURES)
    # The 4 baseline fixtures are curated for UO2 / MOX / ThO2 / Zircaloy.
    assert "uo2" in names.lower(), "Missing UO2 coverage in fixtures."
    assert "mox" in names.lower(), "Missing MOX coverage in fixtures."
    assert "zircaloy" in names.lower() or "zr" in names.lower(), (
        "Missing Zr-alloy coverage in fixtures."
    )


def test_aggregate_parity_summary() -> None:
    """Print the aggregate parity summary and assert harness is GREEN.

    The harness is the deliverable artifact (AC#1-#5).  This test
    is the deterministic end-of-run summary that the CPO and the
    next-phase CI wiring will read.  It runs *after* the per-fixture
    parametrize tests, so by the time it runs the
    ``_SESSION_SUMMARY`` list is fully populated.
    """
    total = len(_SESSION_SUMMARY)
    passed = sum(1 for d in _SESSION_SUMMARY if d.pass_)
    failed = total - passed
    note_counter: dict[str, int] = {}
    for d in _SESSION_SUMMARY:
        for note in d.notes:
            key = note.split(":", 1)[0]
            note_counter[key] = note_counter.get(key, 0) + 1
    top = sorted(note_counter.items(), key=lambda x: -x[1])[:3]

    print("\n" + "=" * 72)
    print(
        f"NFM-2922 V1/V2 parity summary: "
        f"total={total} pass={passed} fail={failed}"
    )
    print("=" * 72)
    print("Per-fixture results:")
    for d in _SESSION_SUMMARY:
        marker = "PASS" if d.pass_ else "FAIL"
        print(
            f"  [{marker}] {d.fixture_name:<48} "
            f"v1={d.v1_count:>2} v2={d.v2_count:>2} "
            f"matched={d.matched:>2} "
            f"within={d.numeric_within_tolerance:>2} "
            f"outside={d.numeric_outside_tolerance:>2} "
            f"miss={d.missing_in_v2:>2}"
        )
    if top:
        print("\nTop failure modes:")
        for k, n in top:
            print(f"  {n:>3}x  {k}")
    print("=" * 72)

    # Harness artifact is the deliverable, not V1==V2 on every fixture.
    # The per-fixture pass count IS the signal; this assertion
    # enforces that the harness itself ran (it cannot pass with zero
    # fixtures) and that the session-level summary was emitted.
    assert total >= 50, (
        f"Harness ran {total} fixtures; AC#2 requires >=50."
    )
    assert total == len(_FIXTURES), (
        f"Summary cardinality {total} != fixture count {len(_FIXTURES)}; "
        f"a fixture was dropped or duplicated."
    )
