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

from nfm_db.models.ref_gap_fill import RefGapFillStaging, StagingStatus
from nfm_db.services.kg_to_staging_bridge import (
    _PROPERTY_SLUGS,
    _canonical_element_system,
    _parse_numeric,
    _slugify,
    bridge_kg_to_staging,
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


def _dispatch_execute(db_session, nodes, edges):
    """Return an execute() shim: KG queries → stubs, others → real DB."""
    real_execute = db_session.execute

    async def _execute(stmt, *a, **kw):
        sql = str(stmt).lower()
        if "kg_nodes" in sql:
            return _StubResult(nodes)
        if "kg_edges" in sql:
            return _StubResult(edges)
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

    # 3 U properties + 1 U₂Mo property (bulk modulus on both materials).
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
