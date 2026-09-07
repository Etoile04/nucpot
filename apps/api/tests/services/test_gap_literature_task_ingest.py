"""Tests for ``gap_literature_task._ingest_and_extract`` (NFM-4313).

The gap-fill ingestion path creates ``DataSource`` rows from DOIs
resolved out of Crossref search, but — like the from-doi endpoint
before NFM-4313 — it wrote only a ``DOI: …`` stub title and left
``journal`` / ``year`` NULL.  These tests pin the Crossref-resolved
bibliography landing on the row.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nfm_db.models.source import DataSource
from nfm_db.tasks.gap_literature_task import _ingest_and_extract

DOI = "10.1016/j.jnucmat.2018.05.039"

MOCK_MARKDOWN = "# Irradiation Creep of Ferritic Alloys\n\nAbstract text without a citation line.\n"

MOCK_CROSSREF = {
    "title": "Irradiation Creep of Ferritic Alloys",
    "journal": "Journal of Nuclear Materials",
    "year": 2018,
}


class _FakeResult:
    """Minimal ``Result`` double: both lookups miss (fresh ingest)."""

    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeSession:
    """AsyncSession double: records ``add``/``commit`` calls only."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, stmt: object, *args: object, **kwargs: object) -> _FakeResult:
        # First execute: DataCollectionRequest lookup → miss (request rows
        # are only touched when present; a miss is a supported path).
        # Second execute: DataSource idempotency lookup → miss.
        entity = getattr(stmt, "column_descriptions", [{}])[0].get("entity")
        name = getattr(entity, "__name__", "")
        return _FakeResult(None if name != "Unexpected" else object())

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:  # pragma: no cover — not exercised
        return None


@pytest.fixture
def literature_storage(tmp_path: Path):
    os.environ["LITERATURE_STORAGE_ROOT"] = str(tmp_path / "uploads" / "literature")
    yield
    os.environ.pop("LITERATURE_STORAGE_ROOT", None)


def _task_session_patch(session: _FakeSession) -> Any:
    """Patch the gap task's task-scoped factory seam (NFM-4076 T5).

    Production path is ``task_session_factory() -> factory -> session``;
    this hands the task module a factory whose sessions are always
    ``session``.
    """

    @asynccontextmanager
    async def _factory_cm() -> AsyncIterator[Any]:
        def _factory() -> _FakeSession:
            return session

        yield _factory

    return patch(
        "nfm_db.tasks.gap_literature_task.task_session_factory",
        _factory_cm,
    )


def _ingest_context(session: _FakeSession) -> ExitStack:
    cm = ExitStack()
    cm.enter_context(
        patch(
            "nfm_db.services.doi_fetcher.fetch_paper_content",
            return_value=MOCK_MARKDOWN,
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            return_value=MOCK_CROSSREF,
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="gap-task-id"),
        ),
    )
    cm.enter_context(_task_session_patch(session))
    return cm


@pytest.mark.asyncio
async def test_ingest_persists_crossref_journal_year(literature_storage) -> None:
    """NFM-4313: gap-fill ingestion must not land DOI-stub-only rows."""
    session = _FakeSession()
    with _ingest_context(session):
        result = await _ingest_and_extract(
            str(uuid.uuid4()), DOI, "material", "thermal_conductivity"
        )

    assert result["status"] == "completed"
    assert len(session.added) == 1
    source = session.added[0]
    assert isinstance(source, DataSource)
    assert source.doi == DOI
    assert source.title == "Irradiation Creep of Ferritic Alloys"
    assert source.journal == "Journal of Nuclear Materials"
    assert source.year == 2018


@pytest.mark.asyncio
async def test_ingest_survives_crossref_unavailable(literature_storage) -> None:
    """Crossref metadata outage must never fail the ingest itself."""
    session = _FakeSession()
    cm = ExitStack()
    cm.enter_context(
        patch(
            "nfm_db.services.doi_fetcher.fetch_paper_content",
            return_value=MOCK_MARKDOWN,
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            return_value=None,
        ),
    )
    cm.enter_context(
        patch(
            "nfm_db.services.literature_dispatcher._send_literature_task",
            return_value=MagicMock(id="gap-task-id"),
        ),
    )
    cm.enter_context(_task_session_patch(session))
    with cm:
        result = await _ingest_and_extract(
            str(uuid.uuid4()), DOI, "material", "thermal_conductivity"
        )

    assert result["status"] == "completed"
    source = session.added[0]
    # Fallback title is the markdown H1; journal/year stay NULL until the
    # stock backfill script heals them.
    assert source.title == "Irradiation Creep of Ferritic Alloys"
    assert source.journal is None
    assert source.year is None
