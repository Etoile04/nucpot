"""Unit tests for ``backfill_doi_metadata.py`` (NFM-4313).

The script heals stock ``data_sources`` rows whose DOI was ingested
before Crossref resolution existed (the production literature list
showed "—" in the journal/year columns).  These tests pin:

* candidate selection semantics (only rows with a DOI and a NULL field)
* fill-NULL-only writes (never overwrites curated values)
* idempotency (a second run over healed rows performs no fetch / write)
* dry-run emits no UPDATE
* the human-readable report counts
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

# Make the script and nfm_db importable for the test process (mirrors
# test_doi_etl_admit.py).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent.parent
_API_SRC = _REPO_ROOT / "apps" / "api" / "src"
for p in (str(_SCRIPTS_DIR), str(_API_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from backfill_doi_metadata import (  # type: ignore[import-not-found]  # noqa: E402
    BackfillReport,
    run_backfill,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    """AsyncSession double: serves one canned SELECT, records UPDATEs."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.updates: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, params=None):
        compiled = str(stmt)
        if compiled.lstrip().upper().startswith("SELECT"):
            return _FakeResult(list(self._rows))
        self.updates.append((compiled, dict(params or {})))
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _row(*, journal=None, year=None, doi="10.1/x"):
    return (uuid.uuid4(), doi, journal, year)


def _meta(journal="Journal of Nuclear Materials", year=2018):
    return {"title": "T", "journal": journal, "year": year}


# ---------------------------------------------------------------------------
# Candidate selection + fills
# ---------------------------------------------------------------------------


class TestRunBackfill:
    def test_fills_null_journal_and_year(self) -> None:
        row = _row()
        session = _FakeSession([row])
        report = asyncio.run(
            run_backfill(session, fetch=lambda doi: _meta(), sleep_s=0)
        )
        assert report.candidates == 1
        assert report.rows_updated == 1
        assert report.journal_filled == 1
        assert report.year_filled == 1
        assert len(session.updates) == 1
        sql, params = session.updates[0]
        assert params["journal"] == "Journal of Nuclear Materials"
        assert params["year"] == 2018
        assert str(row[0]) in sql or params.get("row_id") == str(row[0])

    def test_only_null_fields_are_written(self) -> None:
        # journal already curated → only year may be filled.
        row = _row(journal="Curated Journal", year=None)
        session = _FakeSession([row])
        report = asyncio.run(
            run_backfill(session, fetch=lambda doi: _meta(), sleep_s=0)
        )
        assert report.journal_filled == 0
        assert report.year_filled == 1
        assert report.rows_updated == 1
        sql, params = session.updates[0]
        assert "journal" not in params
        assert params["year"] == 2018
        # Pin the per-field IS NULL guard in the emitted SQL (NFM-4332):
        # the script is meant to be re-run against new stock, so a refactor
        # that silently drops the guard must fail here, not in production
        # where curated values would be overwritten.
        assert "year IS NULL" in sql
        assert "journal IS NULL" not in sql
        assert "SET journal" not in sql

    def test_crossref_has_no_new_fields_no_update(self) -> None:
        # Both fields already set → row is not even a candidate, but if
        # a candidate's Crossref record carries nothing new, no UPDATE.
        row = _row(journal="Curated", year=None)
        session = _FakeSession([row])
        report = asyncio.run(
            run_backfill(
                session, fetch=lambda doi: {"title": "T", "journal": None, "year": None}, sleep_s=0
            )
        )
        assert report.rows_updated == 0
        assert report.rows_no_new_fields == 1
        assert session.updates == []

    def test_crossref_miss_is_reported_not_raised(self) -> None:
        session = _FakeSession([_row()])
        report = asyncio.run(run_backfill(session, fetch=lambda doi: None, sleep_s=0))
        assert report.crossref_misses == 1
        assert report.rows_updated == 0

    def test_fetch_receives_the_doi(self) -> None:
        seen: list[str] = []
        session = _FakeSession([_row(doi="10.1016/j.jnucmat.2018.05.039")])
        asyncio.run(
            run_backfill(
                session, fetch=lambda doi: seen.append(doi) or _meta(), sleep_s=0
            )
        )
        assert seen == ["10.1016/j.jnucmat.2018.05.039"]

    def test_limit_caps_candidate_rows(self) -> None:
        rows = [_row(), _row(), _row()]
        calls: list[str] = []
        session = _FakeSession(rows)
        asyncio.run(
            run_backfill(
                session, fetch=lambda doi: calls.append(doi) or _meta(), sleep_s=0, limit=2
            )
        )
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# Idempotency + dry-run
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_over_healed_rows_is_a_noop(self) -> None:
        # After run 1, the row would be re-selected only if it still had
        # a NULL field. Simulate the post-run state: both fields set →
        # the SELECT returns nothing → zero fetches, zero updates.
        healed_session = _FakeSession([])  # selection now returns no rows
        report = asyncio.run(
            run_backfill(healed_session, fetch=lambda doi: _meta(), sleep_s=0)
        )
        assert report.candidates == 0
        assert report.rows_updated == 0
        assert healed_session.updates == []


class TestDryRun:
    def test_dry_run_emits_no_updates(self) -> None:
        session = _FakeSession([_row()])
        report = asyncio.run(
            run_backfill(session, fetch=lambda doi: _meta(), dry_run=True, sleep_s=0)
        )
        assert session.updates == []
        # The report still shows what WOULD happen.
        assert report.dry_run is True
        assert report.candidates == 1
        assert report.journal_filled == 1
        assert report.year_filled == 1
        assert report.rows_updated == 0

    def test_dry_run_reports_would_update_rows(self) -> None:
        session = _FakeSession([_row(), _row(journal="Kept")])
        report = asyncio.run(
            run_backfill(session, fetch=lambda doi: _meta(), dry_run=True, sleep_s=0)
        )
        assert report.would_update_rows == 2


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


class TestReport:
    def test_render_includes_counts(self) -> None:
        report = BackfillReport(
            candidates=3,
            crossref_hits=2,
            crossref_misses=1,
            journal_filled=2,
            year_filled=2,
            rows_updated=2,
            rows_no_new_fields=0,
            would_update_rows=0,
            dry_run=False,
        )
        text = report.render()
        assert "candidates=3" in text
        assert "crossref_hits=2" in text
        assert "crossref_misses=1" in text
        assert "journal_filled=2" in text
        assert "year_filled=2" in text
        assert "rows_updated=2" in text
        assert "DRY-RUN" not in text

    def test_render_flags_dry_run(self) -> None:
        report = BackfillReport(
            candidates=1, dry_run=True, would_update_rows=1
        )
        assert "DRY-RUN" in report.render()
