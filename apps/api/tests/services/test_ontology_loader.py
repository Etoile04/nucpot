"""Tests for ontology_loader (NFM-3590 / NFM-2868-P1-2-b).

Validates that ``_resolve_version`` and the public ``load_ontology_types``
entry point refuse to proceed when the referenced ``OntologyVersion`` row is
unknown or NULL, and that the loader logs at ERROR before raising.

Acceptance criteria covered:
- [ ] Loader reads ``ontology_version_id`` on every loaded type row
- [ ] Loader raises (and logs at ERROR) when a row references an unknown
      ontology_version_id
- [ ] Loader raises when ``ontology_version_id`` is NULL on a non-baseline
      load (baseline / v0 is the only allowed NULL-pointer path during
      bootstrap)
- [ ] Unit tests: happy path (v0 types load), unknown-version reject,
      NULL reject
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import KEntityType, KRelationType, OntologyVersion
from nfm_db.services.ontology_loader import (
    OntologyVersionNotFoundError,
    _resolve_version,
    load_ontology_types,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Reuse the conftest-seeded author user so the OntologyVersion.created_by FK
# resolves against the in-memory SQLite test database.
_AUTHOR_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


async def _seed_ontology_version(
    session: AsyncSession,
    *,
    version: str = "0.0.0",
    status: str = "published",
    ontology_data: dict | None = None,
    version_id: uuid.UUID | None = None,
) -> OntologyVersion:
    """Insert a minimal OntologyVersion row."""
    ov = OntologyVersion(
        id=version_id or uuid.uuid4(),
        version=version,
        status=status,
        created_by=_AUTHOR_ID,
        ontology_data=ontology_data or {"entity_types": [], "relation_types": []},
    )
    session.add(ov)
    await session.flush()
    await session.refresh(ov)
    return ov


async def _seed_type_rows(
    session: AsyncSession,
    *,
    ontology_version_id: uuid.UUID,
    entity_names: tuple[str, ...] = ("Material",),
    relation_names: tuple[str, ...] = ("hasProperty",),
) -> None:
    """Insert minimal KEntityType / KRelationType rows pointing at the given version."""
    for name in entity_names:
        session.add(
            KEntityType(
                name=name,
                ontology_version_id=ontology_version_id,
            ),
        )
    for name in relation_names:
        session.add(
            KRelationType(
                name=name,
                ontology_version_id=ontology_version_id,
            ),
        )
    await session.flush()


# ---------------------------------------------------------------------------
# _resolve_version
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_resolve_version_returns_row_when_present(
    db_session: AsyncIterator[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Happy path: a known ontology_version_id returns its OntologyVersion row."""
    ov = await _seed_ontology_version(db_session, version="v0")

    with caplog.at_level(logging.ERROR, logger="nfm_db.services.ontology_loader"):
        resolved = await _resolve_version(db_session, ov.id)

    assert resolved.id == ov.id
    assert resolved.version == "v0"
    assert caplog.records == []  # No ERROR logs on the happy path.


@pytest.mark.unit
async def test_resolve_version_raises_on_unknown_uuid(
    db_session: AsyncIterator[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown ontology_version_id raises and logs at ERROR."""
    missing_id = uuid.uuid4()

    with (
        caplog.at_level(logging.ERROR, logger="nfm_db.services.ontology_loader"),
        pytest.raises(OntologyVersionNotFoundError) as exc_info,
    ):
        await _resolve_version(db_session, missing_id)

    assert str(missing_id) in str(exc_info.value)
    assert any(
        record.levelno == logging.ERROR and "ontology_version_id" in record.getMessage().lower()
        for record in caplog.records
    ), (
        f"expected an ERROR log mentioning ontology_version_id; "
        f"got {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.unit
async def test_resolve_version_raises_on_null_id(
    db_session: AsyncIterator[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NULL ontology_version_id raises and logs at ERROR."""
    with (
        caplog.at_level(logging.ERROR, logger="nfm_db.services.ontology_loader"),
        pytest.raises(OntologyVersionNotFoundError) as exc_info,
    ):
        await _resolve_version(db_session, None)

    assert "null" in str(exc_info.value).lower()
    assert any(
        record.levelno == logging.ERROR and "null" in record.getMessage().lower()
        for record in caplog.records
    ), f"expected an ERROR log mentioning null; got {[r.getMessage() for r in caplog.records]}"


# ---------------------------------------------------------------------------
# load_ontology_types (public entry point)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_load_ontology_types_returns_types_for_resolved_version(
    db_session: AsyncIterator[AsyncSession],
) -> None:
    """Happy path: seeded entity + relation rows come back under their version."""
    ov = await _seed_ontology_version(db_session, version="v0")
    await _seed_type_rows(
        db_session,
        ontology_version_id=ov.id,
        entity_names=("Material", "Property"),
        relation_names=("hasProperty",),
    )

    entities, relations = await load_ontology_types(db_session, ov.id)

    assert {e.name for e in entities} == {"Material", "Property"}
    assert {r.name for r in relations} == {"hasProperty"}
    # Each loaded row carries the FK we asked it to resolve.
    assert {e.ontology_version_id for e in entities} == {ov.id}
    assert {r.ontology_version_id for r in relations} == {ov.id}


@pytest.mark.unit
async def test_load_ontology_types_raises_on_unknown_version(
    db_session: AsyncIterator[AsyncSession],
) -> None:
    """Public entry point refuses to proceed when the version row is missing."""
    missing_id = uuid.uuid4()

    with pytest.raises(OntologyVersionNotFoundError):
        await load_ontology_types(db_session, missing_id)


@pytest.mark.unit
async def test_load_ontology_types_raises_on_null_version(
    db_session: AsyncIterator[AsyncSession],
) -> None:
    """Public entry point refuses NULL ontology_version_id on non-baseline loads."""
    with pytest.raises(OntologyVersionNotFoundError):
        await load_ontology_types(db_session, None)
