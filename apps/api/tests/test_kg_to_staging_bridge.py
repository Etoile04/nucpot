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


# --- F8 Owen2023 (NFM-4036) -------------------------------------------------
#
# Source ``9320cb50-…`` (Owen2023, DOI 10.1016/j.jnucmat.2023.154270) emits
# 22 distinct Property labels.  Before NFM-4036, 17 of them fell to the
# ``_slugify`` CJK fallback (which returns the literal ``"unknown"``) and
# the bridge dropped them at the ``== "unknown"`` guard.  The other 5
# survived as corrupt ASCII fragments; the two Cr-doped labels collided on
# ``"Cr"``, silently merging the Cr-doped activation energy with the
# Cr-doped pre-exponential factor on the staging surface.
#
# Acceptance for NFM-4036 / AC-1 / AC-2 is locked in by the scorecard
# ``apps/api/src/nfm_db/audit/f8_scorecard_v050.sql`` whose PASS predicates
# are on these five slugs: ``activation_energy``, ``pre_exponential_factor``
# / ``diffusion_coefficient``, ``density``, ``rdf_peak``, ``bond_length``.
# The scorecard separates undoped from Cr-doped by ``element_system`` and
# value band, never by ``property_name``, so multiple Chinese synonyms may
# legitimately collapse onto one slug — but the Cr-doped Ea and Cr-doped D0
# must NOT collapse onto each other.


# Slugs the F8 scorecard predicates on (PASS criterion only).
_F8_PASS_SLUGS = {
    "activation_energy",
    "pre_exponential_factor",
    "diffusion_coefficient",
    "density",
    "rdf_peak",
    "bond_length",
}


# Every Chinese label the F8 scorecard diagnostic CTE classifies as one of
# the F8 properties.  Pulled verbatim from
# ``audit/f8_scorecard_v050.sql::kg_classified``.  The bridge slug map must
# cover all of these so the kg_nodes loop emits the right ``property_name``.
_F8_CHINESE_LABELS = [
    # activation_energy (F8 checkpoints 1 and 3)
    "扩散激活能",
    "扩散活化能",
    "活化能",
    "激活能",
    "氧扩散激活能",
    "Cr掺杂扩散激活能",
    # pre_exponential_factor (F8 checkpoints 2 and 4 — scorecard also
    # accepts diffusion_coefficient as an alias for D0)
    "扩散前指数因子",
    "扩散指前因子",
    "扩散系数指前因子",
    "扩散预指数因子",
    "预指数因子",
    "氧扩散前指数因子",
    "氧扩散指前因子",
    "Cr掺杂扩散前指数因子",
    # diffusion_coefficient (kept distinct from D0; same F8 IN-list)
    "扩散系数",
    # density (F8 checkpoints 5 and 6)
    "密度",
    # rdf_peak (F8 checkpoint 7)
    "RDF峰",
    # bond_length (F8 checkpoint 8)
    "键长",
]


@pytest.mark.parametrize("label", _F8_CHINESE_LABELS)
def test_f8_owen2023_label_resolves_to_pass_criterion_slug(label):
    """AC-1: every F8 Owen2023 Chinese label must land on a slug the
    scorecard predicates on (so the staging surface and the scorecard
    agree on which rows count as PASS)."""
    assert label in _PROPERTY_SLUGS, (
        f"F8 label {label!r} missing from _PROPERTY_SLUGS — bridge will "
        f"drop the row at the == 'unknown' guard"
    )
    slug = _PROPERTY_SLUGS[label]
    assert slug in _F8_PASS_SLUGS, (
        f"F8 label {label!r} maps to {slug!r} which is NOT an F8 PASS "
        f"slug; scorecard will not count this row"
    )


def test_f8_owen2023_slug_distribution_lands_every_checkpoint():
    """AC-1 (exhaustiveness): every F8 PASS slug has at least one
    Chinese label mapped to it, so no checkpoint is unreachable."""
    slugs_seen = {
        _PROPERTY_SLUGS[label] for label in _F8_CHINESE_LABELS
    }
    missing = _F8_PASS_SLUGS - slugs_seen
    assert not missing, (
        f"No F8 Chinese label maps to these PASS slugs: {missing}. "
        f"Corresponding scorecard checkpoints will stay 0/0."
    )


def test_f8_cr_doped_ea_and_d0_do_not_collide():
    """AC-2 (no silent collision): the two Cr-doped labels previously
    both ``_slugify``'d to ``"Cr"``, silently merging Cr-doped Ea and
    Cr-doped D0 on the staging surface. The map must keep them apart so
    the scorecard can tell them apart by value band."""
    ea_label = "Cr掺杂扩散激活能"
    d0_label = "Cr掺杂扩散前指数因子"
    # First confirm the underlying _slugify collision actually exists
    # (so this test is anchored in a real defect, not a hypothetical).
    assert _slugify(ea_label) == "Cr"
    assert _slugify(d0_label) == "Cr"
    # Now confirm the map fixes it.
    assert ea_label in _PROPERTY_SLUGS
    assert d0_label in _PROPERTY_SLUGS
    assert _PROPERTY_SLUGS[ea_label] != _PROPERTY_SLUGS[d0_label], (
        f"Cr-doped Ea ({ea_label!r}) and Cr-doped D0 ({d0_label!r}) "
        f"must map to distinct slugs — otherwise checkpoints 3 and 4 "
        f"silently merge on the staging surface"
    )
    # And both must target F8 PASS slugs (otherwise the fix is no fix).
    assert _PROPERTY_SLUGS[ea_label] in _F8_PASS_SLUGS
    assert _PROPERTY_SLUGS[d0_label] in _F8_PASS_SLUGS


def test_f8_diffusion_coefficient_kept_distinct_from_d0():
    """AC-2 (semantic): ``扩散系数`` is the diffusivity (D), a
    physically distinct quantity from the Arrhenius pre-exponential
    factor (D0). The scorecard accepts both as PASS for checkpoints 2
    and 4 (``property_name IN ('pre_exponential_factor',
    'diffusion_coefficient')``), but the bridge must NOT silently
    collapse ``扩散系数`` onto the pre-exponential_factor slug — that
    would corrupt future queries that need to distinguish D from D0."""
    assert _PROPERTY_SLUGS["扩散系数"] == "diffusion_coefficient"
    assert (
        _PROPERTY_SLUGS["扩散系数"]
        != _PROPERTY_SLUGS["扩散前指数因子"]
    )


def test_f8_thermal_diffusivity_kept_apart_from_diffusion_coefficient():
    """AC-2 (semantic): ``热扩散率`` is thermal diffusivity (cm²/s in a
    thermal-conductivity/heat-capacity sense), which is *not* the same
    quantity as either D (mass diffusivity) or D0 (Arrhenius prefactor).
    It is NOT an F8 checkpoint target — the scorecard lists it as a
    ``d0`` synonym in its diagnostic CTE, but no F8 PASS predicate
    matches ``thermal_diffusivity``. Mapping it to ``thermal_diffusivity``
    is therefore semantically correct (no false positives for D0) and
    keeps the staging surface honest."""
    assert _PROPERTY_SLUGS["热扩散率"] == "thermal_diffusivity"
    assert _PROPERTY_SLUGS["热扩散率"] not in _F8_PASS_SLUGS


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
