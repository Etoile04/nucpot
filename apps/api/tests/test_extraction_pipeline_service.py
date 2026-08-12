"""Additional unit tests for extraction pipeline service (NFM-583).

Covers areas not tested in test_extraction_pipeline.py:
- _is_stub_mode (env var detection)
- _load_source_content (file loading, missing file)
- _post_process_extracted (phase normalization, category assignment, defaults)
- _stub_extraction_results (structure validation)
- ExtractionJob dataclass (defaults, field types)
- _update_job (immutable-style updates)
- ontofuel_extract LLM fallback (when not stub mode and LLM not configured)
- trigger_extraction gap scan failure (non-fatal)

See test_extraction_pipeline.py for the main test suite.
"""

from __future__ import annotations

import os
import uuid as _uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _apply_property_mapping,
    _find_matching,
    _get_latest_published_ontology,
    _is_stub_mode,
    _job_store,
    _load_source_content,
    _post_process_extracted,
    _stub_extraction_results,
    _update_job,
    ontofuel_extract,
    trigger_extraction,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_job_store():
    _job_store.clear()
    yield
    _job_store.clear()


# ---------------------------------------------------------------------------
# _is_stub_mode tests
# ---------------------------------------------------------------------------


class TestIsStubMode:
    """Tests for stub mode environment detection."""

    def test_true_when_env_is_true(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}):
            assert _is_stub_mode() is True

    def test_true_when_env_is_one(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "1"}):
            assert _is_stub_mode() is True

    def test_true_when_env_is_true_uppercase(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "TRUE"}):
            assert _is_stub_mode() is True

    def test_false_when_env_is_false(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}):
            assert _is_stub_mode() is False

    def test_false_when_env_is_zero(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "0"}):
            assert _is_stub_mode() is False

    def test_false_when_env_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _is_stub_mode() is False

    def test_false_when_env_is_empty_string(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_STUB_MODE": ""}):
            assert _is_stub_mode() is False


# ---------------------------------------------------------------------------
# _load_source_content tests
# ---------------------------------------------------------------------------


class TestLoadSourceContent:
    """Tests for source file loading."""

    def test_loads_existing_file(self, tmp_path: Path) -> None:
        content_file = tmp_path / "source.md"
        content_file.write_text("# Nuclear Fuel Properties\nUO2 density", encoding="utf-8")

        result = _load_source_content(str(content_file))
        assert "# Nuclear Fuel Properties" in result
        assert "UO2 density" in result

    def test_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            _load_source_content("/nonexistent/file.md")

    def test_loads_utf8(self, tmp_path: Path) -> None:
        content_file = tmp_path / "unicode.md"
        content_file.write_text("密度 densité плотность", encoding="utf-8")

        result = _load_source_content(str(content_file))
        assert "密度" in result


# ---------------------------------------------------------------------------
# _stub_extraction_results tests
# ---------------------------------------------------------------------------


class TestStubExtractionResults:
    """Tests for stub extraction result generation."""

    def test_returns_list(self) -> None:
        results = _stub_extraction_results("test_source")
        assert isinstance(results, list)

    def test_returns_three_properties(self) -> None:
        results = _stub_extraction_results("test_source")
        assert len(results) == 3

    def test_source_passed_through(self) -> None:
        results = _stub_extraction_results("custom_source")
        assert all(r["source"] == "custom_source" for r in results)

    def test_high_confidence_first(self) -> None:
        results = _stub_extraction_results("test")
        assert results[0]["confidence"] == "high"

    def test_medium_confidence_second(self) -> None:
        results = _stub_extraction_results("test")
        assert results[1]["confidence"] == "medium"

    def test_low_confidence_third(self) -> None:
        results = _stub_extraction_results("test")
        assert results[2]["confidence"] == "low"

    def test_all_have_values(self) -> None:
        results = _stub_extraction_results("test")
        for r in results:
            assert "value" in r
            assert r["value"] is not None

    def test_all_have_units(self) -> None:
        results = _stub_extraction_results("test")
        for r in results:
            assert "unit" in r
            assert r["unit"] is not None

    def test_cache_levels_present(self) -> None:
        results = _stub_extraction_results("test")
        for r in results:
            assert "cache_level" in r


# ---------------------------------------------------------------------------
# ExtractionJob dataclass tests
# ---------------------------------------------------------------------------


class TestExtractionJob:
    """Tests for the ExtractionJob dataclass."""

    def test_default_status_is_queued(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        assert job.status == JobStatus.QUEUED

    @pytest.mark.xfail(
        reason=(
            "NFM-1366: ExtractionJob has no duplicate_count field; once the "
            "duplicates-tracking shape lands the field defaults to 0 like "
            "extracted_count/staged_count/rejected_count"
        ),
        strict=True,
    )
    def test_counts_default_to_zero(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        assert job.extracted_count == 0
        assert job.staged_count == 0
        assert job.rejected_count == 0
        assert job.duplicate_count == 0

    def test_error_message_default_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        assert job.error_message is None

    def test_timestamps_default_none(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        assert job.started_at is None
        assert job.completed_at is None

    def test_created_at_auto_set(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        assert job.created_at is not None

    def test_optional_fields_nullable(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        assert job.fill_batch_id is None
        assert job.element_systems is None
        assert job.cache_level is None
        assert job.max_confidence is None


# ---------------------------------------------------------------------------
# _extraction_job_to_dict serialization boundary (NFM-2743, D3)
# ---------------------------------------------------------------------------


class TestExtractionJobToDict:
    """NFM-2743 / D3 — single serialization boundary.

    The dataclass :class:`ExtractionJob` (in-memory orchestration/request
    state) and the ORM :class:`ExtractionJob` (in
    ``models/extraction_job.py`` — ingestion/results state) model
    different lifecycle stages and have a 10-field gap. The dict — not
    either class — is the stable public interface for callers. These
    tests lock the contract so the dispatch wrapper and any future
    V2 path can rely on the same shape.

    See ``docs/architecture/ADR-NFM-2739-extraction-job-dual-class.md``
    for the field diff and the deferred migration to a single ORM row.
    """

    def test_dataclass_and_orm_return_identical_key_set(self) -> None:
        """The returned dict key set MUST be identical regardless of input.

        Regression guard for the whole D3 seam — call-sites that switch
        on key presence will silently break if either path adds or
        drops a key.
        """
        from uuid import UUID

        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        dc_job = ExtractionJob(
            job_id="dc-1",
            source_reference="src.md",
            source_type="file",
        )
        orm_job = ORMExtractionJob(
            source_reference="src.md",
            source_type="file",
        )
        orm_job.id = UUID("00000000-0000-0000-0000-000000000001")

        dc_dict = _extraction_job_to_dict(dc_job)
        orm_dict = _extraction_job_to_dict(orm_job)

        assert set(dc_dict.keys()) == set(orm_dict.keys()), (
            f"Key-set mismatch: dataclass-only={set(dc_dict) - set(orm_dict)} "
            f"orm-only={set(orm_dict) - set(dc_dict)}"
        )

    def test_job_id_is_str_for_dataclass(self) -> None:
        """Dataclass ``job_id`` is already a ``str`` — must round-trip as-is."""
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        dc_job = ExtractionJob(
            job_id="plain-str-id", source_reference="s", source_type="file",
        )
        d = _extraction_job_to_dict(dc_job)

        assert d["job_id"] == "plain-str-id"
        assert isinstance(d["job_id"], str)

    def test_job_id_is_str_for_orm_uuid(self) -> None:
        """ORM ``id`` is a ``uuid.UUID`` — must be coerced to ``str``.

        This is the exact confusion that produced PR #726's CI failures
        (NFM-2743 motivation). The helper is the single resolution point.
        """
        from uuid import UUID

        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        orm_job = ORMExtractionJob(source_reference="s", source_type="file")
        orm_job.id = UUID("00000000-0000-0000-0000-000000000099")

        d = _extraction_job_to_dict(orm_job)

        assert d["job_id"] == "00000000-0000-0000-0000-000000000099"
        assert isinstance(d["job_id"], str)
        assert not isinstance(d["job_id"], UUID)  # explicit: NEVER raw UUID

    @pytest.mark.asyncio
    async def test_orm_gap_field_defaults(self, db_session: Any) -> None:
        """The 10 dataclass-only fields must emit documented defaults on ORM path.

        Per the binding contract in NFM-2743:

            fill_batch_id=None
            extracted_count=0
            staged_count=0
            rejected_count=0
            element_systems=None
            cache_level=None
            max_confidence=None
            conflict_strategy="prefer_vlm"
            figures=[]
            tables=[]

        Two layers of defaults are exercised here:

        1. **Python-side defaults** — ``ExtractionJob.__init__`` (NFM-2745,
           see ``models/extraction_job.py``) explicitly applies
           ``setdefault(...)`` for the 6 non-nullable columns so
           transient ORM instances carry the contract defaults *before*
           INSERT.  Without that override, ``getattr(orm_job, name)``
           would return ``None`` for unset mapped attributes (SQLAlchemy
           2.0's default ``__init__`` only fires ``Column.default`` at
           INSERT-flush time).
        2. **Server-side defaults** — ``flush()`` + ``refresh()`` round-
           trips the row through the SQLite schema so the test catches
           drift between the ORM ``server_default=...`` arguments and
           the migration's DDL ``DEFAULT`` clauses.  If a future migration
           forgets a ``server_default`` on a ``NOT NULL`` column, the
           INSERT raises and this test fails loudly instead of silently
           leaving the contract's defaults undefined at the DB level.

        This is the same observation that produced NFM-2746
        (Phase B / transient-ORM default semantics).
        """
        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        orm_job = ORMExtractionJob(source_reference="s", source_type="file")
        db_session.add(orm_job)
        await db_session.flush()
        # Reset the instance state so post-flush values are loaded from
        # the SQLite schema (rather than reading from the pre-flush
        # Python-side attributes).
        await db_session.refresh(orm_job)
        d = _extraction_job_to_dict(orm_job)

        assert d["fill_batch_id"] is None
        assert d["extracted_count"] == 0
        assert d["staged_count"] == 0
        assert d["rejected_count"] == 0
        assert d["element_systems"] is None
        assert d["cache_level"] is None
        assert d["max_confidence"] is None
        assert d["conflict_strategy"] == "prefer_vlm"
        assert d["figures"] == []
        assert d["tables"] == []

    def test_transient_orm_coalesces_none_to_contract_defaults(self) -> None:
        """NFM-2747 AC#4 — transient ORM instances must emit documented defaults.

        A transient (never-flushed) ORM instance holds ``None`` for every
        unset column because SQLAlchemy ``Column(default=…)`` only fires
        at INSERT/flush.  The old ``getattr(job, name, fallback)`` pattern
        silently passed ``None`` through because the attribute descriptor
        always returns ``None`` (never raises ``AttributeError``).

        ``_extraction_job_to_dict`` now uses explicit ``_coalesce(v, d)``
        so the ADR-NFM-2739 §2.1 type-stability guarantee holds for any
        ORM ``ExtractionJob`` instance, including transient ones.
        """
        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        # Build a transient ORM instance — NO session.add / flush.
        # The ORM __init__ applies setdefault for non-nullable columns, so
        # those already carry correct values.  This test additionally
        # verifies that _coalesce would handle the raw-None case even if
        # __init__ were bypassed.
        orm_job = ORMExtractionJob(source_reference="s", source_type="file")

        # Verify that nullable gap columns read as None on a transient
        # instance (SQLAlchemy default= only fires at INSERT).
        assert orm_job.element_systems is None
        assert orm_job.cache_level is None
        assert orm_job.max_confidence is None

        d = _extraction_job_to_dict(orm_job)

        # fill_batch_id is explicitly excluded from coalescing per the spec.
        assert d["fill_batch_id"] is None
        # Counts coalesce to 0.
        assert d["extracted_count"] == 0
        assert d["staged_count"] == 0
        assert d["rejected_count"] == 0
        # Nullable fields stay None (contract-documented default).
        assert d["element_systems"] is None
        assert d["cache_level"] is None
        assert d["max_confidence"] is None
        # conflict_strategy coalesces to "prefer_vlm" — the enum's true
        # zero member (dataclass line 220, ORM column line 189).
        assert d["conflict_strategy"] == "prefer_vlm"
        # figures / tables coalesce to [].
        assert d["figures"] == []
        assert d["tables"] == []

        # Existing key-set identity guard at :230 must still pass.
        dc_job = ExtractionJob(job_id="dc-1", source_reference="s", source_type="file")
        dc_dict = _extraction_job_to_dict(dc_job)
        assert set(dc_dict.keys()) == set(d.keys())

    def test_orm_fill_batch_id_is_str_not_uuid(self) -> None:
        """NFM-2745 AC-3 — ORM ``fill_batch_id`` column type must be ``String``.

        The dataclass field is ``str | None`` and ``api/v4/extraction.py``
        parses it via ``uuid.UUID(job.fill_batch_id)`` (a string). If the
        ORM column were typed as ``Uuid``/``UUID``, ``getattr(job,
        "fill_batch_id")`` would return a ``uuid.UUID`` and leak a
        non-JSON-serializable object into the canonical dict — the exact
        bug class that produced PR #726's CI failures on ``job.id``.

        This test inspects the *mapped* column type (not just instance
        attribute setting) so it fails if the column is wired with the
        wrong type.
        """
        from sqlalchemy import String as SAString
        from sqlalchemy.dialects import postgresql

        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob

        col = ORMExtractionJob.__table__.columns["fill_batch_id"]

        # Must be a String family column (String, String(64), VARCHAR, etc.).
        assert isinstance(col.type, SAString), (
            f"fill_batch_id must be String, got {type(col.type).__name__}: "
            f"{col.type!r}"
        )
        # Explicit: NEVER a Postgres UUID type — that would coerce values to
        # uuid.UUID and break JSON serialization.
        assert not isinstance(col.type, postgresql.UUID), (
            f"fill_batch_id must NOT be Postgres UUID, got {col.type!r}"
        )
        # The dataclass contract binds ``fill_batch_id`` to ``str | None``.
        # The mapper must allow string values to round-trip as str.
        assert "fill_batch_id" in ORMExtractionJob.__table__.columns

    @pytest.mark.asyncio
    async def test_orm_columns_round_trip_non_default_values(
        self, db_session: Any
    ) -> None:
        """NFM-2745 AC-5 — all 10 new ORM columns must be wired through the DB.

        A test that only sets instance attributes and reads them back
        (``test_orm_gap_field_defaults``) does NOT prove the columns are
        mapped — ``getattr(job, "fill_batch_id", None)`` returns ``None``
        via the fallback, and arbitrary instance attribute assignment is
        permitted by SQLAlchemy whether or not the column exists.

        This test:

        1. Builds an ORM job with all 10 new fields set to NON-default
           values.
        2. Persists it through ``db_session`` (the SQLite test fixture
           creates the schema from ``Base.metadata`` — if any column is
           missing from the mapper, the INSERT raises).
        3. Reads the row back through a fresh ``select(...)`` and passes
           it through ``_extraction_job_to_dict`` to confirm the helper
           sees the **real** values, not getattr fallback defaults.
        """
        from sqlalchemy import select

        from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        orm_job = ORMExtractionJob(
            source_reference="s",
            source_type="file",
            fill_batch_id="fb-round-trip-987",
            extracted_count=42,
            staged_count=17,
            rejected_count=3,
            element_systems=["Fe-Cr", "Ni-based"],
            cache_level="L2",
            max_confidence="0.87",
            conflict_strategy="prefer_db",
            figures=[{"id": "fig-1", "caption": "Phase diagram"}],
            tables=[{"id": "tbl-1", "caption": "Composition"}],
        )
        db_session.add(orm_job)
        await db_session.flush()
        # Reset the in-memory state so the post-flush attributes are
        # loaded from the SQLite schema (rather than the pre-flush values
        # sitting on the instance).
        await db_session.refresh(orm_job)

        row_id = orm_job.id

        # Build a fresh instance from the persisted row (forces a real
        # SQL read rather than relying on the still-mapped instance).
        persisted = (
            await db_session.execute(
                select(ORMExtractionJob).where(ORMExtractionJob.id == row_id)
            )
        ).scalar_one()

        d = _extraction_job_to_dict(persisted)

        # Real values must round-trip — NOT getattr fallback defaults.
        assert d["fill_batch_id"] == "fb-round-trip-987"
        assert d["extracted_count"] == 42
        assert d["staged_count"] == 17
        assert d["rejected_count"] == 3
        assert d["element_systems"] == ["Fe-Cr", "Ni-based"]
        assert d["cache_level"] == "L2"
        assert d["max_confidence"] == "0.87"
        assert d["conflict_strategy"] == "prefer_db"
        assert d["figures"] == [{"id": "fig-1", "caption": "Phase diagram"}]
        assert d["tables"] == [{"id": "tbl-1", "caption": "Composition"}]

        # Sanity: dataclass key-set identity guard (NFM-2745 AC-4).
        dc_job = ExtractionJob(
            job_id="dc-1",
            source_reference="s",
            source_type="file",
            fill_batch_id="fb-round-trip-987",
            extracted_count=42,
            staged_count=17,
            rejected_count=3,
            element_systems=["Fe-Cr", "Ni-based"],
            cache_level="L2",
            max_confidence="0.87",
            conflict_strategy="prefer_db",
            figures=[{"id": "fig-1", "caption": "Phase diagram"}],
            tables=[{"id": "tbl-1", "caption": "Composition"}],
        )
        dc_dict = _extraction_job_to_dict(dc_job)
        assert set(dc_dict.keys()) == set(d.keys())
        assert len(dc_dict) == 24
        assert len(d) == 24
        assert dc_dict["fill_batch_id"] == d["fill_batch_id"]
        assert dc_dict["extracted_count"] == d["extracted_count"]
        assert dc_dict["conflict_strategy"] == d["conflict_strategy"]
        assert dc_dict["figures"] == d["figures"]

    def test_status_is_str_value_for_dataclass_enum(self) -> None:
        """``status`` must be the ``str`` value, not the ``JobStatus`` enum member."""
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        dc_job = ExtractionJob(
            job_id="j1",
            source_reference="s",
            source_type="file",
            status=JobStatus.RUNNING,
        )
        d = _extraction_job_to_dict(dc_job)

        assert d["status"] == "running"
        assert isinstance(d["status"], str)

    def test_datetimes_are_iso8601_strings(self) -> None:
        """``created_at``, ``started_at``, ``completed_at`` MUST be ISO-8601 strings."""
        from datetime import UTC, datetime

        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        now = datetime.now(UTC)
        dc_job = ExtractionJob(
            job_id="j1",
            source_reference="s",
            source_type="file",
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        d = _extraction_job_to_dict(dc_job)

        for key in ("created_at", "started_at", "completed_at"):
            assert isinstance(d[key], str), f"{key}={d[key]!r} should be str"
            assert datetime.fromisoformat(d[key]) == now, (
                f"{key} did not round-trip via fromisoformat"
            )

    def test_none_datetimes_remain_none(self) -> None:
        """``None`` datetimes stay ``None`` (do NOT become ``'None'`` strings)."""
        from nfm_db.services.extraction_pipeline import _extraction_job_to_dict

        dc_job = ExtractionJob(job_id="j1", source_reference="s", source_type="file")
        # Reset the auto-set fields so the helper must handle None cleanly.
        dc_job.started_at = None
        dc_job.completed_at = None
        d = _extraction_job_to_dict(dc_job)

        assert d["started_at"] is None
        assert d["completed_at"] is None
        # created_at is auto-set by default_factory and MUST still emit a str.
        assert isinstance(d["created_at"], str)


# ---------------------------------------------------------------------------
# _update_job tests
# ---------------------------------------------------------------------------


class TestUpdateJob:
    """Tests for immutable-style job update."""

    def test_update_status(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        _update_job(job, status=JobStatus.RUNNING)
        assert job.status == JobStatus.RUNNING

    def test_update_multiple_fields(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        _update_job(
            job,
            status=JobStatus.COMPLETED,
            extracted_count=10,
            staged_count=8,
            rejected_count=2,
        )
        assert job.status == JobStatus.COMPLETED
        assert job.extracted_count == 10
        assert job.staged_count == 8
        assert job.rejected_count == 2

    def test_update_ignores_unknown_field(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        _update_job(job, nonexistent_field="value")  # Should not raise
        assert job.status == JobStatus.QUEUED

    def test_update_started_at(self) -> None:
        from datetime import UTC, datetime

        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        now = datetime.now(UTC)
        _update_job(job, started_at=now)
        assert job.started_at == now

    def test_update_error_message(self) -> None:
        job = ExtractionJob(job_id="j1", source_reference="s1", source_type="file")
        _update_job(job, error_message="Connection timeout")
        assert job.error_message == "Connection timeout"


# ---------------------------------------------------------------------------
# _post_process_extracted tests
# ---------------------------------------------------------------------------


class TestPostProcessExtracted:
    """Tests for post-processing of extracted properties."""

    def test_adds_source_file_when_missing(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        processed = _post_process_extracted(raw, "test_source.md")
        assert processed[0]["source_file"] == "test_source.md"

    def test_preserves_existing_source_file(self) -> None:
        raw = [{"property_name": "density", "source_file": "original.md", "value": 10.0}]
        processed = _post_process_extracted(raw, "test_source.md")
        assert processed[0]["source_file"] == "original.md"

    def test_adds_default_confidence(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        processed = _post_process_extracted(raw, "test")
        assert processed[0]["confidence"] == "medium"

    def test_preserves_existing_confidence(self) -> None:
        raw = [{"property_name": "density", "confidence": "high", "value": 10.0}]
        processed = _post_process_extracted(raw, "test")
        assert processed[0]["confidence"] == "high"

    def test_creates_new_dicts(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        original = raw[0]
        processed = _post_process_extracted(raw, "test")
        assert processed[0] is not original
        assert "source_file" not in original

    def test_handles_empty_list(self) -> None:
        processed = _post_process_extracted([], "test")
        assert processed == []

    def test_handles_multiple_properties(self) -> None:
        raw = [
            {"property_name": "density", "value": 10.0},
            {"property_name": "lattice", "value": 5.47},
        ]
        processed = _post_process_extracted(raw, "test")
        assert len(processed) == 2
        assert processed[0]["source_file"] == "test"
        assert processed[1]["source_file"] == "test"


# ---------------------------------------------------------------------------
# ontofuel_extract LLM fallback tests
# ---------------------------------------------------------------------------


class TestOntoFuelExtractLLMFallback:
    """Tests for LLM extraction fallback behavior."""

    @pytest.mark.asyncio
    async def test_falls_back_to_stub_when_llm_not_configured(self) -> None:
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=False),
        ):
            results = await ontofuel_extract("test_source", "file")
            # Should fall back to stub
            assert len(results) == 3
            assert results[0]["element_system"] == "UO2"

    @pytest.mark.asyncio
    async def test_uses_stub_when_env_set(self) -> None:
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
        ):
            results = await ontofuel_extract("test_source", "file")
            assert len(results) == 3

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty(self) -> None:
        """When real LLM extraction fails, returns empty list."""
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline._load_source_content", return_value="content"
            ),
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value="prompt",
            ),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                side_effect=RuntimeError("API error"),
            ),
        ):
            results = await ontofuel_extract("failing.md", "file")
            assert results == []

    @pytest.mark.asyncio
    async def test_llm_returns_list_uses_directly(self) -> None:
        """When LLM returns a list, it's used directly."""
        llm_results = [
            {"property_name": "density", "value": 10.0, "confidence": "high"},
        ]
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline._load_source_content", return_value="content"
            ),
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value="prompt",
            ),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=llm_results,
            ),
        ):
            results = await ontofuel_extract("source.md", "file")
            assert len(results) >= 1
            assert results[0]["property_name"] == "density"

    @pytest.mark.asyncio
    async def test_llm_returns_dict_with_properties_key(self) -> None:
        """When LLM returns {'properties': [...]}, it's unwrapped."""
        llm_result = {"properties": [{"property_name": "mass", "value": 238.0}]}
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline._load_source_content", return_value="content"
            ),
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value="prompt",
            ),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=llm_result,
            ),
        ):
            results = await ontofuel_extract("source.md", "file")
            assert len(results) >= 1
            assert results[0]["property_name"] == "mass"

    @pytest.mark.asyncio
    async def test_llm_returns_dict_with_data_key(self) -> None:
        """When LLM returns {'data': [...]}, it's unwrapped."""
        llm_result = {"data": [{"property_name": "energy", "value": 100.0}]}
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline._load_source_content", return_value="content"
            ),
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value="prompt",
            ),
            patch(
                "nfm_db.services.extraction_pipeline.call_llm",
                new_callable=AsyncMock,
                return_value=llm_result,
            ),
        ):
            results = await ontofuel_extract("source.md", "file")
            assert len(results) >= 1
            assert results[0]["property_name"] == "energy"

    @pytest.mark.asyncio
    async def test_file_not_found_returns_empty(self) -> None:
        """When source file doesn't exist, returns empty list."""
        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline._load_source_content",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            results = await ontofuel_extract("missing.md", "file")
            assert results == []


# ---------------------------------------------------------------------------
# _apply_property_mapping tests
# ---------------------------------------------------------------------------


class TestApplyPropertyMapping:
    """Tests for property name mapping with optional nfm-ref-gapfill."""

    def test_identity_mapping_when_no_gapfill(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert len(result) == 1
        assert result[0]["property_name"] == "density"

    def test_adds_property_alias(self) -> None:
        raw = [{"property_name": "lattice_constant", "value": 5.47}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert result[0]["property"] == "lattice_constant"

    def test_preserves_existing_property(self) -> None:
        raw = [{"property_name": "energy", "property": "energy", "value": 100}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert result[0]["property"] == "energy"

    def test_applies_cache_level_override(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _apply_property_mapping(raw, cache_level="L2")
        assert result[0]["cache_level"] == "L2"

    def test_no_cache_level_when_none(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _apply_property_mapping(raw, cache_level=None)
        assert "cache_level" not in result[0]

    def test_creates_new_dicts(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        original = raw[0]
        result = _apply_property_mapping(raw, cache_level=None)
        assert result[0] is not original

    def test_empty_list_returns_empty(self) -> None:
        result = _apply_property_mapping([], cache_level=None)
        assert result == []

    def test_with_gapfill_mapping(self) -> None:
        with patch.dict("sys.modules", {"nfm_ref_gapfill": MagicMock()}):
            mock_module = MagicMock()
            mock_module.property_mapping = MagicMock()
            mock_module.property_mapping.map_property = lambda name, src: f"MAPPED:{name}"
            import sys

            sys.modules["nfm_ref_gapfill.property_mapping"] = mock_module.property_mapping
            try:
                # The function imports map_property at call time
                raw = [{"property_name": "test", "source": "doi", "value": 1.0}]
                result = _apply_property_mapping(raw, cache_level=None)
                assert len(result) == 1
                assert result[0]["property_name"] == "MAPPED:test"
            finally:
                sys.modules.pop("nfm_ref_gapfill.property_mapping", None)


# ---------------------------------------------------------------------------
# _find_matching tests
# ---------------------------------------------------------------------------


class TestFindMatching:
    """Tests for finding matching raw input by dedup hash."""

    def test_returns_matching_dict(self) -> None:
        raw = [
            {
                "property_name": "density",
                "property": "density",
                "element_system": "UO2",
                "source": "doi:10.0/test",
                "value": 10.0,
            },
        ]
        # Compute the expected hash for this raw entry
        from nfm_db.services.quality_gate import compute_dedup_hash

        expected_hash = compute_dedup_hash(
            element_system="UO2",
            phase=None,
            property_name="density",
            method=None,
            source="doi:10.0/test",
        )
        result = _find_matching(raw, expected_hash)
        assert result is not None
        assert result["property_name"] == "density"

    def test_returns_none_when_no_match(self) -> None:
        raw = [{"property_name": "density", "value": 10.0}]
        result = _find_matching(raw, "nonexistent_hash")
        assert result is None

    def test_returns_none_for_empty_list(self) -> None:
        result = _find_matching([], "any_hash")
        assert result is None


# ---------------------------------------------------------------------------
# trigger_extraction tests (mocked orchestration)
# ---------------------------------------------------------------------------


class TestTriggerExtraction:
    """Tests for the full extraction pipeline orchestration."""

    @pytest.mark.asyncio
    async def test_successful_pipeline_with_empty_results(self) -> None:
        """Pipeline completes when extraction returns empty list."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.GapScanService"),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="file",
            )
            assert job.status == JobStatus.COMPLETED
            assert job.extracted_count == 0
            # Early return path: commit is NOT called when extraction is empty
            mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_failure_sets_failed_status(self) -> None:
        """Pipeline sets FAILED status when extraction raises."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                side_effect=RuntimeError("extraction failed"),
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.GapScanService"),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                mock_session,
                source_reference="fail_source",
                source_type="file",
            )
            assert job.status == JobStatus.FAILED
            assert "extraction failed" in job.error_message
            mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_with_results_and_no_accepted(self) -> None:
        """Pipeline completes when quality gate rejects all properties."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[],
                rejected=[MagicMock()],
                duplicates=[],
            )
        )

        extracted = [
            {"property_name": "density", "value": 10.0, "source": "test", "confidence": "high"}
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="reject_source",
                source_type="file",
            )
            # Pipeline logic: PARTIAL when rejected > 0
            assert job.status == JobStatus.PARTIAL
            assert job.staged_count == 0
            assert job.rejected_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_with_accepted_results(self) -> None:
        """Pipeline stages accepted results and runs gap scan."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        accepted_result = MagicMock(dedup_hash="test_hash")
        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[accepted_result],
                rejected=[],
                duplicates=[],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock()

        extracted = [
            {
                "property_name": "density",
                "value": 10.0,
                "source": "test",
                "confidence": "high",
                "property": "density",
                "element_system": "UO2",
            },
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="test_hash"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="good_source",
                source_type="file",
            )
            assert job.staged_count == 1
            assert job.rejected_count == 0
            mock_scanner.scan_gaps.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_gap_scan_failure_is_non_fatal(self) -> None:
        """Pipeline continues when gap scan throws."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[],
                duplicates=[],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = MagicMock()
        mock_scanner.scan_gaps = AsyncMock(side_effect=RuntimeError("scan failed"))

        extracted = [
            {
                "property_name": "energy",
                "value": 100.0,
                "source": "test",
                "confidence": "high",
                "property": "energy",
                "element_system": "UO2",
            },
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="gap_fail_source",
                source_type="file",
            )
            # Should still complete (gap scan failure is non-fatal)
            assert job.status == JobStatus.COMPLETED
            assert job.staged_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_partial_when_rejected_exist(self) -> None:
        """Pipeline sets PARTIAL status when some results are rejected."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[MagicMock()],
                duplicates=[],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock()

        extracted = [
            {
                "property_name": "density",
                "value": 10.0,
                "source": "test",
                "confidence": "high",
                "property": "density",
                "element_system": "UO2",
            },
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="partial_source",
                source_type="file",
            )
            assert job.status == JobStatus.PARTIAL
            assert job.staged_count == 1
            assert job.rejected_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_stores_job_in_store(self) -> None:
        """Pipeline stores job in the global job store."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        # Pin V2=False — this test exercises the legacy branch's
        # ``_job_store`` dict. The V2 orchestrator path persists to the
        # DB via ``session.add(orm_job)`` and never touches ``_job_store``
        # (NFM-2698), so it must continue exercising V1 until V2 ships
        # a ``_job_store``-equivalent or we accept the schema gap.
        v1_settings = MagicMock()
        v1_settings.extraction_v2_enabled = False

        with (
            patch("nfm_db.config.get_settings", return_value=v1_settings),
            patch(
                "nfm_db.services.extraction_pipeline.get_settings",
                return_value=v1_settings,
                create=True,
            ),
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", new_callable=MagicMock),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="store_test",
                source_type="file",
            )
            assert job.job_id in _job_store
            assert _job_store[job.job_id] is job

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason=(
            "NFM-1366: trigger_extraction() does not yet set "
            "ExtractionJob.duplicate_count from quality_gate.process_bulk().duplicates"
        ),
        strict=True,
    )
    async def test_duplicates_tracked_separately_from_rejected(self) -> None:
        """Duplicates inflate duplicate_count, NOT rejected_count (NFM-637)."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[MagicMock()],
                duplicates=[MagicMock(), MagicMock()],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock()

        extracted = [
            {
                "property_name": "density",
                "value": 10.0,
                "source": "test",
                "confidence": "high",
                "property": "density",
                "element_system": "UO2",
            },
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="dedup_test",
                source_type="file",
            )
            assert job.staged_count == 1
            assert job.rejected_count == 1
            assert job.duplicate_count == 2

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason=(
            "NFM-1366: ExtractionJob.duplicate_count is not yet set when the "
            "duplicates list is empty (should default to 0 alongside rejected_count)"
        ),
        strict=True,
    )
    async def test_no_duplicates_yields_zero_duplicate_count(self) -> None:
        """When quality gate returns no duplicates, duplicate_count is 0."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        accepted_result = MagicMock(dedup_hash="h1")
        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[accepted_result],
                rejected=[],
                duplicates=[],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock()

        extracted = [
            {
                "property_name": "density",
                "value": 10.0,
                "source": "test",
                "confidence": "high",
                "property": "density",
                "element_system": "UO2",
            },
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="no_dup_test",
                source_type="file",
            )
            assert job.staged_count == 1
            assert job.rejected_count == 0
            assert job.duplicate_count == 0

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason=(
            "NFM-1366: ExtractionJob has no total_count field; the staged + rejected "
            "+ duplicates sum assertion fails until the field is added"
        ),
        strict=True,
    )
    async def test_total_accounts_for_staged_rejected_and_duplicates(self) -> None:
        """Total = staged + rejected + duplicates (no records lost)."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_gate = AsyncMock()
        mock_gate.process_bulk = AsyncMock(
            return_value=MagicMock(
                accepted=[MagicMock(dedup_hash="h1")],
                rejected=[MagicMock()],
                duplicates=[MagicMock()],
            )
        )
        mock_gate.stage_record = AsyncMock()

        mock_scanner = AsyncMock()
        mock_scanner.scan_gaps = AsyncMock()

        extracted = [
            {
                "property_name": f"prop{i}",
                "value": i,
                "source": "test",
                "confidence": "high",
                "property": f"prop{i}",
                "element_system": "UO2",
            }
            for i in range(3)
        ]

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService", return_value=mock_gate),
            patch("nfm_db.services.extraction_pipeline.GapScanService", return_value=mock_scanner),
            patch("nfm_db.services.quality_gate.compute_dedup_hash", return_value="h1"),
        ):
            job = await trigger_extraction(
                mock_session,
                source_reference="total_test",
                source_type="file",
            )
            total = job.staged_count + job.rejected_count + job.duplicate_count
            assert total == job.extracted_count
            assert total == 3


# ---------------------------------------------------------------------------
# NFM-2640: Ontology prompt integration tests
# ---------------------------------------------------------------------------


class TestGetLatestPublishedOntology:
    """Tests for _get_latest_published_ontology helper (NFM-2640).

    Regression coverage for Hermes CRITICAL 2026-08-12:
    ``result.scalars().first()`` is async-only — must be awaited.  All
    mocks in this class therefore use ``AsyncMock`` for the ``.first``
    attribute so the real SQLAlchemy semantics are exercised.
    """

    @pytest.mark.asyncio
    async def test_returns_none_when_no_published_ontology(self) -> None:
        """Helper returns None when no OntologyVersion has status='published'."""
        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        # AsyncScalarResult.first() is async — must be AsyncMock with return_value.
        mock_scalars.first = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await _get_latest_published_ontology(mock_session)
        assert result is None
        # Crucially the await path was taken — .first() was called exactly once.
        mock_scalars.first.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_latest_published_ontology(self) -> None:
        """Helper returns the OntologyVersion with most recent created_at."""
        mock_session = AsyncMock()
        ov_id = _uuid.uuid4()
        mock_ov = MagicMock()
        mock_ov.id = ov_id
        mock_ov.version = "1.2.0"
        mock_ov.ontology_data = {
            "entity_types": [{"name": "NuclearFuel"}],
            "relation_types": [{"name": "contains"}],
        }
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=mock_ov)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await _get_latest_published_ontology(mock_session)
        assert result is mock_ov
        assert result.version == "1.2.0"
        mock_scalars.first.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_return_coroutine_object(self) -> None:
        """Regression (Hermes 2026-08-12): function must return the row, not a coroutine.

        ``AsyncScalarResult.first()`` is an ``async def`` method; calling it
        synchronously returns a coroutine object.  Before the fix, the helper
        returned that coroutine without awaiting, which silently regressed
        V2 ontology-driven extraction under the flag-true default.
        """
        import inspect

        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value="the-row")
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await _get_latest_published_ontology(mock_session)
        assert not inspect.iscoroutine(result), (
            "Helper must await result.scalars().first(); returning the coroutine "
            "regresses V2 ontology-driven extraction."
        )
        assert result == "the-row"

    @pytest.mark.asyncio
    async def test_narrowed_exception_surfaces_unrelated_bugs(self) -> None:
        """Regression (Hermes 2026-08-12): ``except Exception`` was too broad.

        After the fix, only ``SQLAlchemyError`` and ``AttributeError`` are
        caught (returning None so the static prompt path falls back).  All
        other exceptions — e.g. ``KeyError``, ``TypeError``, ``ValueError``
        from a programming bug — must propagate so they are not silently
        masked as "missing ontology, use static prompt".
        """
        from sqlalchemy.exc import OperationalError

        mock_session = AsyncMock()
        mock_scalars = MagicMock()

        async def _raise_keyerror() -> Any:
            raise KeyError("not_a_db_error")

        mock_scalars.first = AsyncMock(side_effect=_raise_keyerror)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(KeyError):
            await _get_latest_published_ontology(mock_session)

        # OperationalError (a SQLAlchemyError subclass) MUST still be swallowed
        # so the static-prompt fallback path works when the DB is briefly down.
        mock_scalars.first = AsyncMock(
            side_effect=OperationalError("SELECT 1", {}, Exception("db down"))
        )
        result = await _get_latest_published_ontology(mock_session)
        assert result is None


class TestOntologyPromptInPipeline:
    """Tests for ontology-aware prompt selection in ontofuel_extract (NFM-2640)."""

    @pytest.mark.asyncio
    async def test_uses_ontology_prompt_when_published_version_exists(
        self,
    ) -> None:
        """Pipeline uses build_ontology_extraction_prompt when ontology found."""
        mock_session = AsyncMock()
        ov_id = _uuid.uuid4()
        mock_ov = MagicMock()
        mock_ov.id = ov_id
        mock_ov.version = "2.0.0"
        mock_ov.ontology_data = {
            "entity_types": [{"name": "UO2Fuel"}],
            "relation_types": [],
        }
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=mock_ov)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        ontology_prompt = "ONTOLOGY-AWARE PROMPT NuclearFuel"
        static_prompt = "STATIC PROMPT"

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}, clear=False),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.build_ontology_extraction_prompt",
                return_value=ontology_prompt,
            ) as mock_ontology_prompt,
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value=static_prompt,
            ),
            patch("nfm_db.services.extraction_pipeline.call_llm", new_callable=AsyncMock) as mock_llm,
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="# Test"),
        ):
            mock_llm.return_value = []

            await ontofuel_extract(
                source_reference="/fake/file.md",
                source_type="file",
                db=mock_session,
            )

            mock_ontology_prompt.assert_called_once_with(mock_ov)
            mock_llm.assert_called_once()
            call_kwargs = mock_llm.call_args
            assert call_kwargs.kwargs["system_prompt"] == ontology_prompt

    @pytest.mark.asyncio
    async def test_falls_back_to_static_prompt_when_no_ontology_published(
        self,
    ) -> None:
        """Pipeline uses build_extraction_system_prompt when no ontology published."""
        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        static_prompt = "STATIC BASE PROMPT"

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}, clear=False),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value=static_prompt,
            ),
            patch("nfm_db.services.extraction_pipeline.call_llm", new_callable=AsyncMock) as mock_llm,
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="# Test"),
        ):
            mock_llm.return_value = []

            await ontofuel_extract(
                source_reference="/fake/file.md",
                source_type="file",
                db=mock_session,
            )

            mock_llm.assert_called_once()
            call_kwargs = mock_llm.call_args
            assert call_kwargs.kwargs["system_prompt"] == static_prompt

    @pytest.mark.asyncio
    async def test_pipeline_skips_ontology_query_when_db_is_none(self) -> None:
        """Pipeline uses static prompt when db session is None (file-only path)."""
        static_prompt = "STATIC BASE PROMPT"

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "false"}, clear=False),
            patch("nfm_db.services.extraction_pipeline.is_llm_configured", return_value=True),
            patch(
                "nfm_db.services.extraction_pipeline.build_extraction_system_prompt",
                return_value=static_prompt,
            ),
            patch("nfm_db.services.extraction_pipeline.call_llm", new_callable=AsyncMock) as mock_llm,
            patch("nfm_db.services.extraction_pipeline._load_source_content", return_value="# Test"),
        ):
            mock_llm.return_value = []

            await ontofuel_extract(
                source_reference="/fake/file.md",
                source_type="file",
                db=None,
            )

            mock_llm.assert_called_once()
            call_kwargs = mock_llm.call_args
            assert call_kwargs.kwargs["system_prompt"] == static_prompt


class TestOntologyJobProvenance:
    """Tests for ontology provenance on ExtractionJob (NFM-2640)."""

    @pytest.mark.asyncio
    async def test_trigger_sets_ontology_provenance_when_published(
        self,
    ) -> None:
        """trigger_extraction sets job.ontology fields when ontology is published."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        ov_id = _uuid.uuid4()

        mock_ov = MagicMock()
        mock_ov.id = ov_id
        mock_ov.version = "1.0.0"
        mock_ov.ontology_data = {"entity_types": [], "relation_types": []}
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=mock_ov)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.GapScanService"),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="file",
            )
            assert job.ontology_version_id == ov_id
            assert job.ontology_version_str == "1.0.0"

    @pytest.mark.asyncio
    async def test_trigger_ontology_provenance_none_when_not_published(
        self,
    ) -> None:
        """trigger_extraction leaves job.ontology fields None when no ontology."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        with (
            patch.dict(os.environ, {"EXTRACTION_STUB_MODE": "true"}),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("nfm_db.services.extraction_pipeline.QualityGateService") as mock_qg_cls,
            patch("nfm_db.services.extraction_pipeline.GapScanService"),
        ):
            mock_qg = mock_qg_cls.return_value
            mock_qg.process_bulk = AsyncMock(
                return_value=MagicMock(accepted=[], rejected=[], duplicates=[]),
            )
            job = await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="file",
            )
            assert job.ontology_version_id is None
            assert job.ontology_version_str is None


# ---------------------------------------------------------------------------
# V2-path ORMExtractionJob ontology provenance (NFM-2667)
# ---------------------------------------------------------------------------


class _FakeV2Orchestrator:
    """Captures the orm_job passed to ExtractionOrchestrator and short-circuits run().

    Used by NFM-2667 tests to inspect the ORM object that the V2 path of
    ``trigger_extraction`` constructs.  We never want to actually drive the
    orchestrator — only verify what it receives.
    """

    instances: list[Any] = []  # populated with each orm_job received

    def __init__(self, session: Any, orm_job: Any) -> None:
        self._session = session
        self._job = orm_job
        type(self).instances.append(orm_job)

    async def run(self, **_kwargs: Any) -> Any:
        return MagicMock()


class TestV2PathOntologyProvenanceOnORM:
    """Regression tests for NFM-2667.

    When ``extraction_v2_enabled`` is True, ``trigger_extraction`` builds an
    ``ORMExtractionJob`` and calls ``session.add`` + ``session.flush``.  The
    ontology provenance columns (``ontology_version_id`` and
    ``ontology_version_str``) MUST be wired from ``_get_latest_published_ontology``
    onto the ORM object — otherwise the provenance advertised by NFM-2637 is
    dead-letter: every persisted row has NULL ontology columns.

    These tests verify the V2 path's wiring (the bug fix).  The legacy
    dataclass path is covered by ``TestOntologyJobProvenance`` above.
    """

    @pytest.fixture(autouse=True)
    def _reset_captured_jobs(self) -> None:
        _FakeV2Orchestrator.instances.clear()

    @pytest.mark.asyncio
    async def test_v2_orm_job_populates_ontology_when_published(self) -> None:
        """V2 path: ORMExtractionJob ontology fields are set when an ontology is published."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        # session.add is synchronous on AsyncSession (it just marks the
        # object for insertion; the real await happens on flush).
        mock_session.add = MagicMock()
        ov_id = _uuid.uuid4()

        mock_ov = MagicMock()
        mock_ov.id = ov_id
        mock_ov.version = "1.2.3"
        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=mock_ov)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_settings = MagicMock()
        mock_settings.extraction_v2_enabled = True

        with (
            patch(
                "nfm_db.config.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
                _FakeV2Orchestrator,
            ),
        ):
            await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="file",
            )

        assert len(_FakeV2Orchestrator.instances) == 1, (
            "V2 path must construct exactly one ORMExtractionJob per trigger"
        )
        orm_job = _FakeV2Orchestrator.instances[0]
        assert orm_job.ontology_version_id == ov_id
        assert orm_job.ontology_version_str == "1.2.3"

    @pytest.mark.asyncio
    async def test_v2_orm_job_ontology_none_when_not_published(self) -> None:
        """V2 path: ORMExtractionJob ontology fields stay None when no ontology is published."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        # session.add is synchronous on AsyncSession — see comment in test above.
        mock_session.add = MagicMock()

        mock_scalars = MagicMock()
        mock_scalars.first = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_settings = MagicMock()
        mock_settings.extraction_v2_enabled = True

        with (
            patch(
                "nfm_db.config.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "nfm_db.services.extraction_orchestrator.ExtractionOrchestrator",
                _FakeV2Orchestrator,
            ),
        ):
            await trigger_extraction(
                mock_session,
                source_reference="test_source",
                source_type="file",
            )

        assert len(_FakeV2Orchestrator.instances) == 1
        orm_job = _FakeV2Orchestrator.instances[0]
        assert orm_job.ontology_version_id is None
        assert orm_job.ontology_version_str is None
