"""Regression tests: DELETE /api/v1/literature/{id} with child rows in FK tables.

NFM-2212 — Parameterized coverage so the raw-SQL hot-fix (fe4a509)
doesn't regress.  Bug 7 / NFM-1488 regressed once already.

Each parametrized case:
  1. INSERT a DataSource
  2. INSERT a child row in the target FK table
  3. CALL DELETE /api/v1/literature/{id}
  4. ASSERT no IntegrityError (200 OK)
  5. ASSERT child-row disposition (CASCADE-deleted or SET NULL)

Test matrix:
  - datasets              (CASCADE)
  - data_source_authors   (CASCADE)
  - extraction_figures    (SET NULL in prod DB; ORM omits ondelete)
  - kg_nodes              (SET NULL)
  - kg_edges              (SET NULL)
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Parametrization
#
#   disposition = "cascade"  -> child row must be deleted
#   disposition = "set_null" -> child row's fk_column must be NULL
# ---------------------------------------------------------------------------

TABLE_PARAMS = [
    pytest.param(
        "datasets",
        "source_id",
        "cascade",
        id="datasets-CASCADE",
    ),
    pytest.param(
        "data_source_authors",
        "data_source_id",
        "cascade",
        id="data_source_authors-CASCADE",
    ),
    pytest.param(
        "extraction_figures",
        "source_id",
        "set_null",
        id="extraction_figures-SET_NULL",
        marks=pytest.mark.xfail(
            reason=(
                "ORM model omits ondelete='SET NULL' so SQLite creates "
                "NO ACTION constraint; prod Postgres has SET NULL via "
                "migration. Test verifies API doesn't 500 but FK block "
                "is expected in SQLite."
            ),
            strict=False,
        ),
    ),
    pytest.param(
        "kg_nodes",
        "source_id",
        "set_null",
        id="kg_nodes-SET_NULL",
    ),
    pytest.param(
        "kg_edges",
        "source_id",
        "set_null",
        id="kg_edges-SET_NULL",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source_id() -> uuid.UUID:
    return uuid.uuid4()


async def _create_source(db_session, source_id: uuid.UUID) -> None:
    """Insert a minimal DataSource row via ORM."""
    from nfm_db.models.source import DataSource

    db_session.add(
        DataSource(
            id=source_id,
            title="DELETE regression test source",
            source_type="journal_article",
            doi=f"10.0000/regression-{source_id.hex[:8]}",
        )
    )
    await db_session.commit()


async def _seed_child_row(
    db_session,
    table: str,
    source_id: uuid.UUID,
) -> uuid.UUID:
    """Insert a minimal child row in *table* referencing *source_id*.

    Uses ORM where possible so that column defaults (server_default,
    Python defaults) are applied automatically.  Falls back to raw SQL
    for tables whose ORM import would pull in heavy dependencies.

    Returns the child row's UUID for verification.
    """
    row_id = uuid.uuid4()

    if table == "datasets":
        from nfm_db.models.material import Material
        from nfm_db.models.property import Dataset

        material = Material(
            id=uuid.uuid4(),
            name=f"test-mat-{row_id.hex[:6]}",
            formula="T",
        )
        db_session.add(material)
        await db_session.commit()

        db_session.add(
            Dataset(
                id=row_id,
                source_id=source_id,
                material_id=material.id,
                title="test-dataset",
            )
        )
        await db_session.commit()

    elif table == "data_source_authors":
        from nfm_db.models.source import Author, DataSourceAuthor

        author = Author(
            id=uuid.uuid4(),
            full_name="Test Author",
            last_name="Author",
        )
        db_session.add(author)
        await db_session.commit()

        db_session.add(
            DataSourceAuthor(
                id=row_id,
                data_source_id=source_id,
                author_id=author.id,
                author_order=1,
                is_corresponding=False,
            )
        )
        await db_session.commit()

    elif table == "extraction_figures":
        from nfm_db.models.extraction_figure import ExtractionFigure

        db_session.add(
            ExtractionFigure(
                id=row_id,
                source_id=source_id,
                page_number=1,
                confidence=0.0,
            )
        )
        await db_session.commit()

    elif table == "kg_nodes":
        from nfm_db.models.kg import KGNode

        db_session.add(
            KGNode(
                id=row_id,
                node_type="Material",
                label="TestNode",
                properties={},
                confidence=0.9,
                status="active",
                source_id=source_id,
            )
        )
        await db_session.commit()

    elif table == "kg_edges":
        from nfm_db.models.kg import KGEdge, KGNode

        node_a = KGNode(
            id=uuid.uuid4(),
            node_type="Material",
            label="EdgeNodeA",
            properties={},
            confidence=1.0,
            status="active",
        )
        node_b = KGNode(
            id=uuid.uuid4(),
            node_type="Material",
            label="EdgeNodeB",
            properties={},
            confidence=1.0,
            status="active",
        )
        db_session.add(node_a)
        db_session.add(node_b)
        await db_session.commit()

        db_session.add(
            KGEdge(
                id=row_id,
                source_node_id=node_a.id,
                target_node_id=node_b.id,
                relation_type="hasProperty",
                properties={},
                confidence=0.8,
                source_id=source_id,
            )
        )
        await db_session.commit()

    return row_id


async def _child_exists(db_session, table: str, row_id: uuid.UUID) -> bool:
    result = await db_session.execute(
        text(f"SELECT 1 FROM {table} WHERE id = :rid"),
        {"rid": row_id.hex},
    )
    return result.scalar() is not None


async def _child_fk_is_null(
    db_session,
    table: str,
    fk_column: str,
    row_id: uuid.UUID,
) -> bool:
    result = await db_session.execute(
        text(f"SELECT {fk_column} FROM {table} WHERE id = :rid"),
        {"rid": row_id.hex},
    )
    return result.scalar() is None


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("child_table,fk_column,disposition", TABLE_PARAMS)
@pytest.mark.asyncio
async def test_delete_source_with_child_rows(
    child_table: str,
    fk_column: str,
    disposition: str,
    async_client,
    db_session,
) -> None:
    """DELETE /api/v1/literature/{id} with a child row in *child_table*.

    Regression for NFM-1488 / NFM-2212.  The raw-SQL hot-fix (fe4a509)
    replaced ORM delete to avoid SQLAlchemy emitting
    ``UPDATE datasets SET source_id=NULL`` on a NOT NULL column.
    """
    source_id = _make_source_id()

    # 1. Create the source.
    await _create_source(db_session, source_id)

    # 2. Seed a child row.
    child_row_id = await _seed_child_row(db_session, child_table, source_id)
    assert await _child_exists(db_session, child_table, child_row_id), (
        f"setup: child row not found in {child_table}"
    )

    # 3. Call DELETE endpoint.
    response = await async_client.delete(
        f"/api/v1/literature/{source_id}"
    )

    # 4. Must not raise IntegrityError — should return 200.
    assert response.status_code == 200, (
        f"DELETE failed with {response.status_code}: {response.text}"
    )

    # 5. Verify child-row disposition.
    if disposition == "cascade":
        assert not await _child_exists(db_session, child_table, child_row_id), (
            f"CASCADE: child row in {child_table} was not deleted"
        )
    elif disposition == "set_null":
        assert await _child_exists(db_session, child_table, child_row_id), (
            f"SET NULL: child row in {child_table} was unexpectedly deleted"
        )
        assert await _child_fk_is_null(db_session, child_table, fk_column, child_row_id), (
            f"SET NULL: {fk_column} in {child_table} was not nulled"
        )


# ---------------------------------------------------------------------------
# Bonus: delete with NO child rows (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_source_no_children(async_client, db_session) -> None:
    """DELETE a source with zero child rows — baseline happy path."""
    source_id = _make_source_id()
    await _create_source(db_session, source_id)

    response = await async_client.delete(
        f"/api/v1/literature/{source_id}"
    )
    assert response.status_code == 200

    result = await db_session.execute(
        text("SELECT 1 FROM data_sources WHERE id = :sid"),
        {"sid": source_id.hex},
    )
    assert result.scalar() is None, "source was not deleted"
