"""NFM-4633: PropertyMeasurement.validity_check must be JSONB on PostgreSQL.

The G1-D migration 088 (``088_add_validity_check_and_valid_range``,
NFM-4550 AC-10) creates ``property_measurements.validity_check`` with
``sa.JSON().with_variant(postgresql.JSONB(), "postgresql")`` — i.e. a
native ``JSONB`` column on PostgreSQL (the migration docstring and spec
§8.4 both say JSONB). Staging and production (shipped via NFM-4615,
alembic head 088) carry the column as ``jsonb``.

The model, however, declared the column with SQLAlchemy's generic
``JSON`` — which renders as plain ``json`` in ``Base.metadata``. The
``schema-drift-guard`` CI job (``scripts/check_schema_drift.py``)
compares ``Base.metadata`` against the fresh-migrated schema and
reported::

    FAIL: 1 failing drift(s), 192 warning(s)
    DRIFT: modify_type property_measurements

(exited 1 on PR #1296, run 34486392271 — the first tree since the G1
merge f5536d2c7 whose ``backend`` job passed, so the guard finally ran;
before that the red was masked by ``needs: [backend]`` + the NFM-4620
file-size failure).

This is the same defect class NFM-2152 fixed for
``ExtractionJob.figure_types``: the model must use ``CompatJSONB``
(JSONB on PostgreSQL, JSON-text on SQLite), not a generic ``JSON``.

These tests are deliberately offline — they verify (a) the model
declares ``CompatJSONB`` for ``validity_check`` (via the ORM table
metadata), (b) the type's PostgreSQL dialect implementation is
``JSONB`` (the exact assertion the drift guard violated), and (c) a
``{status, reason}`` payload and ``None`` round-trip on the SQLite
test dialect.

Acceptance criteria:

* [AC-1] ``PropertyMeasurement.validity_check`` declared as
  ``CompatJSONB`` (no longer generic ``JSON``).
* [AC-2] The PostgreSQL dialect impl of the column type is
  ``sqlalchemy.dialects.postgresql.JSONB`` — matches migration 088 and
  the shipped prod/staging schema.
* [AC-3] A ``{status, reason}`` dict round-trips through the column
  unchanged on the test (SQLite) dialect.
* [AC-4] ``validity_check=None`` round-trips as ``None`` (preserves the
  ``nullable=True`` contract — "no evaluation" stays distinguishable).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import DefaultClause, ForeignKeyConstraint, MetaData, create_engine
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import Session

from nfm_db.models import CompatJSONB, PropertyMeasurement

# ---------------------------------------------------------------------------
# AC-1 + AC-2: model declares CompatJSONB, whose PostgreSQL dialect impl
# is JSONB — the drift-guard assertion
# ---------------------------------------------------------------------------


class TestPostgresDDLAlignment:
    """The column type must resolve to JSONB on PostgreSQL."""

    def test_column_type_is_compatjsonb(self) -> None:
        """The declared type must be ``CompatJSONB`` (not generic ``JSON``)."""
        column = PropertyMeasurement.__table__.columns["validity_check"]
        assert isinstance(column.type, CompatJSONB), (
            "Expected validity_check.type to be CompatJSONB, got "
            f"{type(column.type).__name__} — a generic JSON renders as "
            "``json`` in Base.metadata and drifts from migration 088's "
            "JSONB column (NFM-4633 CI red)."
        )

    def test_pg_impl_is_jsonb(self) -> None:
        """The PG impl must be a SQLAlchemy ``JSONB`` instance.

        This is the exact comparison the schema-drift guard performs:
        ``compare_metadata`` sees the dialect-resolved metadata type, so
        anything other than JSONB keeps producing
        ``DRIFT: modify_type property_measurements``.
        """
        engine = create_engine(
            "postgresql://", strategy="mock", executor=lambda *args, **kwargs: None
        )
        column = PropertyMeasurement.__table__.columns["validity_check"]
        impl = column.type.load_dialect_impl(engine.dialect)
        assert isinstance(impl, PG_JSONB), (
            f"Expected PG impl of validity_check to be JSONB, got {type(impl).__name__}"
        )


# ---------------------------------------------------------------------------
# AC-3 + AC-4: round-trip the spec §8.4 payload on the test dialect
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Persist + read-back via SQLite (the pytest test dialect)."""

    @pytest.fixture()
    def sqlite_session(self) -> Generator[Session, None, None]:
        """Yield a Session bound to a fresh in-memory SQLite DB.

        The model table is cloned via ``to_metadata`` so the Postgres-only
        ``'{}'::jsonb``-style server-default casts (NFM-4548
        ``conditions``) can be stripped on the CLONE without mutating the
        global ``Base.metadata`` — the same compat transform conftest's
        ``_replace_jsonb`` applies for the full-suite fixtures. Creating
        the model table directly fails on SQLite with ``unrecognized
        token: ":"``.
        """
        engine = create_engine("sqlite:///:memory:")
        table = PropertyMeasurement.__table__.to_metadata(MetaData())
        for col in table.columns:
            default_sql = getattr(getattr(col.server_default, "arg", None), "text", None)
            if isinstance(default_sql, str) and "::" in default_sql:
                col.server_default = DefaultClause(sa_text(re.sub(r"::\s*\w+", "", default_sql)))
            # Single-table clone: every FK is dangling (its target table
            # is not in the clone's MetaData) — strip before CREATE, the
            # same transform conftest's ``_strip_dangling_fks`` applies.
            for fk in list(col.foreign_keys):
                col.foreign_keys.discard(fk)
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.discard(constraint)
        table.create(engine)
        with Session(engine) as session:
            yield session

    @staticmethod
    def _measurement(validity_check: dict[str, object] | None) -> PropertyMeasurement:
        """Build a minimal row satisfying NOT NULL + value-present CHECK.

        The ``id`` columns use the project's UUID→hex CHAR(32) type, so
        real ``uuid.UUID`` objects must be passed (strings fail the
        custom bind processor with ``'str' object has no attribute
        'hex'``).
        """
        return PropertyMeasurement(
            id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            property_type_id=uuid.uuid4(),
            value_scalar=13.6,
            validity_check=validity_check,
        )

    def test_validity_check_round_trips_status_payload(self, sqlite_session: Session) -> None:
        """A spec §8.4 ``{status, reason}`` dict survives INSERT + SELECT."""
        measurement = self._measurement({"status": "fail", "reason": "density < 0.5 g/cm^3"})
        sqlite_session.add(measurement)
        sqlite_session.commit()

        loaded = sqlite_session.get(PropertyMeasurement, measurement.id)
        assert loaded is not None
        assert loaded.validity_check == {
            "status": "fail",
            "reason": "density < 0.5 g/cm^3",
        }, f"validity_check dict did not round-trip: {loaded.validity_check!r}"

    def test_validity_check_round_trips_none(self, sqlite_session: Session) -> None:
        """``validity_check=None`` survives INSERT + SELECT (nullable contract)."""
        measurement = self._measurement(None)
        sqlite_session.add(measurement)
        sqlite_session.commit()

        loaded = sqlite_session.get(PropertyMeasurement, measurement.id)
        assert loaded is not None
        assert loaded.validity_check is None, (
            f"validity_check None did not round-trip: {loaded.validity_check!r}"
        )
