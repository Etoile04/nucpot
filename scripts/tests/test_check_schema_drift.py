"""Tests for scripts/check_schema_drift.py (NFM-3372 / NFM-3377).

These tests use sqlite in-memory so they exercise ``compute_drift`` against
a real ``MigrationContext`` without spinning up a Postgres server. The
production guard runs against Postgres; these tests pin the diff-rendering
contract so a regression in the renderer is caught before it reaches CI.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest
from sqlalchemy import Column, Index, Integer, MetaData, String, Table, create_engine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import check_schema_drift as csd  # noqa: E402


class TestDriftDataclass(unittest.TestCase):
    """``Drift.render()`` produces greppable output (AC#4)."""

    def test_render_without_detail(self):
        d = csd.Drift(kind="missing_table", table="public.kg_entity_types")
        self.assertEqual(d.render(), "DRIFT: missing_table public.kg_entity_types")

    def test_render_with_detail(self):
        d = csd.Drift(
            kind="missing_table",
            table="public.kg_entity_types",
            detail="id, name",
        )
        self.assertEqual(
            d.render(),
            "DRIFT: missing_table public.kg_entity_types (id, name)",
        )

    def test_render_is_greppable(self):
        d = csd.Drift(kind="missing_column", table="users", detail="email")
        line = d.render()
        self.assertTrue(line.startswith("DRIFT: "))
        self.assertIn("users", line)
        self.assertIn("email", line)


class TestNormalizeOp(unittest.TestCase):
    """``_normalize_op`` translates alembic diff tuples into Drift rows."""

    def _table(self, name: str, *col_names: str) -> Table:
        metadata = MetaData()
        cols = [
            Column(cname, Integer if cname == "id" else String(50))
            for cname in col_names
        ]
        if "id" in col_names:
            # Mark `id` as primary key if present.
            cols = [
                Column("id", Integer, primary_key=True)
                if c == "id"
                else c
                for c in cols
            ]
        return Table(name, metadata, *cols)

    def test_add_table_renders_missing_table(self):
        table = self._table("users", "id", "name")
        drifts = list(csd._normalize_op(("add_table", table)))
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].kind, "missing_table")
        self.assertEqual(drifts[0].table, "users")
        # Column names are sorted so the output is deterministic.
        self.assertEqual(drifts[0].detail, "id, name")

    def test_add_column_renders_missing_column(self):
        col = Column("email", String(120))
        drifts = list(csd._normalize_op(("add_column", None, "users", col)))
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].kind, "missing_column")
        self.assertEqual(drifts[0].table, "users")
        self.assertEqual(drifts[0].detail, "email")

    def test_unknown_action_surfaces_kind_and_table(self):
        """Forward drift directives without an explicit handler still produce
        a Drift row so the diff is never silently dropped (hard constraint:
        do not silently ignore drift)."""
        # ``remove_table`` is reverse-direction drift; per the design we still
        # surface it (with its raw action name) so reviewers see it.
        table = self._table("legacy_audit", "id")
        drifts = list(csd._normalize_op(("remove_table", table)))
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].kind, "remove_table")
        self.assertEqual(drifts[0].table, "legacy_audit")

    def test_add_index_renders_missing_index(self):
        # Real alembic shape: ``("add_index", Index)`` (2-tuple).
        metadata = MetaData()
        users = Table(
            "users",
            metadata,
            Column("id", Integer, primary_key=True),
        )
        idx = Index("ix_users_email", users.c.id)
        drifts = list(csd._normalize_op(("add_index", idx)))
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].kind, "missing_index")
        self.assertEqual(drifts[0].table, "users")
        self.assertEqual(drifts[0].detail, "ix_users_email")

    def test_iter_diffs_flattens_nested_lists(self):
        # The alembic autogenerate may return ``modify_*`` ops grouped in a
        # nested list. _iter_diffs must walk depth-first so each directive is
        # normalized.
        nested = [
            ("add_table", self._table("kg_entity_types", "id", "name")),
            [
                ("modify_nullable", None, "users", "email", {}, True, False),
            ],
        ]
        flat = list(csd._iter_diffs(nested))
        self.assertEqual(len(flat), 2)
        self.assertEqual(flat[0][0], "add_table")
        self.assertEqual(flat[1][0], "modify_nullable")


class TestComputeDrift(unittest.TestCase):
    """End-to-end: ``compute_drift`` against a live sqlite connection."""

    def test_no_drift_when_metadata_matches(self):
        # AC#3: passes on origin/main once NFM-3370 has landed.
        metadata = MetaData()
        Table(
            "users",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(50)),
        )
        engine = create_engine("sqlite:///:memory:")
        try:
            metadata.create_all(engine)
            with engine.connect() as conn:
                drifts = csd.compute_drift(conn, target_metadata=metadata)
            self.assertEqual(drifts, [], "Expected zero drift; got %r" % drifts)
        finally:
            engine.dispose()

    def test_missing_table_is_reported(self):
        # AC#2: catches the kg_entity_types class of defect.
        live_metadata = MetaData()
        Table(
            "users",
            live_metadata,
            Column("id", Integer, primary_key=True),
        )

        target_metadata = MetaData()
        Table(
            "users",
            target_metadata,
            Column("id", Integer, primary_key=True),
        )
        # The drift: target declares ``kg_entity_types`` but the live DB
        # does not have it (NFM-3370 was reverted).
        Table(
            "kg_entity_types",
            target_metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(50)),
        )

        engine = create_engine("sqlite:///:memory:")
        try:
            live_metadata.create_all(engine)
            with engine.connect() as conn:
                drifts = csd.compute_drift(conn, target_metadata=target_metadata)
        finally:
            engine.dispose()

        table_drifts = [d for d in drifts if d.kind == "missing_table"]
        self.assertEqual(
            [d.table for d in table_drifts],
            ["kg_entity_types"],
            "Expected kg_entity_types missing_table drift, got %r" % drifts,
        )

    def test_missing_column_is_reported(self):
        # AC#4: failure output names specific drifted columns.
        live_metadata = MetaData()
        Table(
            "users",
            live_metadata,
            Column("id", Integer, primary_key=True),
        )

        target_metadata = MetaData()
        Table(
            "users",
            target_metadata,
            Column("id", Integer, primary_key=True),
            # Drift: declared in the model but not in the live schema.
            Column("email", String(120)),
        )

        engine = create_engine("sqlite:///:memory:")
        try:
            live_metadata.create_all(engine)
            with engine.connect() as conn:
                drifts = csd.compute_drift(conn, target_metadata=target_metadata)
        finally:
            engine.dispose()

        column_drifts = [d for d in drifts if d.kind == "missing_column"]
        self.assertEqual(
            [(d.table, d.detail) for d in column_drifts],
            [("users", "email")],
        )


class TestMainCli:
    """``main()`` exit-code contract."""

    def test_missing_database_url_returns_2(self, monkeypatch, capsys):
        monkeypatch.delenv("NFM_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = csd.main(["--no-apply-migrations"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "NFM_DATABASE_URL" in err

    def test_no_drift_returns_0(self, monkeypatch):
        # With ``--no-apply-migrations`` and a sqlite URL whose schema equals
        # the test metadata, the script exits 0.
        engine = create_engine("sqlite:///:memory:")
        try:
            metadata = MetaData()
            Table(
                "users",
                metadata,
                Column("id", Integer, primary_key=True),
            )
            metadata.create_all(engine)

            def _fake_sync_engine(url):
                return engine

            monkeypatch.setattr(csd, "_sync_engine_from_url", _fake_sync_engine)
            # Stub out ``nfm_db.models.Base.metadata`` so the test does not
            # need the full ``nfm_db`` package installed (and so we can use a
            # sqlite-friendly metadata without the Postgres-only types that
            # would create spurious drift).
            monkeypatch.setattr(csd, "_load_base_metadata", lambda: metadata)
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                ]
            )
            assert rc == 0, "Expected zero drift exit code"
        finally:
            engine.dispose()

    def test_drift_returns_1_and_prints_drift_lines(self, monkeypatch, capsys):
        # End-to-end: a missing table in the live DB produces exit 1 and a
        # greppable ``DRIFT:`` line on stderr (AC#4).
        engine = create_engine("sqlite:///:memory:")
        try:
            live = MetaData()
            Table("users", live, Column("id", Integer, primary_key=True))
            target = MetaData()
            Table("users", target, Column("id", Integer, primary_key=True))
            Table(
                "kg_entity_types",
                target,
                Column("id", Integer, primary_key=True),
                Column("name", String(50)),
            )
            live.create_all(engine)

            def _fake_sync_engine(url):
                return engine

            monkeypatch.setattr(csd, "_sync_engine_from_url", _fake_sync_engine)
            monkeypatch.setattr(csd, "_load_base_metadata", lambda: target)
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                ]
            )
            assert rc == 1, "Expected drift exit code"
            err = capsys.readouterr().err
            assert "DRIFT:" in err
            assert "kg_entity_types" in err
        finally:
            engine.dispose()

    def test_migration_chain_failure_returns_1_with_drift_line(
        self, monkeypatch, capsys
    ):
        # AC#2: when alembic upgrade head itself fails (the kg_entity_types
        # class of defect — the chain tries to add a column to a table no
        # migration ever created), the script exits 1 with a greppable
        # DRIFT line, NOT a raw alembic stacktrace.
        def _raise_migrations(url):
            raise csd.MigrationFailure(
                "alembic upgrade head failed at "
                "055_add_ontology_version_fk_to_type_tables: "
                'relation "kg_entity_types" does not exist'
            )

        monkeypatch.setattr(csd, "run_migrations", _raise_migrations)
        rc = csd.main(
            [
                "--database-url",
                "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db",
            ]
        )
        assert rc == 1, "MigrationFailure must produce exit 1"
        err = capsys.readouterr().err
        assert "DRIFT:" in err
        assert "kg_entity_types" in err
        assert "migration_chain" in err


if __name__ == "__main__":
    unittest.main()
