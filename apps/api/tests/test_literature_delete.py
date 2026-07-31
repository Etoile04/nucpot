"""Regression tests for DELETE /api/v1/literature/{id} with child FK rows.

NFM-2212 — Ensures the raw-SQL delete endpoint handles child rows in
every FK table correctly (CASCADE or SET NULL) without IntegrityError.

Background: The original ORM-based delete emitted ``UPDATE datasets SET
source_id=NULL`` which violated NOT NULL (commit fe4a509). The hot-fix
switches to raw SQL ``DELETE FROM data_sources WHERE id = :sid`` so
PostgreSQL's ON DELETE CASCADE / ON DELETE SET NULL constraints handle
child rows automatically.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_figure import ExtractionFigure
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.models.material import Material
from nfm_db.models.property import Dataset
from nfm_db.models.source import Author, DataSource, DataSourceAuthor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_source(db: AsyncSession, sid: uuid.UUID | None = None) -> DataSource:
    """Create a minimal DataSource and flush (does NOT commit)."""
    source = DataSource(
        id=sid or uuid.uuid4(),
        title="Delete Regression Source",
        source_type="journal_article",
    )
    db.add(source)
    await db.flush()
    return source


# ---------------------------------------------------------------------------
# Child-row setup callbacks
# Each creates a child row referencing *source_id* and returns metadata
# needed for post-delete verification.
# ---------------------------------------------------------------------------


async def _setup_dataset(db: AsyncSession, source: DataSource) -> dict[str, Any]:
    """datasets.source_id → CASCADE.

    Requires a Material row (datasets FK to materials.id).
    """
    material = Material(name="UO2-Regression")
    db.add(material)
    await db.flush()

    dataset = Dataset(
        material_id=material.id,
        source_id=source.id,
        title="Regression Dataset",
    )
    db.add(dataset)
    await db.flush()
    return {"child_id": dataset.id, "ondelete": "cascade"}


async def _setup_author_link(
    db: AsyncSession, source: DataSource
) -> dict[str, Any]:
    """data_source_authors.data_source_id → CASCADE.

    Requires an Author row (junction FK to authors.id).
    """
    author = Author(full_name="Regression Author", last_name="Author")
    db.add(author)
    await db.flush()

    link = DataSourceAuthor(
        data_source_id=source.id,
        author_id=author.id,
        author_order=1,
    )
    db.add(link)
    await db.flush()
    return {"child_id": link.id, "ondelete": "cascade"}


async def _setup_extraction_figure(
    db: AsyncSession, source: DataSource
) -> dict[str, Any]:
    """extraction_figures.source_id → SET NULL."""
    fig = ExtractionFigure(
        source_id=source.id,
        page_number=1,
        figure_type="chart",
        confidence=0.9,
    )
    db.add(fig)
    await db.flush()
    return {"child_id": fig.id, "ondelete": "set_null"}


async def _setup_kg_node(
    db: AsyncSession, source: DataSource
) -> dict[str, Any]:
    """kg_nodes.source_id → SET NULL."""
    node = KGNode(
        node_type="Material",
        label="RegressionNode",
        confidence=0.95,
        source_id=source.id,
        status="active",
    )
    db.add(node)
    await db.flush()
    return {"child_id": node.id, "ondelete": "set_null"}


async def _setup_kg_edge(
    db: AsyncSession, source: DataSource
) -> dict[str, Any]:
    """kg_edges.source_id → SET NULL.

    Requires two KGNode rows (edge FK to kg_nodes for source/target).
    """
    n1 = KGNode(
        node_type="Material", label="EdgeSrcA", confidence=0.9, status="active"
    )
    n2 = KGNode(
        node_type="Material", label="EdgeTgtB", confidence=0.9, status="active"
    )
    db.add_all([n1, n2])
    await db.flush()

    edge = KGEdge(
        source_node_id=n1.id,
        target_node_id=n2.id,
        relation_type="hasProperty",
        confidence=0.9,
        source_id=source.id,
    )
    db.add(edge)
    await db.flush()
    return {"child_id": edge.id, "ondelete": "set_null"}


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------

_CHILD_TABLES = [
    pytest.param(
        "datasets",
        "source_id",
        "cascade",
        _setup_dataset,
        id="datasets-CASCADE",
    ),
    pytest.param(
        "data_source_authors",
        "data_source_id",
        "cascade",
        _setup_author_link,
        id="data_source_authors-CASCADE",
    ),
    pytest.param(
        "extraction_figures",
        "source_id",
        "set_null",
        _setup_extraction_figure,
        id="extraction_figures-SET_NULL",
    ),
    pytest.param(
        "kg_nodes",
        "source_id",
        "set_null",
        _setup_kg_node,
        id="kg_nodes-SET_NULL",
    ),
    pytest.param(
        "kg_edges",
        "source_id",
        "set_null",
        _setup_kg_edge,
        id="kg_edges-SET_NULL",
    ),
]


@pytest.mark.parametrize(
    "child_table, fk_column, ondelete, setup_fn",
    _CHILD_TABLES,
)
async def test_delete_source_with_child_rows(
    child_table: str,
    fk_column: str,
    ondelete: str,
    setup_fn: Any,
    async_client: Any,
    db_session: AsyncSession,
) -> None:
    """DELETE /api/v1/literature/{id} succeeds when child FK rows exist.

    Regression for NFM-1488 / NFM-2212.  The original ORM delete
    emitted ``UPDATE datasets SET source_id=NULL`` which raised
    ``NotNullViolationError`` because ``datasets.source_id`` is NOT NULL.
    The hot-fix (fe4a509) switched to raw SQL so that the database
    handles CASCADE / SET NULL automatically.

    Each parameterised case:
      1. Creates a DataSource + one child row in the given table.
      2. Calls DELETE on the literature endpoint.
      3. Asserts the child row was CASCADE-deleted or had its FK SET NULL.
    """
    source_id = uuid.uuid4()

    # 1. Seed source + child row via ORM (handles all column defaults)
    source = await _create_source(db_session, source_id)
    info = await setup_fn(db_session, source)
    child_id = info["child_id"]
    await db_session.commit()

    # 2. DELETE via the API endpoint
    resp = await async_client.delete(f"/api/v1/literature/{source_id}")
    assert resp.status_code == 200, f"DELETE failed: {resp.text}"

    # 3. Verify child-row disposition via raw SQL (bypasses ORM cache)
    db_session.expire_all()
    if ondelete == "cascade":
        row = (await db_session.execute(
            text(f"SELECT id FROM {child_table} WHERE id = :cid"),
            {"cid": str(child_id)},
        )).scalar_one_or_none()
        assert row is None, (
            f"{child_table} row ({child_id}) should have been CASCADE-deleted"
        )
    else:
        fk_val = (await db_session.execute(
            text(f"SELECT {fk_column} FROM {child_table} WHERE id = :cid"),
            {"cid": str(child_id)},
        )).scalar_one_or_none()
        assert fk_val is None, (
            f"{child_table}.{fk_column} should have been SET NULL"
        )
