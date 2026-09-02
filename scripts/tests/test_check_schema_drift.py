"""Tests for scripts/check_schema_drift.py (NFM-3372 / NFM-3377 / NFM-3446).

These tests use sqlite in-memory so they exercise ``compute_drift`` against
a real ``MigrationContext`` without spinning up a Postgres server. The
production guard runs against Postgres; these tests pin the diff-rendering
contract so a regression in the renderer is caught before it reaches CI.

NFM-3446 Phase 1: the 253-item backlog on main breaks down into 165 WARN
(modify_comment / remove_index / missing_index) and 88 FAIL (everything
else). The classes at the bottom of this module pin that taxonomy so a
future change cannot silently re-promote a WARN category back to FAIL.
"""

# ruff: noqa: UP031

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
        cols = [Column(cname, Integer if cname == "id" else String(50)) for cname in col_names]
        if "id" in col_names:
            # Mark `id` as primary key if present.
            cols = [Column("id", Integer, primary_key=True) if c == "id" else c for c in cols]
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

    def test_migration_chain_failure_returns_1_with_drift_line(self, monkeypatch, capsys):
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


# ---------------------------------------------------------------------------
# NFM-3446 Phase 1 — severity classification contract.
#
# These tests pin the demotion rule so a future PR cannot silently
# re-promote a WARN category back to FAIL or, equivalently, soften the
# critical categories that Phase 2 will eventually migrate away.
# ---------------------------------------------------------------------------


# Drift taxonomy on main 8-21 02:35 UTC (job 96649637808, c3dfb4c8).
# Demoted to WARN: modify_comment, remove_index, missing_index — 165 items.
# Kept FAIL (Phase 2 will write a migration for them): the rest — 88 items.
WARN_KINDS = frozenset({"modify_comment", "remove_index", "missing_index"})


class TestDriftSeverityField:
    """``Drift.severity`` is the new contract — defaults to FAIL.

    Defaulting to FAIL preserves the NFM-3372 hard rule: a regression in
    classification must never silently downgrade drift. If a new alembic
    op appears, it lands as FAIL until a human re-classifies it.
    """

    def test_severity_defaults_to_fail(self):
        d = csd.Drift(kind="anything_unknown", table="x")
        assert d.severity is csd.FAIL

    def test_severity_is_explicitly_warn(self):
        d = csd.Drift(
            kind="modify_comment",
            table="x",
            severity=csd.WARN,
        )
        assert d.severity is csd.WARN


class TestDriftRenderPrefix:
    """Severity controls the greppable CI-log prefix.

    Existing CI parsers key on ``DRIFT:`` for failures. Phase 1 adds
    ``WARN:`` for the demoted categories so a downstream cron can scrape
    them without conflating them with hard failures.
    """

    def test_warn_render_uses_warn_prefix(self):
        d = csd.Drift(kind="modify_comment", table="t", detail="d", severity=csd.WARN)
        line = d.render()
        assert line.startswith("WARN: ")
        assert "t" in line
        assert "d" in line

    def test_fail_render_uses_drift_prefix(self):
        d = csd.Drift(kind="missing_column", table="t", detail="d", severity=csd.FAIL)
        line = d.render()
        assert line.startswith("DRIFT: ")


class TestNormalizeOpSeverity:
    """``_normalize_op`` attaches the right severity to each drift kind."""

    def _table(self, name: str, *col_names: str) -> Table:
        metadata = MetaData()
        cols = [Column(cname, Integer if cname == "id" else String(50)) for cname in col_names]
        if "id" in col_names:
            cols = [Column("id", Integer, primary_key=True) if c == "id" else c for c in cols]
        return Table(name, metadata, *cols)

    def _drift(self, kind: str) -> csd.Drift:
        """Run a synthetic diff tuple through ``_normalize_op``."""
        # For 'add_index' / 'add_fk' we need a real Index / FK object; for
        # the rest a stub second element is enough because the fallback
        # branch never inspects it.
        if kind == "missing_index":
            table = self._table("users", "id")
            idx = Index("ix_users_email", table.c.id)
            return next(csd._normalize_op(("add_index", idx)))
        if kind == "missing_fk":
            # Fake FK object — _normalize_op only reads ``.name``.
            class _FkStub:
                name = "fk_users_parent_id_fkey"

            return next(csd._normalize_op(("add_fk", _FkStub())))
        return next(csd._normalize_op((kind, "<stub>")))

    def test_modify_comment_is_warn(self):
        assert self._drift("modify_comment").severity is csd.WARN

    def test_remove_index_is_warn(self):
        assert self._drift("remove_index").severity is csd.WARN

    def test_missing_index_is_warn(self):
        assert self._drift("missing_index").severity is csd.WARN

    def test_modify_nullable_is_fail(self):
        assert self._drift("modify_nullable").severity is csd.FAIL

    def test_modify_type_is_fail(self):
        assert self._drift("modify_type").severity is csd.FAIL

    def test_missing_column_is_fail(self):
        assert self._drift("missing_column").severity is csd.FAIL

    def test_missing_table_is_fail(self):
        assert self._drift("missing_table").severity is csd.FAIL

    def test_missing_fk_is_fail(self):
        assert self._drift("missing_fk").severity is csd.FAIL

    def test_remove_column_is_fail(self):
        assert self._drift("remove_column").severity is csd.FAIL

    def test_remove_fk_is_fail(self):
        assert self._drift("remove_fk").severity is csd.FAIL

    def test_add_constraint_is_fail(self):
        assert self._drift("add_constraint").severity is csd.FAIL

    def test_unknown_op_defaults_to_fail(self):
        """NFM-3372 hard rule: never silently drop drift."""
        assert self._drift("future_alembic_op").severity is csd.FAIL


class TestStrictMode:
    """``--strict`` re-promotes every category to FAIL.

    Useful for one-off investigations where the team wants to see the
    full taxonomy without letting the script soft-pass. Strict must NOT
    change which kinds are *rendered* — only which are exit-1.
    """

    def test_strict_flag_is_accepted(self, monkeypatch):
        """main() must accept --strict without error and exit 0 on a clean DB."""
        engine = create_engine("sqlite:///:memory:")
        try:
            metadata = MetaData()
            Table("users", metadata, Column("id", Integer, primary_key=True))
            metadata.create_all(engine)
            monkeypatch.setattr(csd, "_sync_engine_from_url", lambda url: engine)
            monkeypatch.setattr(csd, "_load_base_metadata", lambda: metadata)
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                    "--strict",
                ]
            )
            assert rc == 0
        finally:
            engine.dispose()

    def test_compute_severity_strict_overrides_warn(self):
        """compute_severity(kind, strict=True) must return FAIL even for WARN kinds."""
        for kind in WARN_KINDS:
            assert csd.compute_severity(kind, strict=True) is csd.FAIL, (
                f"--strict must re-promote {kind} to FAIL"
            )

    def test_compute_severity_default_keeps_warn(self):
        for kind in WARN_KINDS:
            assert csd.compute_severity(kind) is csd.WARN


class TestMainExitCodeWithMixedSeverity:
    """main() must split WARN from FAIL and exit 1 only on FAIL items."""

    def _run_with_synthetic_drifts(
        self,
        monkeypatch,
        drifts: list[csd.Drift],
    ) -> tuple[int, str]:
        """Run main() with a fake compute_drift returning ``drifts``.

        Returns (exit_code, captured_stderr).
        """
        monkeypatch.setattr(csd, "compute_drift", lambda conn, target_metadata=None: drifts)
        engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr(csd, "_sync_engine_from_url", lambda url: engine)
        monkeypatch.setattr(csd, "_load_base_metadata", lambda: MetaData())
        # Skip alembic upgrade; we only care about the diff-rendering path.
        try:
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                ]
            )
            import io

            buf = io.StringIO()
            # The test below uses capsys; this helper is just a building
            # block — the actual assertion is in the caller.
            return rc, buf.getvalue()
        finally:
            engine.dispose()

    def test_only_warn_items_exit_zero(self, monkeypatch, capsys):
        """165 WARN items must not block CI — main() exits 0."""
        monkeypatch.setattr(
            csd,
            "compute_drift",
            lambda conn, target_metadata=None: [
                csd.Drift(
                    kind="modify_comment",
                    table=f"t{i}",
                    severity=csd.WARN,
                )
                for i in range(165)
            ],
        )
        engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr(csd, "_sync_engine_from_url", lambda url: engine)
        monkeypatch.setattr(csd, "_load_base_metadata", lambda: MetaData())
        try:
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                ]
            )
        finally:
            engine.dispose()
        assert rc == 0, "WARN-only drift must exit 0 in soft-fail mode"
        err = capsys.readouterr().err
        # All lines are WARN:, no DRIFT: lines, no FAIL banner.
        assert "WARN: " in err
        assert "FAIL:" not in err

    def test_any_fail_item_exits_one(self, monkeypatch, capsys):
        """88 FAIL items must still block CI — main() exits 1."""
        monkeypatch.setattr(
            csd,
            "compute_drift",
            lambda conn, target_metadata=None: [
                csd.Drift(
                    kind="modify_comment",
                    table="t_warn",
                    severity=csd.WARN,
                ),
                csd.Drift(
                    kind="missing_column",
                    table="t_fail",
                    detail="x",
                    severity=csd.FAIL,
                ),
            ],
        )
        engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr(csd, "_sync_engine_from_url", lambda url: engine)
        monkeypatch.setattr(csd, "_load_base_metadata", lambda: MetaData())
        try:
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                ]
            )
        finally:
            engine.dispose()
        assert rc == 1, "Any FAIL drift must exit 1 even when WARNs are present"
        err = capsys.readouterr().err
        assert "DRIFT: missing_column t_fail" in err
        assert "WARN: modify_comment t_warn" in err

    def test_strict_promotes_warn_to_fail_exit(self, monkeypatch, capsys):
        """With --strict, WARN items are treated as FAIL → exit 1."""
        monkeypatch.setattr(
            csd,
            "compute_drift",
            lambda conn, target_metadata=None: [
                csd.Drift(
                    kind="modify_comment",
                    table="t",
                    severity=csd.WARN,
                ),
            ],
        )
        engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr(csd, "_sync_engine_from_url", lambda url: engine)
        monkeypatch.setattr(csd, "_load_base_metadata", lambda: MetaData())
        try:
            rc = csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                    "--strict",
                ]
            )
        finally:
            engine.dispose()
        assert rc == 1, "--strict must re-promote WARN to FAIL"

    def test_soft_fail_summary_counts(self, monkeypatch, capsys):
        """Soft-fail mode prints both counts so reviewers see the full picture."""
        monkeypatch.setattr(
            csd,
            "compute_drift",
            lambda conn, target_metadata=None: [
                csd.Drift(kind="modify_comment", table="a", severity=csd.WARN),
                csd.Drift(kind="modify_comment", table="b", severity=csd.WARN),
                csd.Drift(
                    kind="missing_column",
                    table="c",
                    detail="x",
                    severity=csd.FAIL,
                ),
            ],
        )
        engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr(csd, "_sync_engine_from_url", lambda url: engine)
        monkeypatch.setattr(csd, "_load_base_metadata", lambda: MetaData())
        try:
            csd.main(
                [
                    "--database-url",
                    "sqlite:///:memory:",
                    "--no-apply-migrations",
                ]
            )
        finally:
            engine.dispose()
        err = capsys.readouterr().err
        # The summary line should mention both WARN and FAIL counts.
        assert "2 warn" in err.lower() or "warn: 2" in err.lower()
        assert "1 fail" in err.lower() or "fail: 1" in err.lower()


if __name__ == "__main__":
    unittest.main()
