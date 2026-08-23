"""Shadow-run parity harness for ``gap_scan_service`` vs ``gap_scanner`` (NFM-3565 / D2).

Per NFM-3545 D2, the deliverable is a 100-sample shadow-run fixture plus a
one-cycle CI parity harness.  For each input the harness drives the
**legacy** ``nfm_db.services.gap_scan_service.GapScanService`` and the
**canonical** ``nfm_db.services.gap_scanner.GapScanService`` with
representative inputs and verifies that:

1. Both modules are importable and expose a ``GapScanService`` class.
2. Both modules expose public dataclasses with comparable shapes
   (``frozen=True`` and a stable field set).
3. The legacy module emits ``DeprecationWarning`` on instantiation;
   the canonical module does not.
4. For each of the 100 fixture entries, the module-level pure helpers
   of both modules agree on the structural shape of the input:
   the legacy's ``_compute_priority`` accepts the same
   ``(element_system, phase, property_name)`` triple the canonical's
   ``iter_property_names`` + ``extract_entity_types`` would surface
   from an ontology of equivalent shape.
5. Drift in any of the above surfaces is captured as a structured
   ``ParityFailure`` and surfaced via a single failing test with a precise
   diff (one failure covers all 100 inputs).

Per NFM-3545-D1 (commit ``d1ce5e98f``) the two ``GapScanService``
classes cover different domains — legacy is ``RefGapFillStaging``-based
(hardcoded 12 tuples) and canonical is ``ExtractionGap``-based
(ontology-driven).  They are not byte-for-byte interchangeable, so
"parity" here is **structural / symbolic** parity (same public symbol
shape, same dataclass field conventions, same warning behaviour), not
output equality.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import warnings
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from nfm_db.services import gap_scan_service as legacy_module
from nfm_db.services import gap_scanner as canonical_module

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "gap_scan_parity_100.jsonl"
)


def _load_fixture() -> list[dict[str, Any]]:
    """Return the 100 fixture entries from the JSONL file."""
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Parity fixture missing: {FIXTURE_PATH}. "
            "The fixture is part of the NFM-3565 deliverable."
        )
    entries: list[dict[str, Any]] = []
    with FIXTURE_PATH.open() as handle:
        for lineno, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"Invalid JSON on line {lineno} of {FIXTURE_PATH}: {exc}"
                ) from exc
            entry.setdefault("id", lineno)
            entries.append(entry)
    if len(entries) != 100:
        raise AssertionError(
            f"Parity fixture must contain exactly 100 entries; "
            f"got {len(entries)} (file: {FIXTURE_PATH})"
        )
    return entries


PARITY_FIXTURE: list[dict[str, Any]] = _load_fixture()


# ---------------------------------------------------------------------------
# Parity failure container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParityFailure:
    """A single parity drift captured by the harness.

    Surfaced collectively via ``pytest.fail`` so the test exits non-zero
    with a precise diff for every failing input, not just the first one.
    """

    fixture_id: int
    surface: str
    legacy_value: Any
    canonical_value: Any
    rationale: str

    def render(self) -> str:
        return (
            f"fixture #{self.fixture_id}  surface={self.surface}\n"
            f"  legacy   : {self.legacy_value!r}\n"
            f"  canonical: {self.canonical_value!r}\n"
            f"  reason   : {self.rationale}"
        )


def _run_parity(fixture: dict[str, Any]) -> list[ParityFailure]:
    """Run a single fixture entry and return any parity drifts.

    Captures per-surface discrepancies between the legacy module and the
    canonical module; an empty list means the fixture passed.
    """
    failures: list[ParityFailure] = []
    fid = fixture["id"]
    triple = tuple(fixture["target_triple"])

    # Surface A: legacy._compute_priority vs canonical.iter_property_names
    #            (both consume an (element_system, phase, property_name)
    #            triple and emit a comparable scalar / iterable).
    try:
        legacy_priority = legacy_module._compute_priority(*triple)
    except Exception as exc:
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="legacy._compute_priority",
                legacy_value=f"raised {type(exc).__name__}: {exc}",
                canonical_value=None,
                rationale="legacy helper crashed on a triple it should accept",
            )
        )
        legacy_priority = None

    # Canonical side: the equivalent surface is
    # ``extract_entity_types`` + ``iter_property_names`` operating on a
    # constructed ontology-like blob.
    ontology_like = _ontology_like_for_triple(fixture)
    try:
        entity_types = canonical_module.extract_entity_types(ontology_like)
    except Exception as exc:
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="canonical.extract_entity_types",
                legacy_value=None,
                canonical_value=f"raised {type(exc).__name__}: {exc}",
                rationale="canonical helper crashed on equivalent input",
            )
        )
        entity_types = []

    try:
        property_names: list[str] = []
        for et in entity_types:
            property_names.extend(canonical_module.iter_property_names(
                et.get("properties"),
            ))
    except Exception as exc:
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="canonical.iter_property_names",
                legacy_value=None,
                canonical_value=f"raised {type(exc).__name__}: {exc}",
                rationale="canonical helper crashed on equivalent input",
            )
        )
        property_names = []

    # Parity rule (Surface A): the triple's property_name must surface in
    # the canonical module's resolved property names.  If it does not,
    # the canonical module has lost coverage that the legacy module
    # accepts — that is drift.
    if triple[2] not in property_names and property_names:
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="property_triple_visibility",
                legacy_value=triple,
                canonical_value=property_names,
                rationale=(
                    "legacy accepts this triple but canonical's ontology "
                    "shape does not surface the same property_name"
                ),
            )
        )

    # Surface B: deprecation warning must be emitted by the legacy module
    # but NOT by the canonical module on instantiation.
    legacy_emit, legacy_emitted_warning = _capture_warning(
        lambda: legacy_module.GapScanService.__init__(
            object.__new__(legacy_module.GapScanService),
            session=object(),
            target_tuples=[dict(zip(("element_system", "phase", "property_name"), triple, strict=True))],
        )
    )
    canonical_emit, canonical_emitted_warning = _capture_warning(
        lambda: canonical_module.GapScanService.__init__(
            object.__new__(canonical_module.GapScanService),
            session=object(),
        )
    )

    if not legacy_emit:
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="legacy_emits_deprecation",
                legacy_value=legacy_emitted_warning,
                canonical_value=None,
                rationale="legacy GapScanService no longer emits DeprecationWarning",
            )
        )
    if canonical_emit:
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="canonical_no_deprecation",
                legacy_value=None,
                canonical_value=canonical_emitted_warning,
                rationale="canonical GapScanService emitted an unexpected DeprecationWarning",
            )
        )

    # Surface C: priority ranking sanity — legacy's _compute_priority must
    # return an int and that int must fall in a sane range (>= 1).
    if legacy_priority is not None and (
        not isinstance(legacy_priority, int) or legacy_priority < 1
    ):
        failures.append(
            ParityFailure(
                fixture_id=fid,
                surface="legacy_priority_value",
                legacy_value=legacy_priority,
                canonical_value=None,
                rationale="legacy priority is not a positive int",
            )
        )

    return failures


def _ontology_like_for_triple(fixture: dict[str, Any]) -> Any:
    """Build an ontology-like object that ``extract_entity_types`` accepts.

    ``extract_entity_types`` accepts any object with a ``.ontology_data``
    attribute returning a dict containing the ``entity_types`` list.
    Each fixture entry already carries the canonical-shaped ontology
    blob (under ``canonical_ontology``); we wrap it in a tiny namespace.
    """

    @dataclass
    class _OntologyStub:
        ontology_data: dict[str, Any]

    return _OntologyStub(ontology_data=dict(fixture["canonical_ontology"]))


def _capture_warning(callable_: Any) -> tuple[bool, str | None]:
    """Run ``callable_`` capturing whether a DeprecationWarning fired.

    Returns ``(emitted, category_name)``.  Used to compare legacy vs
    canonical deprecation behaviour without polluting pytest's warning
    capture state.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        callable_()
    emitted = any(
        issubclass(w.category, DeprecationWarning) for w in caught
    )
    category_name = (
        type(caught[-1].message).__name__
        if emitted and caught
        else None
    )
    return emitted, category_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSymbolicParity:
    """Verify the public symbol shape is preserved across both modules."""

    def test_legacy_module_exports_gap_scan_service_class(self) -> None:
        """Legacy module exposes ``GapScanService`` (the symbol D3 will remove)."""
        assert hasattr(legacy_module, "GapScanService")
        assert inspect.isclass(legacy_module.GapScanService)

    def test_canonical_module_exports_gap_scan_service_class(self) -> None:
        """Canonical module exposes ``GapScanService`` (the migration target)."""
        assert hasattr(canonical_module, "GapScanService")
        assert inspect.isclass(canonical_module.GapScanService)

    def test_both_modules_export_frozen_dataclasses(self) -> None:
        """Both modules export at least one frozen dataclass for symmetry."""
        for module, label in (
            (legacy_module, "legacy"),
            (canonical_module, "canonical"),
        ):
            frozen_dataclasses = [
                name
                for name in dir(module)
                if not name.startswith("_")
                and is_dataclass(getattr(module, name, None))
                and dataclasses.fields(
                    getattr(module, name),
                )
                and getattr(module, name).__dataclass_params__.frozen
            ]
            assert frozen_dataclasses, (
                f"{label} module exposes no frozen dataclasses"
            )

    def test_legacy_emits_deprecation_on_init(self) -> None:
        """Legacy ``GapScanService.__init__`` emits ``DeprecationWarning``."""
        emitted, _ = _capture_warning(
            lambda: legacy_module.GapScanService.__init__(
                object.__new__(legacy_module.GapScanService),
                session=object(),
                target_tuples=[],
            )
        )
        assert emitted, (
            "Legacy GapScanService no longer emits DeprecationWarning. "
            "The D4 DeprecationWarning-shim removal cannot proceed if this"
            " regression lands."
        )

    def test_canonical_does_not_emit_deprecation_on_init(self) -> None:
        """Canonical ``GapScanService.__init__`` must not emit any warning."""
        emitted, category = _capture_warning(
            lambda: canonical_module.GapScanService.__init__(
                object.__new__(canonical_module.GapScanService),
                session=object(),
            )
        )
        assert not emitted, (
            f"Canonical GapScanService emitted unexpected warning: {category}"
        )


class TestShadowRunParity:
    """Drive every fixture entry through both modules and verify parity."""

    def test_all_100_inputs_produce_no_parity_drift(self) -> None:
        """For all 100 fixture inputs, no parity surface may drift.

        Any drift is aggregated into a single ``pytest.fail`` so the test
        exits non-zero with a precise diff covering every failing
        fixture entry — not just the first one.
        """
        all_failures: list[ParityFailure] = []
        for fixture in PARITY_FIXTURE:
            all_failures.extend(_run_parity(fixture))

        if all_failures:
            rendered = "\n\n".join(f.render() for f in all_failures)
            pytest.fail(
                "Parity drift detected across "
                f"{len(all_failures)} surface(s) on "
                f"{len({f.fixture_id for f in all_failures})} "
                "fixture input(s):\n\n" + rendered
            )


# ---------------------------------------------------------------------------
# Sanity guard: the fixture must actually exercise every symbol inventoried
# in D1 (per ``docs/refactor/NFM-3545-import-inventory.md`` §3).
# ---------------------------------------------------------------------------


class TestFixtureCoverage:
    """Verify the fixture covers every D1-inventoried public symbol."""

    @pytest.mark.parametrize(
        "entry",
        PARITY_FIXTURE,
        ids=lambda e: f"#{e['id']:03d}-{e['kind']}",
    )
    def test_entry_has_required_shape(self, entry: dict[str, Any]) -> None:
        """Every fixture entry exposes the keys the harness relies on."""
        for key in (
            "id", "kind", "description", "target_triple",
            "canonical_ontology",
        ):
            assert key in entry, (
                f"fixture #{entry.get('id')} missing required key {key!r}"
            )
        triple = entry["target_triple"]
        assert isinstance(triple, list) and len(triple) == 3, (
            f"fixture #{entry['id']} target_triple must be a 3-list"
        )
        ontology = entry["canonical_ontology"]
        assert isinstance(ontology, dict), (
            f"fixture #{entry['id']} canonical_ontology must be a dict"
        )
        assert "entity_types" in ontology, (
            f"fixture #{entry['id']} canonical_ontology must declare "
            "entity_types so canonical.extract_entity_types can resolve it"
        )

    def test_fixture_covers_all_default_target_tuples(self) -> None:
        """The 12 hardcoded legacy target tuples each appear at least once."""
        legacy_default_tuples = {
            ("U", "BCC", "lattice_constant"),
            ("U", "BCC", "bulk_modulus"),
            ("U", "BCC", "thermal_conductivity"),
            ("U", "FCC", "lattice_constant"),
            ("U", "FCC", "bulk_modulus"),
            ("UO2", "FCC", "lattice_constant"),
            ("UO2", "FCC", "bulk_modulus"),
            ("UO2", "FCC", "thermal_conductivity"),
            ("UO2", "FCC", "linear_expansion"),
            ("Zr", "HCP", "lattice_constant"),
            ("Zr", "HCP", "bulk_modulus"),
            ("Zr", "HCP", "thermal_conductivity"),
        }
        seen = {
            tuple(entry["target_triple"])
            for entry in PARITY_FIXTURE
        }
        missing = legacy_default_tuples - seen
        assert not missing, (
            "Fixture does not cover these legacy target tuples: "
            f"{sorted(missing)}"
        )

    def test_fixture_covers_canonical_helpers(self) -> None:
        """At least 10 entries drive ``iter_property_names`` with dict input.

        The D1 inventory notes the canonical helper accepts both
        ``properties: ["density", ...]`` and
        ``properties: [{"name": "density", ...}, ...]``.  The fixture
        must exercise the dict-input path at least 10 times.
        """
        dict_property_entries = [
            entry
            for entry in PARITY_FIXTURE
            if any(
                isinstance(prop, dict)
                for et in entry["canonical_ontology"].get("entity_types", [])
                for prop in (et.get("properties") or [])
            )
        ]
        assert len(dict_property_entries) >= 10, (
            "Fixture must exercise iter_property_names's dict-input "
            f"path at least 10 times; got {len(dict_property_entries)}"
        )
