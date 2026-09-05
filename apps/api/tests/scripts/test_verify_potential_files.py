"""Tests for apps/api/scripts/verify_potential_files.py (NFM-4309 / BUG-37).

Two behaviors are pinned:

1. **Transient upstream failures never blank rows** — only definitive
   not-found verdicts (uploads-volume misses, HTTP 400/404, empty
   200 body) may be cleared by ``--apply``; network errors, auth
   failures (403) and 429/5xx are reported as unverifiable and left
   untouched.
2. **Foreign-origin URLs are never fetched server-side** (SSRF guard,
   same policy as the download proxy) and are reported unverifiable.

The DB-backed tests run the real ``sweep()`` against a temp-file SQLite
database with a minimal ``potentials`` table (same harness idea as
test_migration_083_normalize_potential_file_urls.py) and a stubbed
``httpx.AsyncClient`` that maps object URLs to canned statuses.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import httpx  # noqa: E402
import verify_potential_files as sweep_mod  # noqa: E402

from nfm_db.services import upload_service  # noqa: E402
from nfm_db.services.potential_file_resolver import (  # noqa: E402
    DEFAULT_SUPABASE_PUBLIC_ORIGIN,
)

_SUPABASE = DEFAULT_SUPABASE_PUBLIC_ORIGIN


# ---------------------------------------------------------------------------
# httpx stub — maps public object URLs to canned upstream outcomes
# ---------------------------------------------------------------------------


class _StubSupabaseClient:
    """``httpx.AsyncClient`` stand-in driven by ``_STUB_RESPONSES``.

    ``_STUB_RESPONSES`` maps full URL → HTTP status int, the sentinel
    ``"empty"`` (200 with zero bytes), or an ``Exception`` to raise.
    Unmapped URLs answer 200 with a body.
    """

    _STUB_RESPONSES: dict[str, int | str | Exception] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _StubSupabaseClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str) -> SimpleNamespace:
        outcome = type(self)._STUB_RESPONSES.get(url, 200)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "empty":
            return SimpleNamespace(status_code=200, content=b"")
        return SimpleNamespace(status_code=outcome, content=b"payload" if outcome == 200 else b"")


def _public_url(obj: str) -> str:
    return f"{_SUPABASE}/storage/v1/object/public/{obj}"


@pytest.fixture
def stub_supabase(monkeypatch: pytest.MonkeyPatch) -> dict:
    responses: dict[str, int | str | Exception] = {}
    _StubSupabaseClient._STUB_RESPONSES = responses
    monkeypatch.setattr(sweep_mod.httpx, "AsyncClient", _StubSupabaseClient)
    return responses


# ---------------------------------------------------------------------------
# _verify_supabase — tri-state verdicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_supabase_ok(stub_supabase) -> None:
    state, error = await sweep_mod._verify_supabase(_public_url("bucket/ok.mtp"))
    assert state == "ok"
    assert error == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404])
async def test_verify_supabase_definitive_miss(stub_supabase, status: int) -> None:
    stub_supabase[_public_url("bucket/gone.mtp")] = status
    state, error = await sweep_mod._verify_supabase(_public_url("bucket/gone.mtp"))
    assert state == "missing"
    assert str(status) in error


@pytest.mark.asyncio
async def test_verify_supabase_empty_body_is_missing(stub_supabase) -> None:
    stub_supabase[_public_url("bucket/empty.mtp")] = "empty"
    state, _ = await sweep_mod._verify_supabase(_public_url("bucket/empty.mtp"))
    assert state == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_verify_supabase_transient_statuses(stub_supabase, status: int) -> None:
    stub_supabase[_public_url("bucket/flaky.mtp")] = status
    state, _ = await sweep_mod._verify_supabase(_public_url("bucket/flaky.mtp"))
    assert state == "unverifiable"


@pytest.mark.asyncio
async def test_verify_supabase_network_error_is_unverifiable(stub_supabase) -> None:
    stub_supabase[_public_url("bucket/down.mtp")] = httpx.ConnectError("connection reset")
    state, error = await sweep_mod._verify_supabase(_public_url("bucket/down.mtp"))
    assert state == "unverifiable"
    assert "connection reset" in error


# ---------------------------------------------------------------------------
# _object_fetch_url — foreign origins are never fetched server-side
# ---------------------------------------------------------------------------


def test_object_fetch_url_relative_object() -> None:
    assert sweep_mod._object_fetch_url("potentials/huda/Ag2S_MTP.mtp") == _public_url(
        "potentials/huda/Ag2S_MTP.mtp"
    )


def test_object_fetch_url_supabase_absolute() -> None:
    absolute = _public_url("potentials/library/Al_Mendelev_2008.eam.fs")
    assert sweep_mod._object_fetch_url(absolute) == absolute


def test_object_fetch_url_foreign_origin_returns_none() -> None:
    assert sweep_mod._object_fetch_url("https://example.com/some/pot.dat") is None
    assert sweep_mod._object_fetch_url("http://169.254.169.254/latest/meta-data") is None


# ---------------------------------------------------------------------------
# sweep() end-to-end on SQLite — what --apply clears vs leaves untouched
# ---------------------------------------------------------------------------


def _seed_sweep_db(db_path: Path, upload_dir: Path) -> dict[str, str]:
    """Create a minimal potentials table and return name → id fixtures."""
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    ids: dict[str, str] = {}
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
        fixtures = {
            # uploads volume file that exists → untouched
            "up-present": "/uploads/present.tersoff",
            # uploads volume file that is gone → definitive miss
            "up-missing": "/uploads/missing.tersoff",
            # supabase object that answers 200 → untouched
            "sb-ok": "/storage/v1/object/public/potentials/ok.mtp",
            # supabase object that answers 404 → definitive miss
            "sb-gone": "/storage/v1/object/public/potentials/gone.mtp",
            # supabase object behind a 503 → transient, must survive --apply
            "sb-flaky": "/storage/v1/object/public/potentials/flaky.mtp",
            # foreign-origin absolute URL → never fetched, must survive
            "foreign": "https://example.com/some/pot.dat",
            # legacy multi-object row, first object gone but the second
            # still answers 200 → row survives --apply
            "multi-partial": (
                "/storage/v1/object/public/potentials/multi-gone0.mtp,"
                "/storage/v1/object/public/potentials/multi-ok1.mtp"
            ),
            # legacy multi-object row with every object gone → definitive miss
            "multi-all-gone": (
                "/storage/v1/object/public/potentials/multi-a.mtp,"
                "/storage/v1/object/public/potentials/multi-b.mtp"
            ),
            # legacy multi-object row, first object gone and the second
            # behind a 503 → unverifiable, must survive --apply
            "multi-gone-flaky": (
                "/storage/v1/object/public/potentials/multi-flaky-gone.mtp,"
                "/storage/v1/object/public/potentials/multi-flaky-503.mtp"
            ),
        }
        for name, url in fixtures.items():
            # Dashless hex mirrors how SQLAlchemy's Uuid type stores ids on
            # non-native backends; prod (PG) uses native uuids — the sweep's
            # typed bindparams handle both.
            row_id = uuid.uuid4().hex
            ids[name] = row_id
            conn.execute(
                sa.text(
                    "INSERT INTO potentials (id, name, file_url, extra) "
                    "VALUES (:id, :name, :url, :extra)"
                ),
                {"id": row_id, "name": name, "url": url, "extra": "{}"},
            )
        conn.commit()
    engine.dispose()

    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "present.tersoff").write_bytes(b"# tersoff body")
    return ids


def _file_urls(db_path: Path) -> dict[str, str]:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT name, file_url FROM potentials")).fetchall()
    engine.dispose()
    return {name: url for name, url in rows}


def _drop_rows(db_path: Path, names: list[str]) -> None:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        for name in names:
            conn.execute(sa.text("DELETE FROM potentials WHERE name = :n"), {"n": name})
        conn.commit()
    engine.dispose()


@pytest.mark.asyncio
async def test_sweep_apply_clears_only_definitive_misses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_supabase: dict,
) -> None:
    db_path = tmp_path / "sweep.db"
    upload_dir = tmp_path / "uploads"
    _seed_sweep_db(db_path, upload_dir)

    stub_supabase[_public_url("potentials/ok.mtp")] = 200
    stub_supabase[_public_url("potentials/gone.mtp")] = 404
    stub_supabase[_public_url("potentials/flaky.mtp")] = 503
    stub_supabase[_public_url("potentials/multi-gone0.mtp")] = 404
    stub_supabase[_public_url("potentials/multi-ok1.mtp")] = 200
    stub_supabase[_public_url("potentials/multi-a.mtp")] = 404
    stub_supabase[_public_url("potentials/multi-b.mtp")] = 400
    stub_supabase[_public_url("potentials/multi-flaky-gone.mtp")] = 404
    stub_supabase[_public_url("potentials/multi-flaky-503.mtp")] = 503

    monkeypatch.setenv("NFM_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(upload_service, "_UPLOAD_DIR_OVERRIDE", upload_dir)

    exit_code = await sweep_mod.sweep(apply=True)

    # Transient + foreign rows keep the exit code non-zero so the operator
    # re-runs instead of trusting a half-verified sweep.
    assert exit_code == 1

    urls = _file_urls(db_path)
    assert urls["up-present"] == "/uploads/present.tersoff"
    assert urls["sb-ok"] == "/storage/v1/object/public/potentials/ok.mtp"
    # Definitive misses are blanked.
    assert urls["up-missing"] == ""
    assert urls["sb-gone"] == ""
    assert urls["multi-all-gone"] == ""
    # Transient upstream failure must NOT be blanked (BUG-37: a network
    # error is not evidence the file is missing).
    assert urls["sb-flaky"] == "/storage/v1/object/public/potentials/flaky.mtp"
    assert urls["multi-gone-flaky"] == (
        "/storage/v1/object/public/potentials/multi-flaky-gone.mtp,"
        "/storage/v1/object/public/potentials/multi-flaky-503.mtp"
    )
    # Foreign-origin URL is never fetched and never blanked.
    assert urls["foreign"] == "https://example.com/some/pot.dat"
    # A multi-object row with any surviving object keeps its file_url.
    assert urls["multi-partial"] == (
        "/storage/v1/object/public/potentials/multi-gone0.mtp,"
        "/storage/v1/object/public/potentials/multi-ok1.mtp"
    )


@pytest.mark.asyncio
async def test_sweep_dry_run_never_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_supabase: dict,
) -> None:
    db_path = tmp_path / "sweep.db"
    upload_dir = tmp_path / "uploads"
    _seed_sweep_db(db_path, upload_dir)

    stub_supabase[_public_url("potentials/ok.mtp")] = 200
    stub_supabase[_public_url("potentials/gone.mtp")] = 404
    stub_supabase[_public_url("potentials/flaky.mtp")] = 503

    monkeypatch.setenv("NFM_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(upload_service, "_UPLOAD_DIR_OVERRIDE", upload_dir)

    exit_code = await sweep_mod.sweep(apply=False)

    assert exit_code == 1
    urls = _file_urls(db_path)
    assert all(url != "" for url in urls.values())
    assert urls["up-missing"] == "/uploads/missing.tersoff"


@pytest.mark.asyncio
async def test_sweep_all_ok_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_supabase: dict,
) -> None:
    db_path = tmp_path / "sweep.db"
    upload_dir = tmp_path / "uploads"
    _seed_sweep_db(db_path, upload_dir)
    _drop_rows(
        db_path,
        ["foreign", "up-missing", "sb-gone", "sb-flaky", "multi-all-gone", "multi-gone-flaky"],
    )

    stub_supabase[_public_url("potentials/ok.mtp")] = 200

    monkeypatch.setenv("NFM_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(upload_service, "_UPLOAD_DIR_OVERRIDE", upload_dir)

    assert await sweep_mod.sweep(apply=False) == 0
