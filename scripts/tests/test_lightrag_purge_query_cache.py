"""Tests for scripts/lightrag_purge_query_cache.py (NFM-4804 item 2).

The script purges stale prompt-keyed LLM ANSWER replays
(cache_type='query') from the LightRAG PG cache table. These tests pin
the scope discipline with a fake asyncpg module:

  - only cache_type='query' rows are ever deleted (extract rows survive)
  - default run is a read-only report (no DELETE without --yes)
  - --older-than binds an hours parameter; plain run deletes all query rows
  - 0-row state reports the JsonKV-pre-cutover hint instead of failing
  - driver missing → exit 2; DB failure → exit 1
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import lightrag_purge_query_cache as mod  # noqa: E402


class _FakeResult(dict):
    """Row-shaped mapping (asyncpg.Record-ish)."""


class _FakeConn:
    def __init__(self, count_row=None, fail_on=None):
        self.count_row = count_row or _FakeResult(n=3, oldest=None, newest=None)
        self.fail_on = fail_on
        self.executed: list[tuple] = []
        self.closed = False

    async def fetchrow(self, sql, *params):
        if self.fail_on == "fetchrow":
            raise RuntimeError("connection refused")
        return self.count_row

    async def execute(self, sql, *params):
        if self.fail_on == "execute":
            raise RuntimeError("permission denied")
        self.executed.append((sql, params))
        return "DELETE 3"

    async def close(self):
        self.closed = True


class _FakeAsyncpg(types.ModuleType):
    def __init__(self, conn):
        super().__init__("asyncpg")
        self._conn = conn

    async def connect(self, dsn, timeout=None):
        return self._conn


@pytest.fixture()
def fake_asyncpg(monkeypatch):
    def _install(conn):
        monkeypatch.setitem(sys.modules, "asyncpg", _FakeAsyncpg(conn))
        return conn

    return _install


def test_dry_run_reports_without_deleting(fake_asyncpg, capsys) -> None:
    conn = fake_asyncpg(_FakeConn())
    rc = mod.main(["--dsn", "postgresql://nfm@127.0.0.1:5433/nfm_db"])
    assert rc == 0
    assert conn.executed == [], "no DELETE without --yes"
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "3 row(s)" in out


def test_yes_purges_query_rows_only(fake_asyncpg, capsys) -> None:
    conn = fake_asyncpg(_FakeConn())
    rc = mod.main(["--dsn", "postgresql://x", "--yes"])
    assert rc == 0
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "DELETE FROM lightrag_llm_cache" in sql
    assert "cache_type = $1" in sql, "discriminator must be parameterised"
    assert params == ("query",), "must bind cache_type='query' (never 'extract')"
    assert "extract" not in sql
    assert conn.closed


def test_older_than_binds_hours_param(fake_asyncpg) -> None:
    conn = fake_asyncpg(_FakeConn())
    rc = mod.main(["--dsn", "postgresql://x", "--yes", "--older-than", "24"])
    assert rc == 0
    sql, params = conn.executed[0]
    assert "create_time < now() - make_interval(hours => $2)" in sql
    assert params == ("query", 24.0)


def test_zero_rows_reports_jsonkv_hint(fake_asyncpg, capsys) -> None:
    fake_asyncpg(_FakeConn(count_row=_FakeResult(n=0, oldest=None, newest=None)))
    rc = mod.main(["--dsn", "postgresql://x", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "JsonKVStorage" in out
    assert "cutover" in out


def test_negative_older_than_is_usage_error() -> None:
    rc = mod.main(["--dsn", "postgresql://x", "--yes", "--older-than", "-1"])
    assert rc == 2


def test_db_failure_exits_1(fake_asyncpg) -> None:
    fake_asyncpg(_FakeConn(fail_on="fetchrow"))
    rc = mod.main(["--dsn", "postgresql://x"])
    assert rc == 1


def test_missing_driver_exits_2(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "asyncpg", None)  # import → ImportError
    rc = mod.main(["--dsn", "postgresql://x"])
    assert rc == 2
