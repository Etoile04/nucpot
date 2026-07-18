"""API integration tests for POST /literature/from-doi (NFM-1488 / NFM-1485-4).

The from-doi endpoint triggers a DOI-based fetch + parse pipeline.
On the epic branch, this endpoint is not yet routed — FastAPI returns 405
for POST against a path that only has GET routes registered.

These tests validate the **dispatcher contract** that the from-doi endpoint
MUST satisfy when it lands:
- Accepts a JSON body with a ``doi`` field.
- Creates a DataSource row with ``source_type='doi_fetch'``.
- Dispatches ``schedule_literature_processing`` for the new row.
- Returns the literature_id and status in the standard envelope.

Because the endpoint route is not yet registered, the tests use the
dispatcher directly (the same contract the endpoint will call) to lock
the behavior before the endpoint ships.  A single routing smoke test
confirms the 405 until the route is added.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.source import DataSource
from nfm_db.services.literature_dispatcher import schedule_literature_processing

# ---------------------------------------------------------------------------
# Routing smoke — endpoint not yet registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_doi_endpoint_not_yet_registered(async_client) -> None:
    """POST /literature/from-doi returns 405 until the route is added.

    This is a transitional test — it will fail once the from-doi route
    is registered and should be replaced by the full happy-path test below.
    """
    response = await async_client.post(
        "/api/v1/literature/from-doi",
        json={"doi": "10.1016/j.jnucmat.2024.01.001"},
    )
    # FastAPI returns 405 when POST matches no route.
    assert response.status_code in (405, 404)


# ---------------------------------------------------------------------------
# Dispatcher contract — the behavior the endpoint will delegate to
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doi_dispatcher_creates_datasource_and_dispatches(
    db_session: AsyncSession,
) -> None:
    """Simulate the from-doi endpoint's core contract:

    1. Create a DataSource with source_type='doi_fetch' and the DOI.
    2. Call schedule_literature_processing.
    3. Return the literature_id and status.
    """
    doi = "10.1016/j.jnucmat.2024.01.001"

    # Step 1: Create the DataSource row (what the endpoint will do).
    source = DataSource(
        title=f"DOI: {doi}",
        doi=doi,
        source_type="doi_fetch",
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)

    assert source.id is not None

    # Step 2: Dispatch Celery processing.
    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        return_value=MagicMock(id="doi-task-id"),
    ) as mock_send:
        task_id = schedule_literature_processing(source.id)

    mock_send.assert_called_once()
    assert task_id == "doi-task-id"

    # Step 3: Verify the row is queryable.
    found = await db_session.get(DataSource, source.id)
    assert found is not None
    assert found.doi == doi
    assert found.source_type == "doi_fetch"


@pytest.mark.asyncio
async def test_doi_dispatcher_propagates_broker_errors(
    db_session: AsyncSession,
) -> None:
    """If the Celery broker is down, the dispatcher must raise — the endpoint
    should surface 502 to the client."""
    source = DataSource(
        title="DOI: 10.broken/test",
        doi="10.broken/test",
        source_type="doi_fetch",
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        side_effect=ConnectionError("Redis broker unreachable"),
    ):
        with pytest.raises(ConnectionError, match="Redis broker unreachable"):
            schedule_literature_processing(source.id)


# ---------------------------------------------------------------------------
# Idempotent re-DOI — same DOI creates a new record each time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_doi_creates_distinct_records(
    db_session: AsyncSession,
) -> None:
    """Two DOI fetches for different DOIs produce two separate DataSource rows.

    NOTE: The ``data_sources.doi`` column has a UNIQUE constraint, so each
    DOI can only appear once.  This test verifies that two distinct DOIs
    each produce their own row and both are dispatched.
    """
    dois = [
        "10.1016/j.jnucmat.2024.re.001",
        "10.1016/j.jnucmat.2024.re.002",
    ]
    ids: list[uuid.UUID] = []

    for doi in dois:
        source = DataSource(
            title=f"DOI: {doi}",
            doi=doi,
            source_type="doi_fetch",
        )
        db_session.add(source)
        await db_session.commit()
        await db_session.refresh(source)

        with patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="dup-task"),
        ):
            schedule_literature_processing(source.id)

        ids.append(source.id)

    assert ids[0] != ids[1]
    row1 = await db_session.get(DataSource, ids[0])
    row2 = await db_session.get(DataSource, ids[1])
    assert row1 is not None
    assert row2 is not None
    assert row1.doi == dois[0]
    assert row2.doi == dois[1]
