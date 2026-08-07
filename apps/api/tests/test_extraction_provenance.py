"""Per-item extraction provenance (NFM-2247).

Unblocks NFM-2237: the ``ProvenanceBadge`` on the literature DetailPanel reads a
per-item provenance token and renders ``来源未知`` when it is absent. These tests
pin the four halves of the contract:

1. The token vocabulary and the parse/append helpers (``services.provenance``).
2. The persisted ``extraction_method`` column on every table that feeds the
   literature-detail ``extraction_results`` array.
3. The producing paths — LLM pipeline writes ``llm``, human correction appends
   ``manual``, the figure pipeline writes ``mineru``.
4. The wire shape: every item in ``extraction_results`` carries ``provenance``.

Provenance is *persisted at write time*, never inferred from ``confidence`` or
``review_status`` at read time — see the AC on NFM-2247.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_figure import ExtractionFigure
from nfm_db.models.extraction_result import ExtractionResult
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.models.source import DataSource
from nfm_db.services.provenance import (
    KNOWN_PROVENANCE,
    PROVENANCE_LLM,
    PROVENANCE_MANUAL,
    PROVENANCE_MINERU,
    add_provenance,
    parse_provenance,
)

_API_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION = (
    _API_ROOT / "migrations" / "versions" / "039_add_extraction_method_provenance.py"
)

# ---------------------------------------------------------------------------
# 1. Token vocabulary
# ---------------------------------------------------------------------------


def test_tokens_match_frontend_contract() -> None:
    """The three tokens are exactly what ProvenanceBadge accepts."""
    assert PROVENANCE_LLM == "llm"
    assert PROVENANCE_MANUAL == "manual"
    assert PROVENANCE_MINERU == "mineru"
    assert KNOWN_PROVENANCE == ("llm", "manual", "mineru")


# ---------------------------------------------------------------------------
# 2. parse_provenance — read side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        ("llm", ["llm"]),
        ("  LLM  ", ["llm"]),
        ("llm,manual", ["llm", "manual"]),
        ("llm, manual", ["llm", "manual"]),
        ("llm,,manual", ["llm", "manual"]),
        ("llm,llm", ["llm"]),
        ("MinerU", ["mineru"]),
        (["llm", "manual"], ["llm", "manual"]),
        (["llm", "LLM"], ["llm"]),
        # Unknown tokens are dropped rather than passed through — the badge
        # renders anything unrecognised as 来源未知 anyway, and silently
        # forwarding junk would make the wire contract unverifiable.
        ("heuristic_type_pair", []),
        ("llm,heuristic", ["llm"]),
    ],
)
def test_parse_provenance(raw: object, expected: list[str]) -> None:
    assert parse_provenance(raw) == expected


def test_parse_provenance_uses_canonical_order() -> None:
    """Order is canonical (llm, manual, mineru), not insertion order.

    The client applies manual > mineru > llm itself, so server order is only
    about producing a stable, diffable string.
    """
    assert parse_provenance("mineru,llm,manual") == ["llm", "manual", "mineru"]


# ---------------------------------------------------------------------------
# 3. add_provenance — write side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "token", "expected"),
    [
        (None, PROVENANCE_MANUAL, "manual"),
        ("", PROVENANCE_LLM, "llm"),
        ("llm", PROVENANCE_MANUAL, "llm,manual"),
        ("mineru", PROVENANCE_MANUAL, "manual,mineru"),
        # Idempotent: correcting twice must not grow the string.
        ("llm,manual", PROVENANCE_MANUAL, "llm,manual"),
        ("manual", PROVENANCE_MANUAL, "manual"),
    ],
)
def test_add_provenance(existing: str | None, token: str, expected: str) -> None:
    assert add_provenance(existing, token) == expected


def test_add_provenance_rejects_unknown_token() -> None:
    with pytest.raises(ValueError, match="unknown provenance token"):
        add_provenance("llm", "telepathy")


# ---------------------------------------------------------------------------
# 4. Persisted columns exist on every table feeding extraction_results
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [ExtractionResult, KGNode, KGEdge, ExtractionFigure],
)
def test_model_has_extraction_method_column(model: type) -> None:
    assert "extraction_method" in model.__table__.columns


@pytest.mark.parametrize("model", [ExtractionResult, KGNode, KGEdge])
def test_extraction_method_is_nullable(model: type) -> None:
    """NULL is the documented backfill value: explicit unknown, not a guess."""
    assert model.__table__.columns["extraction_method"].nullable is True


@pytest.mark.parametrize("model", [ExtractionResult, KGNode, KGEdge])
def test_extraction_method_has_no_server_default(model: type) -> None:
    """A DB default would silently stamp provenance onto a forgetful INSERT."""
    assert model.__table__.columns["extraction_method"].server_default is None


@pytest.mark.asyncio
async def test_figure_rows_default_to_mineru(db_session: AsyncSession) -> None:
    """Figures are only ever produced by the MinerU/VLM figure pipeline."""
    figure_id = uuid.uuid4()
    db_session.add(ExtractionFigure(id=figure_id, page_number=1, figure_type="plot"))
    await db_session.commit()

    stored = (
        await db_session.execute(
            select(ExtractionFigure).where(ExtractionFigure.id == figure_id)
        )
    ).scalar_one()
    assert parse_provenance(stored.extraction_method) == [PROVENANCE_MINERU]


# ---------------------------------------------------------------------------
# 5. Migration chains off the previous head and adds the three columns
# ---------------------------------------------------------------------------


def _load_migration() -> ModuleType:
    """Import the 037 revision by path — ``037_...`` is not an identifier."""
    spec = importlib.util.spec_from_file_location("nfm_migration_039", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_exists_and_chains_off_previous_head() -> None:
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"
    module = _load_migration()
    assert module.revision == "039_add_extraction_method_provenance"
    assert module.down_revision == "038_merge_health_events_and_ref_gap"


def test_migration_covers_all_three_tables() -> None:
    module = _load_migration()
    assert module._TABLES == ("extraction_results", "kg_nodes", "kg_edges")
    for table in module._TABLES:
        expected = (
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "extraction_method VARCHAR(100)"
        )
        assert expected in module._PG_ADD_COLUMNS, module._PG_ADD_COLUMNS


def test_migration_adds_no_server_default() -> None:
    """The PG DDL must not carry a DEFAULT — see the backfill note in 037."""
    module = _load_migration()
    for statement in module._PG_ADD_COLUMNS:
        assert "DEFAULT" not in statement.upper(), statement


def test_alembic_has_a_single_head() -> None:
    """A second head would break the ``alembic upgrade head`` container start.

    NFM-2029 introduced migration 040 (down_revision=041_merge_010_and_039)
    which unifies the chain so 040 is now the single head. We accept any
    of the three legitimate single-head states: 039 (legacy), 040 (post
    NFM-2029), or 041 (the merge migration, should never be head in
    practice because 040 chains off it).
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_API_ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"single alembic head invariant violated — NFM-167 gate: {heads}"
    )
    assert heads[0] in {
        "039_add_extraction_method_provenance",
        "040_create_sync_operations",
        "041_merge_010_and_039",
        "042_extraction_step_and_chunk",
        "043_add_domain_expert_role",
        "044_add_ontology_version",
        "045_add_re_extraction_queue",
        "046_add_knowledge_gaps",
        "047_extraction_gap",
    }


# ---------------------------------------------------------------------------
# 6. Producing paths write the right token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_pipeline_node_records_llm(db_session: AsyncSession) -> None:
    """GraphBuilder._create_node stamps ``llm`` without consulting confidence."""
    from nfm_db.services.kg_re import ExtractedEntity, GraphBuilder

    builder = GraphBuilder(session=db_session, sync_to_age=False)
    node = await builder._create_node(
        ExtractedEntity(
            label="UO2",
            entity_type="Material",
            confidence=0.9,
            properties={},
            source_id=None,
            aliases=[],
        )
    )
    assert node.extraction_method == PROVENANCE_LLM

    # A low-confidence entity is still LLM-produced — provenance must not
    # track the review verdict.
    low = await builder._create_node(
        ExtractedEntity(
            label="PuO2",
            entity_type="Material",
            confidence=0.1,
            properties={},
            source_id=None,
            aliases=[],
        )
    )
    assert low.extraction_method == PROVENANCE_LLM


@pytest.mark.asyncio
async def test_human_correction_appends_manual(
    db_session: AsyncSession,
    async_client,
    reviewer_headers: dict[str, str],
) -> None:
    """An LLM item corrected by a human reports both tokens."""
    result = ExtractionResult(
        id=uuid.uuid4(),
        property_name="thermal_conductivity",
        item_type="property",
        item_data={"value": 8.5},
        value=8.5,
        confidence=0.7,
        review_status="needs_revision",
        extraction_method=PROVENANCE_LLM,
    )
    db_session.add(result)
    await db_session.commit()

    response = await async_client.patch(
        f"/api/v1/review/{result.id}",
        json={"status": "corrected", "note": "unit was wrong"},
        headers=reviewer_headers,
    )
    assert response.status_code == 200, response.text

    await db_session.refresh(result)
    assert parse_provenance(result.extraction_method) == ["llm", "manual"]


@pytest.mark.asyncio
async def test_approval_does_not_add_manual(
    db_session: AsyncSession,
    async_client,
    reviewer_headers: dict[str, str],
) -> None:
    """Approving is a verdict, not authorship — it must not claim ``manual``."""
    result = ExtractionResult(
        id=uuid.uuid4(),
        property_name="melting_point",
        item_type="property",
        item_data={"value": 3120},
        value=3120,
        confidence=0.95,
        review_status="pending",
        extraction_method=PROVENANCE_LLM,
    )
    db_session.add(result)
    await db_session.commit()

    response = await async_client.patch(
        f"/api/v1/review/{result.id}",
        json={"status": "approved", "note": "looks right"},
        headers=reviewer_headers,
    )
    assert response.status_code == 200, response.text

    await db_session.refresh(result)
    assert parse_provenance(result.extraction_method) == ["llm"]


# ---------------------------------------------------------------------------
# 7. Wire contract — every literature-detail item carries provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_literature_detail_emits_provenance_on_every_item(
    db_session: AsyncSession,
    async_client,
) -> None:
    source_id = uuid.uuid4()
    db_session.add(
        DataSource(
            id=source_id,
            title="Thermal properties of UO2",
            source_type="literature",
            parse_status="completed",
        )
    )
    # Flush the parent before the children: several test tables share FKs into
    # data_sources and the unit-of-work ordering is not reliable enough here.
    await db_session.flush()
    # Legacy branch: one llm-extracted, one human-corrected, one legacy NULL.
    db_session.add_all(
        [
            ExtractionResult(
                id=uuid.uuid4(),
                source_id=source_id,
                property_name="k",
                item_type="property",
                item_data={},
                value=8.5,
                confidence=0.8,
                extraction_method=PROVENANCE_LLM,
            ),
            ExtractionResult(
                id=uuid.uuid4(),
                source_id=source_id,
                property_name="cp",
                item_type="property",
                item_data={},
                value=235.0,
                confidence=0.6,
                extraction_method="llm,manual",
            ),
            ExtractionResult(
                id=uuid.uuid4(),
                source_id=source_id,
                property_name="legacy",
                item_type="property",
                item_data={},
                value=1.0,
                confidence=0.5,
                extraction_method=None,
            ),
        ]
    )
    # KG branch: a node pair plus the edge between them.
    node_a, node_b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            KGNode(
                id=node_a,
                node_type="Material",
                label="UO2",
                properties={},
                confidence=0.9,
                source_id=source_id,
                extraction_method=PROVENANCE_LLM,
            ),
            KGNode(
                id=node_b,
                node_type="Property",
                label="thermal_conductivity",
                properties={},
                confidence=0.9,
                source_id=source_id,
                extraction_method=PROVENANCE_LLM,
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        KGEdge(
            id=uuid.uuid4(),
            source_node_id=node_a,
            target_node_id=node_b,
            relation_type="hasProperty",
            properties={},
            confidence=0.9,
            source_id=source_id,
            extraction_method=PROVENANCE_LLM,
        )
    )
    db_session.add(
        ExtractionFigure(
            id=uuid.uuid4(),
            source_id=source_id,
            page_number=3,
            figure_type="plot",
            confidence=0.7,
        )
    )
    await db_session.commit()

    response = await async_client.get(f"/api/v1/literature/{source_id}")
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    items = data["extraction_results"]
    assert len(items) == 6, [item["property_name"] for item in items]

    # AC #1 — every item carries the key, no exceptions.
    for item in items:
        assert "provenance" in item, item

    by_name = {item["property_name"]: item["provenance"] for item in items}
    assert by_name["k"] == ["llm"]
    # AC #3 — a human-corrected item reports manual.
    assert by_name["cp"] == ["llm", "manual"]
    # AC #4 — legacy rows are explicitly unknown, not guessed.
    assert by_name["legacy"] == []
    assert by_name["UO2"] == ["llm"]
    assert by_name["hasProperty"] == ["llm"]

    # Figures carry the MinerU token so the badge can label them MinerU图.
    assert data["figures"][0]["provenance"] == ["mineru"]
