"""Contract tests for the KG → staging bridge (NFM-3478 Layer B).

Pins the mapping that makes freshly extracted papers visible in the
ontology viewer: Property nodes (+ Material / Condition context from
``hasProperty`` / ``relatedTo`` edges) must land in
``_ref_gap_fill_staging`` as one corpus per paper, with normalized
units, slugged property names, parsed conditions, and dedup-safe rows.

SQLite test back-end cannot store the production PG UUID columns of
KGNode/KGEdge, so the e2e tests stub the SQLAlchemy execute() path
for KG queries only — staging rows are written to the real SQLite
RefGapFillStaging table so we still cover the write/dedup contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.services.kg_to_staging_bridge import (
    _PROPERTY_SLUGS,
    _canonical_element_system,
    _parse_numeric,
    _slugify,
    bridge_kg_to_staging,
    confidence_from_property_measurement,
)
from tests._helpers.owen2023_corpus import snapshot_labels

# --- pure helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("U", "U"),
        ("U₂Mo", "U-Mo"),
        ("UO2", "UO2"),
        ("U3Si2", "U3Si2"),
    ],
)
def test_canonical_element_system(label, expected):
    assert _canonical_element_system(label) == expected


# NFM-4037: morphology adjectives + Cr-bearing UO2 labels that the
# Owen2023 corpus (source 9320cb50) emits on Material KGNode rows.
# The F8 scorecard (``audit/f8_scorecard_v050.sql``) only accepts
# ``element_system IN ('UO2', 'UO2+Cr', 'U-Cr-O')`` so every label in
# this table must collapse onto one of those three.  Regression tests
# for the pre-existing subscript path (``U₂Mo`` → ``U-Mo``) live above.
#
# NFM-4048: this table is a superset of the production corpus.  The
# prod half is cross-checked against
# ``tests/fixtures/owen2023_material_labels.json`` by
# ``test_canonical_element_system_covers_every_owen2023_label``, so a
# prod label can no longer be missing from here (QA warning W1).  The
# remaining rows are deliberate *defensive* coverage for labels prod
# does not currently hold (``crystalline UO2``, ``nano-Cr``, the
# ``U-10Mo`` pass-throughs) — keep them.
_OWEN2023_LABELS: list[tuple[str, str]] = [
    # Undoped UO2 — the strip-loop must remove the adjective but leave
    # a label the F8 ``element_system = 'UO2'`` predicate matches.
    ("UO2", "UO2"),
    ("amorphous UO2", "UO2"),
    ("crystalline UO2", "UO2"),
    ("polycrystalline UO2", "UO2"),
    # Cr-bearing UO2 — every label collapses onto 'UO2+Cr' (the F8
    # scorecard accepts BOTH 'UO2+Cr' and 'U-Cr-O'; the Owen2023 corpus
    # is matrix+dopant so the matrix+dopant form is the unambiguous pick).
    # NFM-4048 AC-1 (QA warning W1): the at.%Cr ladder is pinned at every
    # concentration prod actually holds — 10/20/30/40/50, bare AND
    # amorphous-prefixed.  NFM-4037 shipped with 20/40 (bare-amorphous)
    # and 40 (bare) unpinned; they worked via substring matching but no
    # test would have caught a regression.
    ("UO2-10at.%Cr", "UO2+Cr"),
    ("UO2-20at.%Cr", "UO2+Cr"),
    ("UO2-30at.%Cr", "UO2+Cr"),
    ("UO2-40at.%Cr", "UO2+Cr"),
    ("UO2-50at.%Cr", "UO2+Cr"),
    ("amorphous UO2-10at.%Cr", "UO2+Cr"),
    ("amorphous UO2-20at.%Cr", "UO2+Cr"),
    ("amorphous UO2-30at.%Cr", "UO2+Cr"),
    ("amorphous UO2-40at.%Cr", "UO2+Cr"),
    ("amorphous UO2-50at.%Cr", "UO2+Cr"),
    ("Cr-doped UO2", "UO2+Cr"),
    ("Cr-doped amorphous UO2", "UO2+Cr"),
    ("undoped and Cr-doped amorphous UO2", "UO2+Cr"),
    # Parenthetical variants: the strip-loop doesn't reach inside the
    # parens, but the Cr/UO2 substring check on the full label still
    # resolves to 'UO2+Cr'.  ``UO2 (amorphous)`` has no Cr anywhere, so
    # it must land on the undoped 'UO2' bucket — pinning it guards the
    # has_cr gate against a future edit that keys off the paren text.
    ("amorphous UO2 (undoped and Cr-doped)", "UO2+Cr"),
    ("Cr-doped UO2 (amorphous)", "UO2+Cr"),
    ("UO2 (amorphous)", "UO2"),
    # Pass-through guards: bare Cr without UO2 must NOT be coerced into
    # 'UO2+Cr' (it would land on a label that doesn't describe its
    # chemistry).  Pre-NFM-4037 pass-through behaviour is preserved.
    ("Cr", "Cr"),
    ("Cr2O3", "Cr2O3"),
    # Non-UO2 alloys: still pass through so the existing U-10Mo /
    # polycrystalline paths keep working.
    ("U-10Mo", "U-10Mo"),
    ("polycrystalline U-10Mo", "U-10Mo"),
    ("nano-Cr", "Cr"),  # bare Cr after prefix strip; pass-through.
]


@pytest.mark.parametrize(("label", "expected"), _OWEN2023_LABELS)
def test_canonical_element_system_owen2023(label, expected):
    """NFM-4037 AC-1: every Owen2023 (source 9320cb50) Material label
    must collapse onto an F8-scorecard-recognised element_system."""
    assert _canonical_element_system(label) == expected


def test_canonical_element_system_covers_every_owen2023_label():
    """NFM-4048 AC-2 (audit pin, tightened): drive the pin from the
    PRODUCTION corpus, not from this module's own allowlist.

    NFM-4037 shipped this test comparing ``_OWEN2023_LABELS`` against
    itself, which is vacuous: it passes even when the pin covers only
    12 of the 17 labels prod holds (QA warnings W1 + W2).  The snapshot
    at ``tests/fixtures/owen2023_material_labels.json`` records every
    label in ``kg_nodes`` where ``source_id = 9320cb50-…`` and
    ``node_type = 'Material'``; this test asserts

      1. every prod label is pinned here, and
      2. the pinned expectation is what the function actually returns.

    So a corpus that grows fails the suite as soon as the snapshot is
    refreshed (``scripts/nfm-4048-refresh-owen2023-label-snapshot.py``)
    instead of drifting silently.  Extra non-prod labels in the pin
    table are allowed and wanted — they are defensive coverage for
    labels a future extraction could plausibly emit.
    """
    pinned = dict(_OWEN2023_LABELS)
    prod_labels = snapshot_labels()

    unpinned = [label for label in prod_labels if label not in pinned]
    assert not unpinned, (
        f"{len(unpinned)} production Owen2023 label(s) are not pinned in "
        f"_OWEN2023_LABELS: {unpinned}. Add each one with its expected "
        "element_system (and check the F8 scorecard predicate still "
        "accepts it) before this corpus reaches staging."
    )

    actual = {label: _canonical_element_system(label) for label in prod_labels}
    expected = {label: pinned[label] for label in prod_labels}
    assert actual == expected


def test_owen2023_pin_table_is_internally_consistent():
    """Every pinned label — prod or defensive — must still canonicalise to
    the value pinned beside it.  Guards the non-prod defensive rows
    (``crystalline UO2``, ``nano-Cr``, ``U-10Mo``, …) that the
    prod-corpus pin above does not reach."""
    actual_pairs = sorted(
        (label, _canonical_element_system(label)) for label, _ in _OWEN2023_LABELS
    )
    assert actual_pairs == sorted(set(_OWEN2023_LABELS))


def test_canonical_element_system_preserves_subscript_path():
    """Regression guard for the original NFM-3478 alloy path."""
    assert _canonical_element_system("U₂Mo") == "U-Mo"
    # Subscript plus adjective: subscript path wins (we never strip
    # adjectives on subscript-bearing labels — the alloy branch
    # returns first).
    assert _canonical_element_system("amorphous U₂Mo") == "U-Mo"


# NFM-4037 AC-3: pin the pre-existing pass-through contract for every
# label the change does NOT touch.  If a non-Owen2023 source's row gets
# renormalised, the staging surface for that source silently shifts and
# the dedup hash changes — this list is the regression guard.
_REGRESSION_LABELS: list[tuple[str, str]] = [
    # Subscript-bearing alloy notation — pre-NFM-4037 alloy path.
    ("U₂Mo", "U-Mo"),
    ("U₃Si₂", "U-Si"),
    # Plain formulas — pre-NFM-4037 pass-through.
    ("U", "U"),
    ("Mo", "Mo"),
    ("Cr", "Cr"),
    ("U3Si2", "U3Si2"),
    ("Cr2O3", "Cr2O3"),
    ("Al2O3", "Al2O3"),
    # Alloy formulas with separator — pre-NFM-4037 pass-through.
    ("U-10Mo", "U-10Mo"),
    ("Zr-4", "Zr-4"),
    # Adjective-prefixed labels whose chemistry is NOT UO2 — strip the
    # adjective but keep the chemistry.  These were previously
    # pass-through (the old code returned ``plain`` for ASCII labels);
    # the strip-loop now collapses them onto the bare chemistry too.
    # Pin the new behaviour so a future edit cannot silently regress
    # this into "polycrystalline U-10Mo" leaking back onto the staging
    # surface.
    ("polycrystalline U-10Mo", "U-10Mo"),
    ("crystalline Al2O3", "Al2O3"),
]


@pytest.mark.parametrize(("label", "expected"), _REGRESSION_LABELS)
def test_canonical_element_system_non_owen2023_pass_through(label, expected):
    """NFM-4037 AC-3: non-Owen2023 sources must not see their staging
    surface silently shift.  Every label the change does NOT touch must
    canonicalise to its pre-NFM-4037 value (or, for adjective-bearing
    non-UO2 labels, the bare chemistry)."""
    assert _canonical_element_system(label) == expected


def test_property_slug_map_covers_starikov2023_labels():
    """NFM-3478 B2'+ mapping table must cover every Property label the
    Starikov & Smirnova 2023 extraction produced (2026-08-23 rerun), so no
    row falls to the unknown/empty slug fallback."""
    labels = [
        "体积模量",
        "晶格参数a",
        "晶格参数b",
        "晶格参数c",
        "形成能",
        "混合焓",
        "相分数",
        "相变温度",
        "相变体积变化",
        "相变潜热",
        "相变类型",
        "相平衡线斜率",
        "相稳定温度下限",
        "Clausius-Clapeyron斜率",
        "弹性常数",
    ]
    for label in labels:
        assert label in _PROPERTY_SLUGS, f"missing slug mapping for {label!r}"
    # both slope labels canonicalize to the same slug (dedup-safe)
    assert _PROPERTY_SLUGS["相平衡线斜率"] == _PROPERTY_SLUGS["Clausius-Clapeyron斜率"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (97, (97.0, None)),
        ("97", (97.0, None)),
        ("110±10", (110.0, 10.0)),
        ("110 ± 10", (110.0, 10.0)),
        ("5.47 Å", (5.47, None)),  # prose salvage
        (None, None),
        ("N/A", None),
    ],
)
def test_parse_numeric(raw, expected):
    assert _parse_numeric(raw) == expected


def test_slugify_ascii_only():
    # Slug retains viewer-safe chars; non-ASCII labels fall through to
    # _PROPERTY_SLUGS lookup (callers must map before slugify).
    assert _slugify("10.1016/j.jnucmat.2023.154265") == "10.1016_j.jnucmat.2023.154265"
    assert _slugify("hello-world.tar.gz") == "hello-world.tar.gz"
    assert _slugify("体积模量") == "unknown"  # documented fallback


# --- bridge e2e (sqlite + PG-UUID-safe stubs) ------------------------------


def _stub_nodes_edges():
    """Mimic the shape of KGNode / KGEdge rows the bridge actually reads."""
    mat_u = SimpleNamespace(
        id="mat_u", node_type="Material", label="U", properties={}, confidence=0.9
    )
    mat_u2mo = SimpleNamespace(
        id="mat_u2mo", node_type="Material", label="U₂Mo", properties={}, confidence=0.6
    )
    p_bulk = SimpleNamespace(
        id="p_bulk",
        node_type="Property",
        label="体积模量",
        properties={"unit": "GPa", "value": "97"},
        confidence=0.9,
    )
    p_lat = SimpleNamespace(
        id="p_lat",
        node_type="Property",
        label="晶格参数a",
        properties={"unit": "Å", "value": "2.8552"},
        confidence=0.9,
    )
    p_slope = SimpleNamespace(
        id="p_slope",
        node_type="Property",
        label="相平衡线斜率",
        properties={"unit": "K/GPa", "value": "110±10"},
        confidence=0.9,
    )
    c_temp = SimpleNamespace(
        id="c_temp",
        node_type="Condition",
        label="temp_C=826.85",
        properties={"condition_key": "temp_C", "condition_value": "826.85"},
        confidence=0.7,
    )
    c_method = SimpleNamespace(
        id="c_method",
        node_type="Condition",
        label="simulation_method=ADP",
        properties={"condition_key": "simulation_method", "condition_value": "ADP"},
        confidence=0.7,
    )
    c_press = SimpleNamespace(
        id="c_press",
        node_type="Condition",
        label="pressure_MPa=4000",
        properties={"condition_key": "pressure_MPa", "condition_value": "4000"},
        confidence=0.7,
    )
    nodes = [mat_u, mat_u2mo, p_bulk, p_lat, p_slope, c_temp, c_method, c_press]
    # Alias-collapse case (NFM-3478 B2'+): Clausius-Clapeyron斜率 is the same
    # measurement as 相平衡线斜率 (same value 110±10, same unit) — extraction
    # emits both labels.  The bridge must collapse to one staging row.
    p_cc = SimpleNamespace(
        id="p_cc",
        node_type="Property",
        label="Clausius-Clapeyron斜率",
        properties={"unit": "K/GPa", "value": "110±10"},
        confidence=0.9,
    )
    c_cc_method = SimpleNamespace(
        id="c_cc_method",
        node_type="Condition",
        label="simulation_method=ADP MD, Clausius-Clapeyron relation",
        properties={
            "condition_key": "simulation_method",
            "condition_value": "ADP MD, Clausius-Clapeyron relation",
        },
        confidence=0.7,
    )
    nodes = [*nodes, p_cc, c_cc_method]
    edges = [
        SimpleNamespace(
            source_node_id=mat_u.id, target_node_id=p_bulk.id, relation_type="hasProperty"
        ),
        SimpleNamespace(
            source_node_id=mat_u.id, target_node_id=p_lat.id, relation_type="hasProperty"
        ),
        SimpleNamespace(
            source_node_id=mat_u.id, target_node_id=p_slope.id, relation_type="hasProperty"
        ),
        SimpleNamespace(
            source_node_id=mat_u.id, target_node_id=p_cc.id, relation_type="hasProperty"
        ),
        SimpleNamespace(
            source_node_id=mat_u2mo.id, target_node_id=p_bulk.id, relation_type="hasProperty"
        ),
        SimpleNamespace(
            source_node_id=p_bulk.id, target_node_id=c_temp.id, relation_type="hasCondition"
        ),
        SimpleNamespace(
            source_node_id=p_bulk.id, target_node_id=c_method.id, relation_type="hasCondition"
        ),
        SimpleNamespace(
            source_node_id=p_slope.id, target_node_id=c_press.id, relation_type="relatedTo"
        ),
        SimpleNamespace(
            source_node_id=p_cc.id, target_node_id=c_cc_method.id, relation_type="hasCondition"
        ),
    ]
    return nodes, edges


class _StubResult:
    """Mimics the ``result.scalars().all()`` shape used by bridge queries."""

    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _StubScalars(self._items)


class _StubScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


def _dispatch_execute(db_session, nodes, edges, pm_stubs=None):
    """Return an execute() shim: KG queries → stubs, others → real DB."""
    real_execute = db_session.execute
    pm_items = list(pm_stubs) if pm_stubs is not None else None

    async def _execute(stmt, *a, **kw):
        sql = str(stmt).lower()
        if "kg_nodes" in sql:
            return _StubResult(nodes)
        if "kg_edges" in sql:
            return _StubResult(edges)
        if pm_items is not None and "property_measurements" in sql:
            return _StubResult(pm_items)
        return await real_execute(stmt, *a, **kw)

    return _execute, real_execute


@pytest.mark.asyncio
async def test_bridge_writes_staging_rows_for_extracted_paper(db_session, monkeypatch):
    nodes, edges = _stub_nodes_edges()
    execute, real_execute = _dispatch_execute(db_session, nodes, edges)
    monkeypatch.setattr(db_session, "execute", execute)

    written = await bridge_kg_to_staging(
        db_session,
        source_id="00000000-0000-0000-0000-000000000001",
        corpus_id="Smirnov2023",
        source_doi="10.1016/x",
    )
    await db_session.flush()

    # 3 U properties + 1 U₂Mo property (bulk modulus on both materials);
    # the Clausius-Clapeyron alias collapses into 相平衡线斜率's row.
    assert written == 4

    from sqlalchemy import select as _select

    rows = (
        (
            await real_execute(
                _select(RefGapFillStaging).where(RefGapFillStaging.source == "Smirnov2023")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 4
    slope_rows = [r for r in rows if r.property_name == "phase_boundary_slope"]
    assert len(slope_rows) == 1, "alias labels must collapse to one row"
    # alias merged its richer method into the first-seen row
    assert slope_rows[0].method == "ADP MD, Clausius-Clapeyron relation"
    assert slope_rows[0].context == "pressure_MPa=4000"

    bulk_u = next(r for r in rows if r.property_name == "bulk_modulus" and r.element_system == "U")
    assert bulk_u.value == 97.0
    assert bulk_u.unit == "GPa"
    assert bulk_u.temperature == pytest.approx(826.85 + 273.15)
    assert bulk_u.method == "ADP"
    assert bulk_u.source_doi == "10.1016/x"
    assert bulk_u.confidence.value == "high"
    assert bulk_u.status == StagingStatus.PENDING

    lat = next(r for r in rows if r.property_name == "lattice_constant_a")
    assert lat.value == 2.8552
    assert lat.unit == "angstrom"

    slope = next(r for r in rows if r.property_name == "phase_boundary_slope")
    assert slope.value == 110.0
    assert slope.uncertainty == 10.0
    assert slope.context == "pressure_MPa=4000"

    bulk_u2mo = next(
        r for r in rows if r.property_name == "bulk_modulus" and r.element_system == "U-Mo"
    )
    assert bulk_u2mo.value == 97.0


@pytest.mark.asyncio
async def test_bridge_regenerates_rows_on_rerun(db_session, monkeypatch):
    """NFM-3478 B2' contract upgrade: a re-extraction of the same source
    REGENERATES its corpus rows (delete-then-write), because the new run
    may fill method/temperature fields the old hash (computed with empty
    method) would treat as duplicates. Row count stays stable, no
    cross-source leakage into other corpora."""
    nodes, edges = _stub_nodes_edges()
    execute, real_execute = _dispatch_execute(db_session, nodes, edges)
    monkeypatch.setattr(db_session, "execute", execute)

    first = await bridge_kg_to_staging(
        db_session,
        source_id="00000000-0000-0000-0000-000000000002",
        corpus_id="Smirnov2023",
        source_doi="10.1016/x",
    )
    await db_session.flush()

    second = await bridge_kg_to_staging(
        db_session,
        source_id="00000000-0000-0000-0000-000000000002",
        corpus_id="Smirnov2023",
        source_doi="10.1016/x",
    )
    await db_session.flush()

    assert first == 4
    assert second == 4  # regenerate: same corpus rows replaced, not skipped

    from sqlalchemy import func
    from sqlalchemy import select as _select

    count = (
        await real_execute(
            _select(func.count())
            .select_from(RefGapFillStaging)
            .where(RefGapFillStaging.source == "Smirnov2023")
        )
    ).scalar_one()
    assert count == 4  # exactly one generation of rows, no accumulation


# --- ADR-010 UNION tests: KG + PropertyMeasurement ------------------------
# ADR-010 D1-D6 require the bridge to read BOTH kg_nodes and
# property_measurements for the same source_uuid and collapse the two
# surfaces on the (element_system, property_name, value, unit, method)
# 5-tuple. Verification V1-V3 are unit-tested below.


import logging  # noqa: E402
import uuid as _uuid  # noqa: E402

from nfm_db.models.material import Material  # noqa: E402
from nfm_db.models.property import (  # noqa: E402
    Dataset,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.models.source import DataSource  # noqa: E402
from nfm_db.models.unit import Unit  # noqa: E402


@pytest.mark.parametrize(
    ("review_status", "expected"),
    [
        ("approved", Confidence.HIGH),
        ("pending", Confidence.MEDIUM),
        ("rejected", Confidence.LOW),
        ("", Confidence.MEDIUM),
        ("unknown_status", Confidence.MEDIUM),
        (None, Confidence.MEDIUM),
    ],
)
def test_confidence_from_property_measurement(review_status, expected):
    """ADR-010 D4: review_status → confidence mapping."""
    assert confidence_from_property_measurement(review_status) == expected


async def _seed_pm_environment(db_session, *, source_id, material_formula="U-Mo"):
    """Seed Material/PropertyType/Unit/DataSource/Dataset needed by PM path.

    Returns the (source, material, property_type, unit) tuple for tests that
    want to reference the live ORM rows. These are inserted into the real
    SQLite session via the test fixture's engine — SQLite DOES support PG's
    uuid columns when stored as VARCHAR(36) via SQLAlchemy's PG dialect
    coercion. Using the real DB keeps the SQL join path exercised (unlike
    kg_nodes, which is mocked) so the property_measurements loop can be
    tested against actual Dataset → PropertyMeasurement → Material joins.
    """
    # DataSource -- needs source_id == the source_uuid the bridge is asked to
    # bridge.  Use string UUID because PG dialect enforces typing.
    source_id_uuid = _uuid.UUID(source_id) if isinstance(source_id, str) else source_id
    source = DataSource(id=source_id_uuid, title="ADR-010 fixture source", source_type="journal")
    db_session.add(source)
    await db_session.flush()

    material = Material(
        id=_uuid.uuid4(), name="U-Mo alloy", formula=material_formula, is_active=True
    )
    db_session.add(material)
    await db_session.flush()

    category = PropertyCategory(
        id=_uuid.uuid4(),
        name="mechanical",
        slug="mechanical",
        description=None,
    )
    db_session.add(category)
    await db_session.flush()

    property_type = PropertyType(
        id=_uuid.uuid4(),
        category_id=category.id,
        name="bulk_modulus",
        slug="bulk_modulus",
        value_type="scalar",
        description=None,
    )
    db_session.add(property_type)
    await db_session.flush()

    unit = Unit(
        id=_uuid.uuid4(),
        name="gigapascal",
        symbol="GPa",
        dimension="pressure",
    )
    db_session.add(unit)
    await db_session.flush()

    dataset = Dataset(
        id=_uuid.uuid4(),
        material_id=material.id,
        source_id=source.id,
        title="ADR-010 fixture dataset",
        is_verified=False,
    )
    db_session.add(dataset)
    await db_session.flush()

    return source, material, property_type, unit, dataset


def _pm_stub(
    *,
    pm_uuid=None,
    dataset=None,
    property_type=None,
    unit=None,
    value_scalar=None,
    value_expression=None,
    value_text=None,
    method="",
    review_status="pending",
):
    """Build a SimpleNamespace that mimics a PropertyMeasurement row with
    loaded relationships (the bridge reads pm.dataset.material /
    pm.property_type.name / pm.unit.symbol).
    """
    pm_uuid = pm_uuid or _uuid.uuid4()
    # Provide either real ORM relationships (preferred) or stub attrs that
    # carry the fields the bridge actually reads.  ``material`` is read off
    # ``dataset.material``; the bridge canonicalizes its label via
    # ``_canonical_element_system(_material_label(material))``.
    material_obj = getattr(dataset, "material", None) if dataset is not None else None
    pt_obj = (
        type("PT", (), {"name": property_type.name, "id": property_type.id})()
        if property_type is not None
        else None
    )
    unit_obj = (
        type("UnitNS", (), {"symbol": unit.symbol, "id": unit.id})() if unit is not None else None
    )
    dataset_obj = (
        type("DS", (), {"material": material_obj, "id": dataset.id})()
        if dataset is not None
        else None
    )
    return SimpleNamespace(
        id=pm_uuid,
        dataset_id=dataset.id if dataset is not None else _uuid.uuid4(),
        dataset=dataset_obj,
        property_type_id=(property_type.id if property_type is not None else _uuid.uuid4()),
        property_type=pt_obj,
        unit_id=unit.id if unit is not None else _uuid.uuid4(),
        unit=unit_obj,
        value_scalar=value_scalar,
        value_expression=value_expression,
        value_text=value_text,
        value_min=None,
        value_max=None,
        value_list=None,
        uncertainty=None,
        notes=None,
        conditions=None,
        conditions_hash=None,
        method=method,
        review_status=review_status,
    )


@pytest.mark.asyncio
async def test_v1_kg_and_pm_collapse_on_5tuple_higher_confidence_wins(
    db_session, monkeypatch, caplog
):
    """ADR-010 V1: same 5-tuple from kg_nodes AND property_measurements →
    exactly one staging row; higher-confidence value wins; one structured
    ``bridge.dedup.collapse`` log entry per collapse.
    """
    source_id = "00000000-0000-0000-0000-00000000000a"
    source, material, property_type, unit, dataset = await _seed_pm_environment(
        db_session, source_id=source_id
    )

    # Property node carrying the same measurement at HIGH confidence (via
    # KG confidence = 0.9 → _confidence_from_kg returns HIGH).  Also give
    # the KG surface a `simulation_method` condition so the winner-overwrites
    # semantic is observable: if kg_nodes wins, its method ("DFT") is the
    # method on the staging row.
    p_v1 = SimpleNamespace(
        id="p_v1",
        node_type="Property",
        label="bulk_modulus",
        properties={"unit": "GPa", "value": "100"},
        confidence=0.9,
    )
    mat_v1 = SimpleNamespace(
        id="mat_v1", node_type="Material", label="U-Mo", properties={}, confidence=0.9
    )
    c_method_v1 = SimpleNamespace(
        id="c_method_v1",
        node_type="Condition",
        label="simulation_method=DFT",
        properties={
            "condition_key": "simulation_method",
            "condition_value": "DFT",
        },
        confidence=0.7,
    )
    edges_v1 = [
        SimpleNamespace(
            source_node_id=mat_v1.id, target_node_id=p_v1.id, relation_type="hasProperty"
        ),
        SimpleNamespace(
            source_node_id=p_v1.id,
            target_node_id=c_method_v1.id,
            relation_type="hasCondition",
        ),
    ]
    pm = _pm_stub(
        dataset=dataset,
        property_type=property_type,
        unit=unit,
        value_scalar=100.0,
        method="ADP",
        review_status="pending",
    )

    execute, real_execute = _dispatch_execute(
        db_session,
        [mat_v1, p_v1, c_method_v1],
        edges_v1,
        pm_stubs=[pm],
    )
    monkeypatch.setattr(db_session, "execute", execute)

    caplog.set_level(logging.INFO, logger="nfm_db.services.kg_to_staging_bridge")
    written = await bridge_kg_to_staging(
        db_session, source_id=source_id, corpus_id="ADR010V1", source_doi="10.0000/v1"
    )
    await db_session.flush()

    from sqlalchemy import select as _select

    rows = (
        (
            await real_execute(
                _select(RefGapFillStaging).where(RefGapFillStaging.source == "ADR010V1")
            )
        )
        .scalars()
        .all()
    )
    assert written == 1, f"expected exactly 1 staging row, got {written}"
    assert len(rows) == 1
    row = rows[0]
    # Same 5-tuple key, kg surface had confidence 0.9 (HIGH), pm surface
    # had pending (MEDIUM).  Higher wins → HIGH survives.
    assert row.confidence == Confidence.HIGH
    assert row.value == pytest.approx(100.0)
    assert row.unit == "GPa"
    assert row.property_name == "bulk_modulus"
    assert row.element_system == "U-Mo"
    assert row.method == "DFT"  # kg_nodes (HIGH) won → its method survives

    # Structured collapse log entry per ADR-010 D4.  The bridge uses
    # ``logger.info("event-name", extra={...})`` so each extra becomes a
    # direct attribute on the LogRecord (not a ``payload`` dict).
    collapse_logs = [rec for rec in caplog.records if rec.message == "bridge.dedup.collapse"]
    assert len(collapse_logs) == 1, f"expected 1 collapse log entry, got {len(collapse_logs)}"
    rec = collapse_logs[0]
    assert rec.confidence_winner == "kg_nodes"
    assert rec.confidence_value == "high"
    assert rec.property_measurement_uuid == str(pm.id)
    assert rec.element_system == "U-Mo"
    assert rec.property_name == "bulk_modulus"


@pytest.mark.asyncio
async def test_v2_pm_only_numeric_row_uses_review_status_confidence(db_session, monkeypatch):
    """ADR-010 V2: property_measurements-only row with ``value_scalar``
    IS NOT NULL → one staging row whose confidence comes from the
    ``review_status`` mapping (pending → MEDIUM).
    """
    source_id = "00000000-0000-0000-0000-00000000000b"
    _source, _material, property_type, unit, dataset = await _seed_pm_environment(
        db_session, source_id=source_id
    )
    pm = _pm_stub(
        dataset=dataset,
        property_type=property_type,
        unit=unit,
        value_scalar=97.0,
        method="tensile",
        review_status="pending",
    )
    execute, real_execute = _dispatch_execute(db_session, [], [], pm_stubs=[pm])
    monkeypatch.setattr(db_session, "execute", execute)

    written = await bridge_kg_to_staging(
        db_session, source_id=source_id, corpus_id="ADR010V2", source_doi="10.0000/v2"
    )
    await db_session.flush()

    from sqlalchemy import select as _select

    rows = (
        (
            await real_execute(
                _select(RefGapFillStaging).where(RefGapFillStaging.source == "ADR010V2")
            )
        )
        .scalars()
        .all()
    )
    assert written == 1
    assert len(rows) == 1
    row = rows[0]
    assert row.value == pytest.approx(97.0)
    assert row.unit == "GPa"
    assert row.method == "tensile"
    # pending → MEDIUM (D4 mapping)
    assert row.confidence == Confidence.MEDIUM
    assert row.element_system == "U-Mo"
    assert row.property_name == "bulk_modulus"


@pytest.mark.asyncio
async def test_v3_pm_value_expression_only_no_numeric_row(db_session, monkeypatch, caplog):
    """ADR-010 V3: PropertyMeasurement with only ``value_expression`` /
    ``value_text`` (no ``value_scalar``) → no numeric staging row is
    written; the expression text is appended to a context-only entry.

    Same source is also queried for kg_nodes, which produces an unrelated
    numeric row; the PM-only text path must NOT produce a second row.
    """
    source_id = "00000000-0000-0000-0000-00000000000c"
    _source, _material, property_type, unit, dataset = await _seed_pm_environment(
        db_session, source_id=source_id
    )
    pm = _pm_stub(
        dataset=dataset,
        property_type=property_type,
        unit=unit,
        value_scalar=None,
        value_expression="exp(-Ea/kT)",
        value_text=None,
        method="empirical fit",
        review_status="approved",
    )
    execute, real_execute = _dispatch_execute(db_session, [], [], pm_stubs=[pm])
    monkeypatch.setattr(db_session, "execute", execute)

    caplog.set_level(logging.INFO, logger="nfm_db.services.kg_to_staging_bridge")
    written = await bridge_kg_to_staging(
        db_session, source_id=source_id, corpus_id="ADR010V3", source_doi="10.0000/v3"
    )
    await db_session.flush()

    from sqlalchemy import select as _select

    rows = (
        (
            await real_execute(
                _select(RefGapFillStaging).where(RefGapFillStaging.source == "ADR010V3")
            )
        )
        .scalars()
        .all()
    )
    # No numeric row from PM (KG side is empty in this test).
    assert written == 0, f"expected no numeric staging row, wrote {written}"
    assert len(rows) == 0, "no numeric staging row should exist for value_expression-only PM"
    # PM row still surfaces as context for downstream visibility.
    pm_logs = [rec for rec in caplog.records if rec.message == "bridge.pm.expression_only"]
    assert len(pm_logs) == 1, "expected 1 expression-only log entry to carry the text into context"


# --- NFM-4038: bridge must not lazy-load pm.dataset.material ----------------
#
# Production crash on the deployed image:
#
#     sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called;
#       can't call await_only() here. Was IO attempted in an unexpected place?
#
# The bridge's PM select() set up
#     selectinload(PropertyMeasurement.dataset)
# but the loop at kg_to_staging_bridge.py:369 reads
#     pm.dataset.material
# — a *nested* relationship on ``Dataset`` that is NOT covered by the
# first selectinload.  asyncpg rejects the sync IO that the implicit
# lazy load triggers inside the async greenlet.
#
# Two-part regression test:
#  (1) The bridge's PM select() must chain
#      ``selectinload(PropertyMeasurement.dataset).selectinload(Dataset.material)``
#      so the Material row is fetched in the second selectinload IN query
#      rather than as a per-row lazy load inside the loop.  Pinned via
#      statement introspection so this test fails BEFORE the fix and
#      passes AFTER.
#  (2) The end-to-end bridge call against a real PropertyMeasurement
#      row must complete without raising MissingGreenlet.  Column-level
#      accesses on ``pm.property_type.name`` / ``pm.unit.symbol`` do NOT
#      need eager loading (they are columns on already-loaded rows);
#      only the nested ``Dataset → Material`` relationship does.


@pytest.mark.asyncio
async def test_nfm4038_pm_query_chains_dataset_material_selectinload(db_session, monkeypatch):
    """NFM-4038 AC-2: the bridge's PropertyMeasurement select() must
    chain ``selectinload(PropertyMeasurement.dataset).selectinload(Dataset.material)``
    so the loop reading ``pm.dataset.material`` does not trigger a
    per-row lazy load.

    Before the fix the query only carries the outer selectinload; the
    Material relationship is left to implicit lazy loading, which
    asyncpg rejects with ``MissingGreenlet`` inside the async greenlet.

    We assert on the SQLAlchemy ``Load`` option tree attached to the
    captured PM select: the chained option's path must terminate at the
    ``Material`` mapper.  This is the public, documented API for
    inspecting loader strategies in SQLAlchemy 2.x and does not depend
    on private internals.
    """
    from sqlalchemy.orm import Load
    from sqlalchemy.sql import Select

    source_id = "00000000-0000-0000-0000-00000000d038"
    _source, _material, property_type, unit, dataset = await _seed_pm_environment(
        db_session, source_id=source_id
    )

    # Real PM row so the same dialect/SQLAlchemy codepath runs in test.
    pm = PropertyMeasurement(
        id=_uuid.uuid4(),
        dataset_id=dataset.id,
        property_type_id=property_type.id,
        unit_id=unit.id,
        value_scalar=42.0,
        method="DFT",
        conditions_hash="x" * 40,
    )
    db_session.add(pm)
    await db_session.flush()

    captured_stmts: list[Select] = []
    real_execute = db_session.execute

    async def _capturing_execute(stmt, *a, **kw):
        sql = str(stmt).lower()
        # Capture the bridge's PM select so we can introspect its
        # loader option tree.  Other queries (staging insert, KG queries)
        # are not relevant here.
        if "property_measurements" in sql and isinstance(stmt, Select):
            captured_stmts.append(stmt)
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "execute", _capturing_execute)

    written = await bridge_kg_to_staging(
        db_session,
        source_id=source_id,
        corpus_id="NFM4038",
        source_doi="10.0000/nfm4038",
    )
    assert written == 1

    assert len(captured_stmts) == 1, (
        f"bridge must issue exactly one PM select; captured {len(captured_stmts)}"
    )
    pm_stmt = captured_stmts[0]

    # SQLAlchemy exposes loader options via ``_with_options``; each entry
    # is a ``Load`` whose ``.path`` walks the chain.  Chained
    # ``selectinload(A.b).selectinload(B.c)`` produces a single ``Load``
    # whose path is [A mapper, A.b rel, B mapper, B.c rel, C mapper].
    # We walk the path of every Load and look for the Material mapper.
    load_options = [o for o in (pm_stmt._with_options or ()) if isinstance(o, Load)]
    assert load_options, "bridge PM select has no loader Load options"

    def _path_targets(load: Load, target_cls: type) -> bool:
        """True iff the loader's chain reaches the given ORM class."""
        path = getattr(load, "path", None)
        if path is None:
            return False
        for node in path:
            cls = getattr(node, "class_", None)
            if cls is target_cls:
                return True
            mapper = getattr(node, "mapper", None)
            if mapper is not None and getattr(mapper, "class_", None) is target_cls:
                return True
        return False

    assert any(_path_targets(opt, Material) for opt in load_options), (
        "NFM-4038 regression: bridge PM select does NOT chain "
        "selectinload(Dataset.material); the loader option tree has "
        f"no Load whose path reaches Material.  Options seen: "
        f"{[(type(o).__name__, getattr(o, 'path', None)) for o in load_options]}"
    )

    # Outer datasets selectinload must still be present so ``pm.dataset``
    # itself does not lazy-load.
    assert any(_path_targets(opt, Dataset) for opt in load_options), (
        "bridge PM select lost selectinload(PropertyMeasurement.dataset); "
        f"options: {[type(o).__name__ for o in load_options]}"
    )


@pytest.mark.asyncio
async def test_nfm4038_bridge_does_not_lazy_load_pm_dataset_material(db_session, monkeypatch):
    """NFM-4038 AC-1 + AC-3: running the bridge against a real
    PropertyMeasurement row does not raise MissingGreenlet.

    The previous test pins the eager-loading shape; this test pins the
    end-to-end behaviour: the bridge completes the PM loop on a real
    row whose ``Dataset.material`` relationship is fetched through the
    same selectinload chain.  If a future change drops the chain (or
    replaces selectinload with a lazy strategy) the loop crashes here
    on asyncpg-backed sessions and the bridge is unusable on production.
    """
    source_id = "00000000-0000-0000-0000-00000000d039"
    _source, _material, property_type, unit, dataset = await _seed_pm_environment(
        db_session, source_id=source_id
    )

    pm = PropertyMeasurement(
        id=_uuid.uuid4(),
        dataset_id=dataset.id,
        property_type_id=property_type.id,
        unit_id=unit.id,
        value_scalar=12.5,
        method="ADP",
        conditions_hash="y" * 40,
    )
    db_session.add(pm)
    await db_session.flush()

    written = await bridge_kg_to_staging(
        db_session,
        source_id=source_id,
        corpus_id="NFM4038E2E",
        source_doi="10.0000/nfm4038e2e",
    )
    assert written == 1

    from sqlalchemy import select as _select

    rows = (
        (
            await db_session.execute(
                _select(RefGapFillStaging).where(RefGapFillStaging.source == "NFM4038E2E")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    # Element-system + property name confirm pm.dataset.material
    # AND pm.property_type.name were accessible inside the async loop.
    assert row.element_system == "U-Mo"
    assert row.property_name == "bulk_modulus"
    assert row.value == pytest.approx(12.5)
    assert row.unit == "GPa"
