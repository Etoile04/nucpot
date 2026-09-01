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


# ---------------------------------------------------------------------------
# NFM-3918 shard-classification regression tests
# ---------------------------------------------------------------------------
# Added after the first faithful staging dry-run (staging seeded from a prod
# dump of the 11 cleanup-relevant tables). On the real 28-row shard the SQL
# reported zero_downstream=2 / carrying_data=26, but the ticket body's
# 决策依据 defines zero-downstream as "无 measurement / alias / composition"
# — which measures 17 / 11. The predicate had been written against *dataset*
# existence, so 15 Unknown rows that own an EMPTY dataset were routed into the
# merge phase instead of the hard-delete phase.
#
# `datasets.material_id` is ON DELETE CASCADE, so hard-deleting a material
# whose only datasets are empty also removes those datasets — which is exactly
# what the ticket asks for ("硬删除 ... 含其空 dataset 行").


@pytest.fixture(scope="module")
def sql_text():
    sql_path = REPO_ROOT / "scripts" / "nfm-3918-unknown-material-cleanup.sql"
    return sql_path.read_text(encoding="utf-8")


def _strip_sql_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )


def test_zero_downstream_predicate_is_measurement_based(sql_text):
    """Zero-downstream must mean 'no measurements', not 'no datasets'.

    A material owning an empty dataset carries no data and must be hard
    deleted, not merged. Measured on the real shard: dataset-based predicate
    classifies 2 rows, measurement-based classifies 17 (the ticket's number).
    """
    body = _strip_sql_comments(sql_text)

    # The dataset-only downstream test must not appear as a classification
    # predicate anywhere.
    assert "NOT EXISTS (SELECT 1 FROM datasets d WHERE d.material_id = m.id)" not in body, (
        "zero-downstream is still keyed on dataset existence; an Unknown row "
        "with an empty dataset would be merged instead of hard-deleted"
    )

    # It must instead test for measurements reachable through datasets.
    assert body.count(
        "JOIN property_measurements pm ON pm.dataset_id = d.id"
    ) >= 2, "expected the measurement-based downstream predicate in phase 2 and phase 3"


def test_before_snapshot_emits_total_measurement_count(sql_text):
    """AC #3 compares totals, so BEFORE must publish the global total.

    Previously BEFORE emitted only the Unknown-scoped count while AFTER
    emitted the global total, so the 'no measurement loss' guard compared
    94 against 97 and could never fail.
    """
    assert "NFM-3918_BEFORE" in sql_text
    before_notice = sql_text.split("NFM-3918_BEFORE", 1)[1].split("END $$;", 1)[0]
    assert "measurements_total=%" in before_notice, (
        "BEFORE notice must emit measurements_total so the invariant compares "
        "like with like"
    )


def test_parse_counts_before_extracts_measurements_total(wrapper):
    line = (
        "NOTICE:  NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    snap = wrapper.parse_counts(line)
    assert snap.label == "BEFORE"
    assert snap.counts["measurements"] == 94
    assert snap.counts["measurements_total"] == 97
    assert snap.counts["zero_downstream"] == 17
    assert snap.counts["carrying_data"] == 11


def test_invariants_detect_measurement_loss(wrapper):
    """Regression: destroying every Unknown measurement must FAIL the check.

    With the old code BEFORE.measurements was Unknown-scoped (94) and
    AFTER.measurements was the global total (97), so `after < before` was
    false and the guard passed even on total data loss.
    """
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    # Every one of the 94 Unknown-linked measurements destroyed: 97 - 94 = 3.
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=3 (was 97) density_10_55=1 "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    failures = wrapper.assert_invariants(
        before, after, apply=True, expected_measurements=None
    )
    assert any("measurement" in f.lower() for f in failures), (
        f"measurement loss went undetected; failures={failures}"
    )


def test_invariants_pass_on_clean_merge(wrapper):
    """The happy path must stay green: no loss, Unknown gone, density collapsed."""
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=97 (was 97) density_10_55=1 "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    assert wrapper.assert_invariants(
        before, after, apply=True, expected_measurements=None
    ) == []


# ---------------------------------------------------------------------------
# NFM-3918 transactional-boundary regression tests
# ---------------------------------------------------------------------------
# Added after the first faithful staging apply surfaced a structural failure:
# PL/pgSQL has no implicit transaction across top-level DO blocks. The
# pre-fix migration committed 17 hard deletes in Phase 3 then crashed in
# Phase 4 on the `materials.source_id` typo, leaving the database at
# unknown=11 with no rollback path. Phase 4's compensating merges never
# ran, so the 94 surviving measurements stayed parked on a "Unknown
# Material" identity that no longer carried the dataset rows they
# referenced — i.e., partially-orphaned.
#
# The fix wraps both phases in a single `DO $outer$ ... END $outer$;`
# block. These tests pin the structure: one outer block, one BEGIN,
# one EXCEPTION handler, and crucially NO top-level Phase 3 or Phase 4
# DO blocks at file scope.


def test_phases_3_and_4_wrapped_in_single_transaction(sql_text):
    """Phase 3 + Phase 4 must live inside one DO $outer$ block.

    Regression for the partial-apply failure mode. Two independent
    top-level `DO $$` blocks for Phase 3 and Phase 4 would each commit
    on success, so a Phase 4 error would leave Phase 3's deletions
    durable without any compensating merge.
    """
    body = _strip_sql_comments(sql_text)

    # Exactly one outer wrapper.
    assert body.count("DO $outer$") == 1, (
        "expected exactly one DO $outer$ wrapper around Phases 3+4; "
        f"found {body.count('DO $outer$')}"
    )
    assert body.count("END $outer$;") == 1, (
        "expected exactly one END $outer$; — Phase 3+4 must close the "
        "transactional wrapper exactly once"
    )

    # The wrapper must contain an explicit BEGIN/EXCEPTION block. This is
    # what guarantees any Phase 4 error RAISEs inside the txn and rolls
    # Phase 3's deletes back. Without it, an uncaught error in Phase 4
    # would still leave Phase 3 committed (PL/pgSQL does NOT auto-rollback
    # across inner DO blocks).
    assert "BEGIN" in body, "Phase 3+4 wrapper must include a BEGIN block"
    assert "EXCEPTION WHEN OTHERS THEN" in body, (
        "Phase 3+4 wrapper must catch errors and re-RAISE inside the "
        "transaction so the whole apply rolls back together"
    )


def test_no_top_level_phase_3_or_phase_4_do_blocks(sql_text):
    """No bare `DO $$ ... END $$;` block can implement Phase 3 or Phase 4.

    The transactional wrapper is a single DO $outer$ ... END $outer$;.
    Any additional top-level DO $$ ... END $$; that contains the Phase 3
    DELETE or the Phase 4 FOR-rec-loop would re-introduce the partial-
    apply failure mode.
    """
    body = _strip_sql_comments(sql_text)
    # Top-level (non-outer) DO blocks for the mutation phases are
    # forbidden. Phase 0, 1, 2, 5 are non-mutating and use plain DO $$.
    # Phase 3 and Phase 4 must live ONLY inside $outer$.
    #
    # We detect this by asserting that the Phase 3 "zero_downstream"
    # DELETE-with-CTE and the Phase 4 "carrying" FOR-rec-loop each appear
    # in the file exactly once AND each appears after the DO $outer$
    # marker.
    outer_idx = body.index("DO $outer$")
    phase3_cte = "WITH zero_downstream AS ("
    phase4_loop = "FOR rec IN"
    assert body.count(phase3_cte) == 1, (
        f"Phase 3 zero_downstream CTE must appear exactly once; "
        f"found {body.count(phase3_cte)}"
    )
    assert body.count(phase4_loop) == 1, (
        f"Phase 4 FOR rec IN must appear exactly once; "
        f"found {body.count(phase4_loop)}"
    )
    assert body.index(phase3_cte) > outer_idx, (
        "Phase 3 CTE must live INSIDE the $outer$ transactional wrapper"
    )
    assert body.index(phase4_loop) > outer_idx, (
        "Phase 4 FOR loop must live INSIDE the $outer$ transactional wrapper"
    )


def test_phase_5_runs_after_commit(sql_text):
    """Phase 5's AFTER snapshot must read post-commit state.

    The migration file must close $outer$ (commit) before Phase 5's DO
    block opens. If Phase 5 were inside the same wrapper, an error in
    Phase 5 would roll back Phase 3+4's successful apply.
    """
    body = _strip_sql_comments(sql_text)
    end_outer_idx = body.index("END $outer$;")
    after_marker = body.index("NFM-3918_AFTER")
    assert after_marker > end_outer_idx, (
        "Phase 5 (which emits NFM-3918_AFTER) must run AFTER the "
        "transactional wrapper closes, so its snapshot reads committed state"
    )
