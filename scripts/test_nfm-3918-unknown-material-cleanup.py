#!/usr/bin/env python3
"""Unit tests for the NFM-3918 verification wrapper.

These tests pin down the substitution logic that hides the gap between
psql's `-v name=value` (client-side substitution variable) and the SQL
file's `current_setting('name', true)` (server-side GUC read). Without
this, `--expected-unknown-count=0` on an empty staging DB silently does
nothing and the wrapper exits 3 with a confusing preflight FAILED.

Run with:

    python -m pytest scripts/test_nfm-3918-unknown-material-cleanup.py -v

The tests do not require a live PostgreSQL connection — they only
exercise `_rewrite_sql_with_literals` and the GUC substitution table.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER_PATH = REPO_ROOT / "scripts" / "nfm-3918-unknown-material-cleanup.py"


@pytest.fixture(scope="module")
def wrapper():
    """Import the wrapper module without executing its main().

    The wrapper is a CLI script with a `if __name__ == "__main__":` guard,
    so direct importlib loading is safe and clean.
    """
    spec = importlib.util.spec_from_file_location("nfm_3918_wrapper", WRAPPER_PATH)
    if spec is None or spec.loader is None:
        pytest.skip(f"Could not load wrapper from {WRAPPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rewrite_substitutes_typed_literal(wrapper):
    """`current_setting('expected_unknown_count', true)::int` becomes a literal."""
    sql = (
        "DO $$\n"
        "DECLARE expected_count INTEGER := "
        "coalesce(current_setting('expected_unknown_count', true)::int, 27);\n"
        "BEGIN\n"
        "  RAISE NOTICE 'expected=%', expected_count;\n"
        "END $$;"
    )
    rewritten = wrapper._rewrite_sql_with_literals(
        sql, {"expected_unknown_count": "0"}
    )
    # Cast preserved, value literal inlined
    assert "current_setting('expected_unknown_count'" not in rewritten
    assert "0::int" in rewritten


def test_rewrite_substitutes_boolean_dry_run(wrapper):
    sql = (
        "DO $$\n"
        "DECLARE is_dry BOOLEAN := current_setting('dry_run', true)::boolean;\n"
        "BEGIN\n"
        "  IF is_dry THEN RAISE NOTICE 'dry'; END IF;\n"
        "END $$;"
    )
    rewritten_dry = wrapper._rewrite_sql_with_literals(sql, {"dry_run": "1"})
    assert "current_setting('dry_run'" not in rewritten_dry
    assert "true::boolean" in rewritten_dry

    rewritten_apply = wrapper._rewrite_sql_with_literals(sql, {"dry_run": "0"})
    assert "false::boolean" in rewritten_apply


def test_rewrite_substitutes_text_path(wrapper):
    sql = (
        "DO $$\n"
        "DECLARE path TEXT := current_setting('require_backup_path', true);\n"
        "BEGIN\n"
        "  RAISE NOTICE '%', path;\n"
        "END $$;"
    )
    rewritten = wrapper._rewrite_sql_with_literals(
        sql, {"require_backup_path": "/var/backups/nfm-3918-pre.sql"}
    )
    assert "current_setting('require_backup_path'" not in rewritten
    assert "'/var/backups/nfm-3918-pre.sql'" in rewritten


def test_rewrite_escapes_single_quotes(wrapper):
    sql = "SELECT current_setting('require_backup_path', true);"
    rewritten = wrapper._rewrite_sql_with_literals(
        sql, {"require_backup_path": "O'Brien's path"}
    )
    # Single quotes must be doubled inside the literal string
    assert "'O''Brien''s path'" in rewritten


def test_rewrite_preserves_unrelated_current_setting_calls(wrapper):
    """`current_setting('server_version', true)` is NOT in our GUC table; leave alone."""
    sql = "SELECT current_setting('server_version', true);"
    with pytest.raises(SystemExit):
        # Default rewrite errors on unknown GUC names because that signals
        # a forgotten entry in _GUC_SUBSTITUTIONS. The wrapper refuses to
        # silently drop a parameter binding.
        wrapper._rewrite_sql_with_literals(sql, {"server_version": "150001"})


def test_rewrite_does_not_match_in_comments_only(wrapper):
    """Smoke test: a body with no current_setting calls round-trips unchanged
    apart from any other edits (none in this path)."""
    sql = "-- just a comment with the word dry_run in it\nSELECT 1;"
    rewritten = wrapper._rewrite_sql_with_literals(sql, {"dry_run": "1"})
    # The comment word survives; only `current_setting('dry_run', true)`
    # would be rewritten.
    assert "dry_run in it" in rewritten
    assert rewritten.endswith("SELECT 1;")


def test_rewrite_with_all_real_gucs(wrapper):
    """End-to-end shape against a representative Phase 0 DO block."""
    sql = """
DO $$
DECLARE
    unknown_count INTEGER;
    expected_count INTEGER := coalesce(current_setting('expected_unknown_count', true)::int, 27);
    backup_path TEXT := current_setting('require_backup_path', true);
    is_dry_run BOOLEAN := current_setting('dry_run', true)::boolean;
BEGIN
    RAISE NOTICE 'count=% path=% dry=%', expected_count, backup_path, is_dry_run;
END $$;
"""
    rewritten = wrapper._rewrite_sql_with_literals(
        sql,
        {
            "expected_unknown_count": "0",
            "dry_run": "1",
            "require_backup_path": "stub",
        },
    )
    assert "0::int" in rewritten
    assert "true::boolean" in rewritten
    assert "'stub'::text" in rewritten
    assert "current_setting(" not in rewritten
