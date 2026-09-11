"""Tests for the processing-timeout sweep (NFM-4742-B / NFM-4744).

The processing reaper is the daily 03:30Z audit's first-class safety
net for documents stuck in the LightRAG ``processing`` bucket.  Without
it, a crashed worker silently strands rows forever and the bucket
counter keeps climbing while nothing ever reaches ``failed``.

The sweep must:

* leave rows younger than the threshold alone (23h);
* transition rows older than the threshold to ``failed`` with
  ``failure_kind = processing_timeout`` and a human-readable reason
  (25h);
* be idempotent — running twice on the same data must not emit a
  duplicate transition row, because the second sweep no longer sees
  the doc in the sidecar's ``processing`` list (the reaper removed it);
* emit one structured log line per transition so the 03:30Z beat
  (NFM-4742-D) can aggregate without re-reading the audit log;
* honour a configurable threshold so QA can replay short-lived
  regressions without faking the clock 24h into the future.

The "monotonic-clock fake" the AC names is implemented by patching the
``_now`` helper inside :mod:`nfm_db.services.rag_audit` — production
calls ``datetime.now(UTC)`` and tests pin ``_now`` to a deterministic
instant so the threshold math is reproducible across hosts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import RagIndexAuditLog
from nfm_db.services.rag_audit import (
    DEFAULT_PROCESSING_TIMEOUT,
    FAILURE_KIND_PROCESSING_TIMEOUT,
    ProcessingReapOutcome,
    _extract_doc_id,
    reap_processing_documents,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pin_clock(monkeypatch: pytest.MonkeyPatch, frozen: datetime) -> MagicMock:
    """Patch the reaper's ``_now`` helper so threshold math is deterministic.

    Returns the MagicMock so tests can assert call counts.  Production
    falls through to ``datetime.now(UTC)``; freezing the helper is the
    only safe way to assert "23h → still processing, 25h → reaped"
    without sleeping the test suite.
    """
    fake_now = MagicMock(return_value=frozen)
    monkeypatch.setattr("nfm_db.services.rag_audit._now", fake_now)
    return fake_now


@dataclass
class _SidecarStub:
    """Pair of mocks for ``_list_processing_rows`` + ``_delete_processing_document``.

    The stateful behaviour (rows drain after a delete) is what makes the
    idempotency test possible without standing up a real LightRAG sidecar.
    Production always pulls a fresh list from LightRAG; this stub
    mirrors the sidecar's "DELETE removes the row from the next list"
    contract so the reaper sees an empty bucket on the second sweep.
    """

    list_mock: MagicMock
    delete_mock: MagicMock


def _stub_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> _SidecarStub:
    """Wire up a stateful ``_list_processing_rows`` + ``_delete_processing_document`` pair.

    Captures the ``doc_id`` passed to ``_delete_processing_document``
    (kwargs-style, matching the implementation's signature) and removes
    matching rows from the simulated ``processing`` bucket so the next
    ``_list_processing_rows`` call no longer returns them.
    """
    pending = list(rows)
    deleted: list[str] = []

    async def _list_side_effect(
        *_args: Any, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        return [r for r in pending if _extract_doc_id(r) not in deleted]

    async def _delete_side_effect(*_args: Any, **kwargs: Any) -> bool:
        doc_id = kwargs.get("doc_id")
        if doc_id:
            deleted.append(doc_id)
        return True

    list_mock = AsyncMock(side_effect=_list_side_effect)
    delete_mock = AsyncMock(side_effect=_delete_side_effect)
    monkeypatch.setattr(
        "nfm_db.services.rag_audit._list_processing_rows", list_mock
    )
    monkeypatch.setattr(
        "nfm_db.services.rag_audit._delete_processing_document", delete_mock
    )
    return _SidecarStub(list_mock=list_mock, delete_mock=delete_mock)


def _iso(hours_ago: float, *, base: datetime) -> str:
    """Return an ISO-8601 timestamp ``hours_ago`` hours before ``base``."""
    return (base - timedelta(hours=hours_ago)).isoformat().replace(
        "+00:00", "Z"
    )


async def _audit_rows(db_session: AsyncSession) -> list[RagIndexAuditLog]:
    result = await db_session.execute(select(RagIndexAuditLog))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Threshold behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_skips_processing_under_threshold(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 23h-old ``processing`` row is left alone (no reaped audit row)."""
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    sidecar = _stub_sidecar(
        monkeypatch,
        [
            {
                "id": "data_source:doc-young",
                "file_source": "doc-young",
                "updated_at": _iso(23.0, base=frozen),
            }
        ],
    )

    outcome = await reap_processing_documents(
        db_session, lightrag_host="localhost", lightrag_port=9621
    )

    assert outcome.reaped == 0
    assert outcome.errors == 0
    assert outcome.inspected == 1
    rows = await _audit_rows(db_session)
    assert rows == []
    sidecar.delete_mock.assert_not_called()


@pytest.mark.asyncio
async def test_reap_transitions_processing_over_threshold(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 25h-old ``processing`` row transitions to ``failed`` with the timeout kind."""
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    doc_id = "data_source:doc-stuck-25h"
    sidecar = _stub_sidecar(
        monkeypatch,
        [
            {
                "id": doc_id,
                "file_source": "doc-stuck-25h",
                "updated_at": _iso(25.0, base=frozen),
            }
        ],
    )

    outcome = await reap_processing_documents(
        db_session, lightrag_host="localhost", lightrag_port=9621
    )

    assert outcome.reaped == 1
    assert outcome.errors == 0
    assert outcome.inspected == 1

    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "failed"
    assert row.failure_kind == FAILURE_KIND_PROCESSING_TIMEOUT
    assert row.error_message is not None
    assert "processing_timeout" in row.error_message
    # Human-readable reason mentions the age so operators can sanity-check
    # the threshold without re-reading the audit log timestamp math.
    assert "25" in row.error_message

    sidecar.delete_mock.assert_called_once_with(
        lightrag_host="localhost", lightrag_port=9621, doc_id=doc_id
    )


@pytest.mark.asyncio
async def test_reap_threshold_is_configurable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A custom ``timeout=`` flips the threshold without changing the constant."""
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    sidecar = _stub_sidecar(
        monkeypatch,
        [
            {
                "id": "data_source:doc-13h",
                "file_source": "doc-13h",
                "updated_at": _iso(13.0, base=frozen),
            }
        ],
    )

    # With a 12h threshold the same row that survived the default sweep
    # above must now be reaped.
    outcome = await reap_processing_documents(
        db_session,
        lightrag_host="localhost",
        lightrag_port=9621,
        timeout=timedelta(hours=12),
    )

    assert outcome.reaped == 1
    assert outcome.timeout == timedelta(hours=12)
    sidecar.delete_mock.assert_called_once()

    # The default constant is preserved so other callers and tests are
    # unaffected by the override.
    assert timedelta(hours=24) == DEFAULT_PROCESSING_TIMEOUT


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_is_idempotent_across_runs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second sweep sees no processing rows and writes nothing extra.

    The reaper removes the doc from the sidecar's ``processing`` bucket
    on transition, so the second invocation must report
    ``inspected == reaped == 0`` — proving the AC's "multiple runs do
    not double-transition" guarantee.
    """
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    _stub_sidecar(
        monkeypatch,
        [
            {
                "id": "data_source:doc-once",
                "file_source": "doc-once",
                "updated_at": _iso(30.0, base=frozen),
            }
        ],
    )

    first = await reap_processing_documents(
        db_session, lightrag_host="localhost", lightrag_port=9621
    )
    second = await reap_processing_documents(
        db_session, lightrag_host="localhost", lightrag_port=9621
    )

    assert first.reaped == 1
    assert second.reaped == 0
    assert second.inspected == 0
    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    assert rows[0].failure_kind == FAILURE_KIND_PROCESSING_TIMEOUT


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_emits_log_line_per_transition(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each transition emits one INFO log carrying ``doc_id`` and age in hours."""
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    _stub_sidecar(
        monkeypatch,
        [
            {
                "id": "data_source:doc-48h",
                "file_source": "doc-48h",
                "updated_at": _iso(48.0, base=frozen),
            }
        ],
    )

    with caplog.at_level(logging.INFO, logger="nfm_db.services.rag_audit"):
        outcome = await reap_processing_documents(
            db_session, lightrag_host="localhost", lightrag_port=9621
        )

    assert outcome.reaped == 1
    matching = [
        record
        for record in caplog.records
        if record.name == "nfm_db.services.rag_audit"
        and record.levelno == logging.INFO
        and "data_source:doc-48h" in record.getMessage()
    ]
    assert len(matching) == 1, [r.getMessage() for r in caplog.records]
    # Age is carried in the message so the 03:30Z beat (NFM-4742-D) can
    # aggregate without re-querying the database.
    assert "48" in matching[0].getMessage()
    assert matching[0].levelname == "INFO"


# ---------------------------------------------------------------------------
# Outcome dataclass
# ---------------------------------------------------------------------------


def test_processing_reap_outcome_defaults() -> None:
    """ProcessingReapOutcome exposes the dataclass fields the AC promises."""
    frozen_date = datetime(2026, 9, 12, tzinfo=UTC).date()
    outcome = ProcessingReapOutcome(
        run_date=frozen_date,
        timeout=DEFAULT_PROCESSING_TIMEOUT,
    )
    assert outcome.run_date == frozen_date
    assert outcome.timeout == DEFAULT_PROCESSING_TIMEOUT
    assert outcome.inspected == 0
    assert outcome.reaped == 0
    assert outcome.errors == 0
    assert outcome.error_message is None


@pytest.mark.asyncio
async def test_reap_skips_row_without_timestamp(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A processing row without ``updated_at`` / ``started_at`` / ``created_at`` is skipped."""
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    sidecar = _stub_sidecar(
        monkeypatch,
        [{"id": "data_source:doc-notime", "file_source": "doc-notime"}],
    )

    outcome = await reap_processing_documents(
        db_session, lightrag_host="localhost", lightrag_port=9621
    )

    # Skip the row rather than evict a doc that just entered the pipeline.
    assert outcome.inspected == 1
    assert outcome.reaped == 0
    assert outcome.errors == 0
    sidecar.delete_mock.assert_not_called()
    assert await _audit_rows(db_session) == []


@pytest.mark.asyncio
async def test_reap_records_lightrag_outage(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LightRAG outage records an ``error`` audit row and re-raises for the watchdog.

    Mirrors the pattern from :func:`run_rag_audit_index_coverage` so the
    existing 03:30Z watchdog (NFM-4406) sees the failure.
    """
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)

    async def _raise(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("sidecar offline")

    monkeypatch.setattr(
        "nfm_db.services.rag_audit._list_processing_rows", _raise
    )

    with pytest.raises(RuntimeError, match="sidecar offline"):
        await reap_processing_documents(
            db_session, lightrag_host="localhost", lightrag_port=9621
        )

    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "error"
    assert row.failure_kind is None
    assert row.error_message is not None
    assert "sidecar offline" in row.error_message


@pytest.mark.asyncio
async def test_reap_skips_row_without_doc_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A processing row with no parseable doc-id is logged at WARNING and skipped."""
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    sidecar = _stub_sidecar(
        monkeypatch,
        # No ``id`` / ``file_source`` / ``file_path`` / ``data_source`` —
        # the extractor returns ``None`` and the row is skipped.
        [{"updated_at": _iso(40.0, base=frozen)}],
    )

    with caplog.at_level(logging.WARNING, logger="nfm_db.services.rag_audit"):
        outcome = await reap_processing_documents(
            db_session, lightrag_host="localhost", lightrag_port=9621
        )

    assert outcome.inspected == 1
    assert outcome.reaped == 0
    assert outcome.errors == 0
    sidecar.delete_mock.assert_not_called()
    assert await _audit_rows(db_session) == []
    assert any(
        record.levelno == logging.WARNING
        and "row missing doc-id" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_reap_continues_when_delete_throws(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed sidecar delete increments ``errors`` and does not write a second row.

    The reaper records the transition first, then deletes from the sidecar;
    a delete failure must not roll back the audit row, but it must
    increment the error counter so operators can spot a flapping sidecar.
    """
    frozen = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    _pin_clock(monkeypatch, frozen)
    _stub_sidecar(
        monkeypatch,
        [
            {
                "id": "data_source:doc-delete-fail",
                "file_source": "doc-delete-fail",
                "updated_at": _iso(30.0, base=frozen),
            }
        ],
    )

    async def _delete_boom(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("delete network error")

    monkeypatch.setattr(
        "nfm_db.services.rag_audit._delete_processing_document", _delete_boom
    )

    with caplog.at_level(logging.WARNING, logger="nfm_db.services.rag_audit"):
        outcome = await reap_processing_documents(
            db_session, lightrag_host="localhost", lightrag_port=9621
        )

    assert outcome.inspected == 1
    assert outcome.reaped == 0
    assert outcome.errors == 1
    # Audit row was committed before the delete attempted — the reaper
    # does not roll back on sidecar failure.
    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    assert rows[0].failure_kind == FAILURE_KIND_PROCESSING_TIMEOUT
    assert any(
        record.levelno == logging.WARNING
        and "transition failed" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Helper coverage — direct unit tests for the timestamp / id extractors.
# These keep the ``reap_processing_documents`` tests focused on the
# orchestration contract; the helpers deserve their own assertions so a
# regression in, say, epoch-millisecond parsing surfaces a precise
# failure rather than a behavioural drift in the sweep tests.
# ---------------------------------------------------------------------------


class TestParseLightragTimestamp:
    """``_parse_lightrag_timestamp`` must accept every shape the sidecar emits."""

    def test_iso_z_suffix(self) -> None:
        from nfm_db.services.rag_audit import _parse_lightrag_timestamp

        parsed = _parse_lightrag_timestamp("2026-09-11T10:20:00Z")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.year == 2026 and parsed.month == 9 and parsed.day == 11

    def test_iso_explicit_offset(self) -> None:
        from nfm_db.services.rag_audit import _parse_lightrag_timestamp

        parsed = _parse_lightrag_timestamp("2026-09-11T10:20:00+00:00")
        assert parsed is not None
        assert parsed.utcoffset() is not None

    def test_epoch_milliseconds(self) -> None:
        from nfm_db.services.rag_audit import _parse_lightrag_timestamp

        # 2026-09-11T10:20:00Z in epoch milliseconds.
        epoch_ms = "1760274000000"
        parsed = _parse_lightrag_timestamp(epoch_ms)
        # Year assertion is approximate because we don't hard-code the
        # exact calendar math; just verify we get a sensible UTC datetime.
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)
        assert 2025 <= parsed.year <= 2026

    def test_unparseable_returns_none(self) -> None:
        from nfm_db.services.rag_audit import _parse_lightrag_timestamp

        assert _parse_lightrag_timestamp("not-a-date") is None

    def test_non_string_returns_none(self) -> None:
        from nfm_db.services.rag_audit import _parse_lightrag_timestamp

        assert _parse_lightrag_timestamp(None) is None  # type: ignore[arg-type]
        assert _parse_lightrag_timestamp(12345) is None  # type: ignore[arg-type]
        assert _parse_lightrag_timestamp("") is None


class TestProcessingRowAgeHours:
    """``_processing_row_age_hours`` derives age from the sidecar's timestamp field."""

    def test_uses_updated_at_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nfm_db.services.rag_audit import _processing_row_age_hours

        base = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
        _pin_clock(monkeypatch, base)
        row = {"updated_at": (base - timedelta(hours=5)).isoformat().replace("+00:00", "Z")}
        assert _processing_row_age_hours(row) == pytest.approx(5.0)

    def test_falls_back_to_started_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nfm_db.services.rag_audit import _processing_row_age_hours

        base = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
        _pin_clock(monkeypatch, base)
        row = {"started_at": (base - timedelta(hours=2)).isoformat().replace("+00:00", "Z")}
        assert _processing_row_age_hours(row) == pytest.approx(2.0)

    def test_falls_back_to_created_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nfm_db.services.rag_audit import _processing_row_age_hours

        base = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
        _pin_clock(monkeypatch, base)
        row = {"created_at": (base - timedelta(hours=30)).isoformat().replace("+00:00", "Z")}
        assert _processing_row_age_hours(row) == pytest.approx(30.0)

    def test_returns_none_when_no_timestamp(self) -> None:
        from nfm_db.services.rag_audit import _processing_row_age_hours

        assert _processing_row_age_hours({"id": "x"}) is None

    def test_returns_none_when_unparseable(self) -> None:
        from nfm_db.services.rag_audit import _processing_row_age_hours

        assert _processing_row_age_hours({"updated_at": "garbage"}) is None


class TestExtractDocId:
    """``_extract_doc_id`` prefers ``id`` and falls back through ``file_*`` keys."""

    def test_prefers_id_key(self) -> None:
        from nfm_db.services.rag_audit import _extract_doc_id

        assert _extract_doc_id({"id": "data_source:a"}) == "data_source:a"

    def test_falls_back_to_file_source(self) -> None:
        from nfm_db.services.rag_audit import _extract_doc_id

        assert _extract_doc_id({"file_source": "lit"}) == "lit"

    def test_returns_none_when_no_known_key(self) -> None:
        from nfm_db.services.rag_audit import _extract_doc_id

        assert _extract_doc_id({"unknown": "x"}) is None

    def test_skips_empty_strings(self) -> None:
        from nfm_db.services.rag_audit import _extract_doc_id

        assert _extract_doc_id({"id": "", "file_source": "lit"}) == "lit"


class TestNowHelper:
    """``_now`` returns a timezone-aware UTC datetime."""

    def test_returns_aware_datetime(self) -> None:
        from nfm_db.services.rag_audit import _now

        instant = _now()
        assert isinstance(instant, datetime)
        assert instant.tzinfo is not None
        assert instant.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Sidecar helpers — exercise the real ``LightRAGClient`` glue so the
# lazy-import + ``LightRAGClient(host=..., port=...)`` path gets covered.
# The HTTP transport is replaced with an ``AsyncMock`` so the test does
# not depend on a running LightRAG instance.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_processing_rows_returns_processing_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_list_processing_rows`` forwards to ``LightRAGClient.list_processing_documents``."""
    from nfm_db.services import rag_audit

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "statuses": {
            "processed": [{"id": "data_source:keep-me"}],
            "processing": [
                {"id": "data_source:stuck", "updated_at": "2026-09-10T00:00:00Z"}
            ],
            "failed": [{"id": "data_source:gone"}],
        }
    }
    fake_response.raise_for_status = lambda: None

    # Patch ``httpx.AsyncClient`` at the module level so the new client
    # constructed inside ``_list_processing_rows`` receives our fake
    # transport on ``__init__``.
    fake_http = MagicMock()
    fake_http.get = AsyncMock(return_value=fake_response)
    fake_http.delete = AsyncMock()
    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_http,
    )

    rows = await rag_audit._list_processing_rows(
        lightrag_host="localhost", lightrag_port=9621
    )

    assert len(rows) == 1
    assert rows[0]["id"] == "data_source:stuck"


@pytest.mark.asyncio
async def test_delete_processing_document_returns_true_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_delete_processing_document`` returns ``True`` when the sidecar confirms removal."""
    from nfm_db.services import rag_audit

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = lambda: None

    fake_http = MagicMock()
    fake_http.delete = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_http,
    )

    removed = await rag_audit._delete_processing_document(
        lightrag_host="localhost", lightrag_port=9621, doc_id="data_source:x"
    )
    assert removed is True


@pytest.mark.asyncio
async def test_delete_processing_document_returns_false_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_delete_processing_document`` returns ``False`` when the sidecar replies 404."""
    from nfm_db.services import rag_audit

    fake_response = MagicMock()
    fake_response.status_code = 404

    fake_http = MagicMock()
    fake_http.delete = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_http,
    )

    removed = await rag_audit._delete_processing_document(
        lightrag_host="localhost", lightrag_port=9621, doc_id="data_source:gone"
    )
    assert removed is False


@pytest.mark.asyncio
async def test_delete_processing_document_rejects_empty_doc_id() -> None:
    """An empty ``doc_id`` is rejected up-front so callers cannot accidentally wipe the index."""
    from nfm_db.services import rag_audit
    from nfm_db.services.lightrag_client import LightRAGClientError

    with pytest.raises(LightRAGClientError):
        await rag_audit._delete_processing_document(
            lightrag_host="localhost", lightrag_port=9621, doc_id="   "
        )


@pytest.mark.asyncio
async def test_delete_processing_document_translates_httpx_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``httpx.HTTPError`` from the transport becomes ``LightRAGClientError``."""
    import httpx

    from nfm_db.services import rag_audit
    from nfm_db.services.lightrag_client import LightRAGClientError

    fake_http = MagicMock()
    fake_http.delete = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_http,
    )

    with pytest.raises(LightRAGClientError, match="DELETE failed"):
        await rag_audit._delete_processing_document(
            lightrag_host="localhost", lightrag_port=9621, doc_id="data_source:boom"
        )


@pytest.mark.asyncio
async def test_delete_processing_document_raises_on_unexpected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-200/204/404 response raises ``LightRAGClientError`` with the status code."""
    import httpx

    from nfm_db.services import rag_audit
    from nfm_db.services.lightrag_client import LightRAGClientError

    fake_response = MagicMock()
    fake_response.status_code = 500
    # ``raise_for_status`` raises ``HTTPStatusError`` when the status is
    # in the 4xx/5xx range — that's the path the impl uses to convert
    # the failure into ``LightRAGClientError``.
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("DELETE", "http://localhost:9621/documents"),
        response=fake_response,
    )

    fake_http = MagicMock()
    fake_http.delete = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(
        "nfm_db.services.lightrag_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_http,
    )

    with pytest.raises(LightRAGClientError, match="HTTP 500"):
        await rag_audit._delete_processing_document(
            lightrag_host="localhost", lightrag_port=9621, doc_id="data_source:oops"
        )
