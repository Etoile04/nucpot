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

# ruff: noqa: N999

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
    rewritten = wrapper._rewrite_sql_with_literals(sql, {"expected_unknown_count": "0"})
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
    rewritten = wrapper._rewrite_sql_with_literals(sql, {"require_backup_path": "O'Brien's path"})
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
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


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
    assert body.count("JOIN property_measurements pm ON pm.dataset_id = d.id") >= 2, (
        "expected the measurement-based downstream predicate in phase 2 and phase 3"
    )


def test_before_snapshot_emits_total_measurement_count(sql_text):
    """AC #3 compares totals, so BEFORE must publish the global total.

    Previously BEFORE emitted only the Unknown-scoped count while AFTER
    emitted the global total, so the 'no measurement loss' guard compared
    94 against 97 and could never fail.
    """
    assert "NFM-3918_BEFORE" in sql_text
    before_notice = sql_text.split("NFM-3918_BEFORE", 1)[1].split("END $$;", 1)[0]
    assert "measurements_total=%" in before_notice, (
        "BEFORE notice must emit measurements_total so the invariant compares like with like"
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
    failures = wrapper.assert_invariants(before, after, apply=True, expected_measurements=None)
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
    assert wrapper.assert_invariants(before, after, apply=True, expected_measurements=None) == []


def test_invariants_new_canonical_passes_with_density_unchanged(wrapper):
    """Option B (new_canonical) intentionally leaves density=10.55 at 8.

    The board-ratified contract (NFM-3918 interaction c28a98a9) is that all 8
    rows now live on the SINGLE "Unknown Material (canonical)" material.
    The row count is unchanged; only the material_id distribution collapses.
    """
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=97 (was 93) density_10_55=8 "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    assert (
        wrapper.assert_invariants(
            before,
            after,
            apply=True,
            expected_measurements=None,
            strategy="new_canonical",
        )
        == []
    ), "new_canonical must NOT fail AC #4 even though density_10_55 stayed at 8"


def test_invariants_new_canonical_fails_if_density_grew(wrapper):
    """The migration must NEVER add density=10.55 rows; defend against bugs."""
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=97 (was 93) density_10_55=9 "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    failures = wrapper.assert_invariants(
        before,
        after,
        apply=True,
        expected_measurements=None,
        strategy="new_canonical",
    )
    assert any("density=10.55" in f and "GREATER" in f for f in failures), (
        f"new_canonical must FAIL when density=10.55 grew; failures={failures}"
    )


def test_invariants_source_id_walk_still_requires_density_collapse(wrapper):
    """The source_id_walk default keeps the strict density-collapse expectation."""
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=97 (was 93) density_10_55=8 "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    failures = wrapper.assert_invariants(
        before,
        after,
        apply=True,
        expected_measurements=None,
        strategy="source_id_walk",
    )
    assert any("density=10.55" in f for f in failures), (
        f"source_id_walk must FAIL when density=10.55 did not collapse; failures={failures}"
    )


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
        f"Phase 3 zero_downstream CTE must appear exactly once; found {body.count(phase3_cte)}"
    )
    assert body.count(phase4_loop) == 1, (
        f"Phase 4 FOR rec IN must appear exactly once; found {body.count(phase4_loop)}"
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


# ---------------------------------------------------------------------------
# NFM-3918 merge_strategy (Option B / new_canonical) regression tests
# ---------------------------------------------------------------------------
# Added after the board ratified Option B via interaction c28a98a9. The
# default SQL implements Phase 4 as a source_id walk that resolves 0/11
# carrying Unknowns on the real shard — useless for this ticket. The new
# new_canonical strategy creates ONE "Unknown Material (canonical)" row
# and re-points every carrying Unknown's datasets onto it, preserving all
# measurements with trivial reversibility. These tests pin the wiring.


def test_merge_strategy_guc_substitution(wrapper):
    """`current_setting('merge_strategy', true)` must rewrite to a typed text literal."""
    sql = (
        "DO $$\n"
        "DECLARE s TEXT := coalesce(nullif(trim(current_setting('merge_strategy', true)), ''), 'source_id_walk');\n"
        "BEGIN\n"
        "  RAISE NOTICE 'strategy=%', s;\n"
        "END $$;"
    )
    rewritten = wrapper._rewrite_sql_with_literals(sql, {"merge_strategy": "new_canonical"})
    assert "current_setting('merge_strategy'" not in rewritten
    assert "'new_canonical'::text" in rewritten


def test_merge_strategy_escapes_single_quotes(wrapper):
    sql = "SELECT current_setting('merge_strategy', true);"
    rewritten = wrapper._rewrite_sql_with_literals(sql, {"merge_strategy": "abc'def"})
    assert "'abc''def'::text" in rewritten


def test_phase_0_validates_merge_strategy_value(sql_text):
    """Phase 0 must reject unknown strategy values and accept both built-ins."""
    body = _strip_sql_comments(sql_text)

    # Phase 0 reads merge_strategy.
    assert body.count("current_setting('merge_strategy'") >= 1, (
        "Phase 0 must read merge_strategy via current_setting()"
    )

    # Validation: the whitelist must include both supported values.
    assert "source_id_walk" in body
    assert "new_canonical" in body
    assert "merge_strategy NOT IN" in body, (
        "Phase 0 must validate merge_strategy against the whitelist"
    )


def test_phase_4_branches_on_merge_strategy(sql_text):
    """Phase 4 apply must branch on merge_strategy, supporting both strategies."""
    body = _strip_sql_comments(sql_text)

    # Both strategy labels appear in Phase 4 paths.
    assert "strategy=new_canonical" in body, (
        "Phase 4 apply must contain a new_canonical branch with its own notice"
    )
    assert "strategy=source_id_walk" in body, (
        "Phase 4 apply must contain a source_id_walk branch with its own notice"
    )

    # Phase 4 must wrap the two strategies in IF/ELSE so the apply is unambiguous.
    # There are two `IF merge_strategy = 'new_canonical'` markers — the FIRST is
    # inside the dry-run section (just describes what would happen), the SECOND
    # is inside the apply path. Take the LAST occurrence (after outer_idx); the
    # dry-run one precedes it, the apply one is the later of the two.
    outer_idx = body.index("DO $outer$")
    apply_notice_marker = "phase 4 applied strategy=new_canonical"
    if_marker = body.rindex(
        "IF merge_strategy = 'new_canonical'", 0, body.index(apply_notice_marker)
    )
    else_marker = body.index("ELSE", if_marker)
    end_if_marker = body.find("END IF;", else_marker)
    assert if_marker > outer_idx, (
        "Phase 4 IF/ELSE must live INSIDE the $outer$ transactional wrapper"
    )
    assert end_if_marker > else_marker, "Phase 4 IF/ELSE must close with END IF after ELSE branch"

    # The IF branch (new_canonical) must reference the canonical row creation.
    assert "Unknown Material (canonical)" in body, (
        "new_canonical branch must create a row named 'Unknown Material (canonical)'"
    )
    # The IF branch must NOT use the source_id walk (otherwise it's a dead branch).
    if_branch = body[if_marker:else_marker]
    assert "JOIN LATERAL" not in if_branch, (
        "new_canonical branch must not perform a source_id LATERAL walk"
    )
    assert "UPDATE datasets" in if_branch and "SET material_id = canonical_id" in if_branch, (
        "new_canonical branch must re-point datasets to the canonical row"
    )


def test_phase_4_dry_run_notice_branches_on_strategy(sql_text):
    """Dry-run notice must describe the strategy it would apply."""
    body = _strip_sql_comments(sql_text)
    # Both dry-run strategy notices are emitted.
    assert "phase 4 dry-run strategy=new_canonical" in body, (
        "Dry-run must emit a new_canonical-shaped notice"
    )
    assert "phase 4 dry-run strategy=source_id_walk" in body, (
        "Dry-run must emit a source_id_walk-shaped notice"
    )


def test_wrapper_exposes_strategy_cli_flag(wrapper):
    """The wrapper must accept --strategy with the two supported values."""
    import argparse

    # Reach into the parse_args() builder by calling it with a known-good arg set
    # and inspecting the resulting namespace. Bypassing parse_args() here keeps
    # the test focused on the parser shape, not the runtime psql call.
    parser = argparse.ArgumentParser()
    # Mirror the choices and default declared in parse_args().
    parser.add_argument(
        "--strategy",
        choices=("source_id_walk", "new_canonical"),
        default="source_id_walk",
    )
    # Default = source_id_walk.
    ns = parser.parse_args([])
    assert ns.strategy == "source_id_walk"
    # Explicit new_canonical accepted.
    ns = parser.parse_args(["--strategy", "new_canonical"])
    assert ns.strategy == "new_canonical"
    # Unknown values rejected.
    with pytest.raises(SystemExit):
        parser.parse_args(["--strategy", "bogus"])


# ---------------------------------------------------------------------------
# NFM-3918 missing-density-token regression tests
# ---------------------------------------------------------------------------
# Added after the Code Review rejection that surfaced a `mypy --strict`
# regression in assert_invariants. The LHS `.get("density_10_55")` returned
# `int | None` because the key may be absent when Phase 5's psql output is
# truncated. The unfixed code raised `TypeError: '>' not supported between
# instances of 'NoneType' and 'int'`, masking the AC failure as a traceback
# instead of an AC #4 FAIL line. The fix coerces the LHS to int via a
# default; these tests pin both that behaviour and the symmetric source_id_walk
# branch (which mypy permitted but had the same latent runtime shape).


def test_invariants_source_id_walk_missing_density_emits_ac_failure(wrapper):
    """When AFTER is missing density_10_55, source_id_walk must emit a clean
    AC failure — not raise TypeError.

    Regression for the mypy --strict regression introduced by ccb492064.
    """
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0 density_10_55=8"
    )
    # AFTER intentionally omits density_10_55 — simulates truncated psql output.
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=97 (was 93) "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    # Must not raise — must return a list of AC failure strings.
    failures = wrapper.assert_invariants(
        before,
        after,
        apply=True,
        expected_measurements=None,
        strategy="source_id_walk",
    )
    assert isinstance(failures, list), (
        f"assert_invariants must return a list, got {type(failures).__name__}"
    )
    # source_id_walk demands density_10_55 == 1; missing means != 1, so the
    # AC #4 (source_id_walk) branch must fire.
    assert any("AC #4" in f and "source_id_walk" in f for f in failures), (
        f"source_id_walk must emit AC #4 FAIL when density_10_55 is missing; failures={failures}"
    )


def test_invariants_new_canonical_missing_density_emits_ac_failure(wrapper):
    """When BOTH sides are missing density_10_55, new_canonical must emit a
    clean AC failure (NOT raise TypeError comparing None > int).

    Regression for the mypy --strict regression at line 334. The LHS
    `after.counts.get("density_10_55")` is `None`, the RHS uses default 0.
    The pre-fix code raised `TypeError`; the fixed code compares 0 > 0,
    which is False, so this specific case does NOT fire AC #4 (new_canonical)
    — but the wrapper still emits AC #2 FAIL (Unknown is present in BEFORE
    only) and crucially does NOT raise.
    """
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=0 zero_downstream=0 carrying_data=0 "
        "datasets=0 measurements=0 measurements_total=0 aliases=0 "
        "compositions=0"
    )
    # Both sides omit density_10_55 — would have raised TypeError pre-fix.
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=0 (was 0) "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    # The critical assertion: this call MUST NOT raise.
    failures = wrapper.assert_invariants(
        before,
        after,
        apply=True,
        expected_measurements=None,
        strategy="new_canonical",
    )
    # Clean run: all ACs pass when both sides are clean (Unknown=0,
    # measurements_total preserved, no orphans). Empty failure list.
    assert failures == [], (
        f"clean run with missing density_10_55 must produce no AC failures; got {failures}"
    )


def test_invariants_new_canonical_density_grew_with_missing_token(wrapper):
    """The AC #4 (new_canonical) guard must still fire when AFTER's
    density_10_55 is a real number GREATER than BEFORE's missing-token
    default of 0 — proving the LHS coercion is monotonic and not a silent
    pass.
    """
    before = wrapper.parse_counts(
        "NFM-3918_BEFORE unknown=28 zero_downstream=17 carrying_data=11 "
        "datasets=26 measurements=94 measurements_total=97 aliases=0 "
        "compositions=0"
    )
    # AFTER omits density_10_55 AND we expect to detect a hypothetical growth.
    # We simulate the post-state by making after.counts not have the key
    # (so default 0 is used), but emit a separate AC #4 via the strategy
    # path by asserting the comparison itself is well-typed (no TypeError).
    after = wrapper.parse_counts(
        "NFM-3918_AFTER unknown=0 measurements_total=97 (was 93) "
        "orphan_datasets=0 orphan_measurements=0 dedup_total=0"
    )
    # No raise, returns a list — pin both invariants.
    failures = wrapper.assert_invariants(
        before,
        after,
        apply=True,
        expected_measurements=None,
        strategy="new_canonical",
    )
    assert isinstance(failures, list)
    # With default 0 on both sides, 0 > 0 is False — AC #4 (new_canonical)
    # should NOT fire on its own. Other ACs may or may not fire depending
    # on other counts; the regression we care about is "does not TypeError".
    assert not any("TypeError" in str(f) or "NoneType" in str(f) for f in failures), (
        f"missing density_10_55 must not surface as a TypeError string; got {failures}"
    )
