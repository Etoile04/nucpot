"""NFM-2152: ExtractionJob.figure_types must be JSONB to match alembic 035.

NFM-2137 (commit ``bc26392``) added the ``figure_types`` column via
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS figure_types JSONB`` on
PostgreSQL (migration 035 line 79) and ``sa.JSON()`` on SQLite.

The SQLAlchemy model ``apps/api/src/nfm_db/models/extraction_job.py``
lines 110-112 declared the column as ``JSONArray`` (PostgreSQL
``ARRAY(Text)`` / SQLite ``Text`` with JSON text serialization). That
mismatch causes the live ingest INSERT to crash with::

    asyncpg.exceptions.DatatypeMismatchError:
        column "figure_types" is of type jsonb but expression is of type text[]

NFM-2152 fixes the model to ``CompatJSONB`` (already imported in
``apps/api/src/nfm_db/models/__init__.py`` and used by
``dft_calculation.computation_metadata`` and
``entity_merge_log.details``).

These tests are deliberately offline — they verify (a) the model
import resolves ``CompatJSONB`` for ``figure_types``, (b) the DDL the
model emits on PostgreSQL compiles to ``JSONB`` (not ``ARRAY``), and
(c) a list round-trips through the column on SQLite (the test
dialect). Live PG verification lives in
``test_migration_035_multimodal_flags_runtime.py``.

Acceptance criteria:

* [AC-1] ``ExtractionJob.figure_types`` declared as ``CompatJSONB``
  (no longer ``JSONArray``).
* [AC-2] Compiled PG DDL for the column is ``JSONB`` (matches
  migration 035 line 79).
* [AC-3] A ``list[str]`` round-trips through ``figure_types``
  unchanged on the test (SQLite) dialect.
* [AC-4] ``figure_types=None`` is persisted and read back as
  ``None`` (preserves the ``nullable=True`` contract).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import Session

from nfm_db.models import CompatJSONB
from nfm_db.models.extraction_job import ExtractionJob

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = REPO_ROOT / "src" / "nfm_db" / "models" / "extraction_job.py"


# ---------------------------------------------------------------------------
# AC-1: model declares CompatJSONB, not JSONArray, for figure_types
# ---------------------------------------------------------------------------


class TestModelDeclaration:
    """Static checks against the model source."""

    def test_model_source_uses_compatjsonb_for_figure_types(self) -> None:
        """``figure_types`` Column line must use ``CompatJSONB``."""
        text = MODEL_PATH.read_text()
        # Find the figure_types column declaration block. The
        # type annotation ``Mapped[list[str] | None]`` contains nested
        # brackets, so we use ``.*?`` (DOTALL) to span them rather than
        # a character class that would stop at the first ``]``.
        match = re.search(
            r"figure_types:\s*Mapped\[.*?\]\s*=\s*mapped_column\("
            r"(?P<body>.*?)\)",
            text,
            re.DOTALL,
        )
        assert match is not None, (
            f"Could not locate figure_types column declaration in {MODEL_PATH}"
        )
        body = match.group("body")
        assert "CompatJSONB" in body, (
            "figure_types must be declared as CompatJSONB to match "
            f"migration 035 (JSONB). Got column body: {body!r}"
        )
        assert "JSONArray" not in body, (
            "figure_types must NOT be declared as JSONArray "
            f"(maps to ARRAY(Text), not JSONB). Got column body: {body!r}"
        )

    def test_model_imports_compatjsonb(self) -> None:
        """The module must import ``CompatJSONB`` from ``nfm_db.models``."""
        text = MODEL_PATH.read_text()
        # The imports block must include CompatJSONB.
        match = re.search(
            r"from\s+nfm_db\.models\s+import\s+(?P<imports>[^\n]+)",
            text,
        )
        assert match is not None, "extraction_job.py missing nfm_db.models import"
        imports = match.group("imports")
        assert "CompatJSONB" in imports, (
            f"extraction_job.py must import CompatJSONB. Imports: {imports!r}"
        )


# ---------------------------------------------------------------------------
# AC-2: compiled PG DDL is JSONB
# ---------------------------------------------------------------------------


class TestPostgresDDLAlignment:
    """SQLAlchemy emits JSONB on PostgreSQL — matches migration 035."""

    def test_pg_impl_is_jsonb(self) -> None:
        """The PG impl of the column type must be a SQLAlchemy ``JSONB``."""
        engine = create_engine(
            "postgresql://", strategy="mock", executor=lambda *args, **kwargs: None
        )
        column = ExtractionJob.__table__.columns["figure_types"]
        assert isinstance(column.type, CompatJSONB), (
            "Expected figure_types.type to be CompatJSONB, "
            f"got {type(column.type).__name__}"
        )
        # CompatJSONB.load_dialect_impl returns PG JSONB() on PG — the
        # underlying impl must be a SQLAlchemy JSONB instance.
        impl = column.type.load_dialect_impl(engine.dialect)
        assert isinstance(impl, PG_JSONB), (
            f"Expected PG impl of CompatJSONB to be JSONB, "
            f"got {type(impl).__name__}"
        )


# ---------------------------------------------------------------------------
# AC-3 + AC-4: round-trip a list and a None on the test (SQLite) dialect
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Persist + read-back via SQLite (the pytest test dialect)."""

    @pytest.fixture()
    def sqlite_session(self) -> Session:
        """Yield a Session bound to a fresh in-memory SQLite DB.

        Only ``ExtractionJob.__table__`` is created — using the global
        ``Base.metadata`` would force SQLite to render every model's
        CREATE TABLE (including models with raw ``JSONB`` columns that
        SQLite cannot compile).
        """
        engine = create_engine("sqlite:///:memory:")
        ExtractionJob.__table__.create(engine)
        with Session(engine) as session:
            yield session

    def test_figure_types_round_trips_list(self, sqlite_session: Session) -> None:
        """A ``list[str]`` survives INSERT + SELECT."""
        job = ExtractionJob(figure_types=["line", "bar", "heatmap"])
        sqlite_session.add(job)
        sqlite_session.commit()

        loaded = sqlite_session.get(ExtractionJob, job.id)
        assert loaded is not None
        assert loaded.figure_types == ["line", "bar", "heatmap"], (
            f"figure_types list did not round-trip: got {loaded.figure_types!r}"
        )

    def test_figure_types_round_trips_none(self, sqlite_session: Session) -> None:
        """``figure_types=None`` survives INSERT + SELECT."""
        job = ExtractionJob(figure_types=None)
        sqlite_session.add(job)
        sqlite_session.commit()

        loaded = sqlite_session.get(ExtractionJob, job.id)
        assert loaded is not None
        assert loaded.figure_types is None, (
            f"figure_types None did not round-trip: got {loaded.figure_types!r}"
        )

    def test_figure_types_round_trips_empty_list(
        self,
        sqlite_session: Session,
    ) -> None:
        """An empty list round-trips as ``[]`` (not ``None``)."""
        job = ExtractionJob(figure_types=[])
        sqlite_session.add(job)
        sqlite_session.commit()

        loaded = sqlite_session.get(ExtractionJob, job.id)
        assert loaded is not None
        assert loaded.figure_types == [], (
            f"figure_types empty list did not round-trip: got {loaded.figure_types!r}"
        )

    def test_figure_types_default_is_none(self) -> None:
        """An ExtractionJob constructed with no figure_types arg defaults to None."""
        job = ExtractionJob()
        assert job.figure_types is None, (
            f"Default figure_types must be None, got {job.figure_types!r}"
        )

    def test_figure_types_independent_per_row(self, sqlite_session: Session) -> None:
        """Two distinct jobs carry independent figure_types values."""
        job_a = ExtractionJob(figure_types=["line"])
        job_b = ExtractionJob(figure_types=["bar", "scatter"])
        sqlite_session.add_all([job_a, job_b])
        sqlite_session.commit()

        loaded_a = sqlite_session.get(ExtractionJob, job_a.id)
        loaded_b = sqlite_session.get(ExtractionJob, job_b.id)
        assert loaded_a is not None and loaded_b is not None
        assert loaded_a.figure_types == ["line"]
        assert loaded_b.figure_types == ["bar", "scatter"]
        assert loaded_a.id != loaded_b.id
