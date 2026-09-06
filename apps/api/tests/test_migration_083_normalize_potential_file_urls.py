"""Runtime tests for migration 083 — NFM-4309 / BUG-37 file_url normalization.

Follows the test_migration_063_reference_values_formal.py harness: bind the
module-level ``op`` proxy to a real SQLite connection via ``Operations.context``,
seed fixture rows covering every historical form, run ``module.upgrade()``,
and assert the canonical rewrite.

``env.py``'s ``pg_advisory_lock`` makes full ``alembic upgrade`` runs
Postgres-only, which is why the migration body is exercised in isolation.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

_MIGRATION_PATH = Path("migrations/versions/083_normalize_potential_file_urls.py").resolve()


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("_nfm4309_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seeded_db() -> sa.Engine:
    """In-memory SQLite DB with a minimal potentials table + form fixtures."""
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE potentials ("
                " id VARCHAR(36) PRIMARY KEY,"
                " name VARCHAR(256),"
                " file_url VARCHAR(512),"
                " extra JSON)"
            )
        )
        fixtures = [
            # (name, file_url, extra) — one row per historical form
            ("f-uploads", "/uploads/abc-123.tersoff", {"status": "pending"}),
            ("f-container", "/app/uploads/Fe_Mendelev_2007v2.eam.fs", {}),
            (
                "f-supabase-rel",
                "/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp",
                {"irradiationRelevant": False},
            ),
            (
                "f-supabase-multi",
                "/storage/v1/object/public/potentials/library/d.eam.alloy,"
                "/storage/v1/object/public/potentials/library/s.eam.fs",
                {},
            ),
            (
                "f-http-abs",
                "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/"
                "public/potentials/library/Al_Mendelev_2008.eam.fs",
                {},
            ),
            ("f-empty", None, {}),
            ("f-empty-str", "", {}),
            ("f-bare", "lonely.eam.alloy", {"keep": "me"}),
        ]
        for name, url, extra in fixtures:
            conn.execute(
                sa.text(
                    "INSERT INTO potentials (id, name, file_url, extra) "
                    "VALUES (:id, :name, :url, :extra)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "url": url,
                    "extra": json.dumps(extra),
                },
            )
        conn.commit()
    return engine


def _run_upgrade(engine: sa.Engine) -> None:
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            _load_migration_module().upgrade()
        conn.commit()


def _rows(engine: sa.Engine) -> dict[str, tuple[str, str, dict]]:
    with engine.connect() as conn:
        result = conn.execute(sa.text("SELECT id, name, file_url, extra FROM potentials"))
        return {
            name: (str(row_id), url, json.loads(extra))
            for row_id, name, url, extra in result.fetchall()
        }


def test_upgrade_rewrites_all_legacy_forms_to_canonical() -> None:
    engine = _seeded_db()
    _run_upgrade(engine)
    rows = _rows(engine)

    # Migratable forms → canonical URL + storage ref.
    for name in ("f-uploads", "f-container", "f-supabase-rel", "f-http-abs"):
        row_id, url, extra = rows[name]
        assert url == f"/api/v1/potentials/{row_id}/file", name
        assert "file_storage" in extra, name

    _, _, extra = rows["f-uploads"]
    assert extra["file_storage"] == {"kind": "uploads", "key": "abc-123.tersoff"}
    assert extra["status"] == "pending"  # pre-existing extra keys preserved

    _, _, extra = rows["f-container"]
    assert extra["file_storage"] == {
        "kind": "uploads",
        "key": "Fe_Mendelev_2007v2.eam.fs",
    }

    _, _, extra = rows["f-supabase-rel"]
    assert extra["file_storage"] == {
        "kind": "supabase",
        "objects": ["potentials/huda/Ag2S_MTP.mtp"],
    }
    assert extra["irradiationRelevant"] is False

    _, _, extra = rows["f-supabase-multi"]
    assert extra["file_storage"]["objects"] == [
        "potentials/library/d.eam.alloy",
        "potentials/library/s.eam.fs",
    ]

    _, _, extra = rows["f-http-abs"]
    # Absolute Supabase URL collapses to the bare object path (no baked origin).
    assert extra["file_storage"]["objects"] == ["potentials/library/Al_Mendelev_2008.eam.fs"]

    # Unrecoverable bare filename → blanked with a note, extra preserved.
    _, url, extra = rows["f-bare"]
    assert url == ""
    assert extra["keep"] == "me"
    assert "BUG-37" in extra["file_url_note"]

    # Empty rows untouched.
    assert rows["f-empty"][1] is None
    assert rows["f-empty-str"][1] == ""


def test_upgrade_is_idempotent() -> None:
    engine = _seeded_db()
    _run_upgrade(engine)
    first = _rows(engine)
    _run_upgrade(engine)
    second = _rows(engine)

    assert first == second  # second pass is a no-op, note not doubled
    assert first["f-bare"][1] == ""


def test_downgrade_is_noop() -> None:
    engine = _seeded_db()
    _load_migration_module().downgrade()  # must not raise
    rows = _rows(engine)
    assert rows["f-uploads"][1] == "/uploads/abc-123.tersoff"
