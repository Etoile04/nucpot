"""TDD tests for the V1<->V2 parity harness (NFM-3539).

Coverage scope (per AC #7: "Tests added under the parity harness
directory; coverage of the comparison logic"):

* Classification rules: ratio-based vs equality-based.
* Snapshot comparison surface coverage (7 surfaces per fixture).
* ``ParityReport.is_blocked`` correctly gates on a single blocking row.
* Markdown report renders the expected sections and surfaces.
* All 3 fixtures (short / long / multi-doc) drive end-to-end through
  ``build_report`` without crashing.

The actual V1 / V2 path execution is exercised by
``tools/parity_v1v2/run_harness.py`` against the in-code fixtures;
the unit tests below focus on the *comparison* and *classification*
logic that the runnable harness depends on.
"""

from __future__ import annotations

from tests.parity_v1v2.fixtures import ALL_FIXTURES, SHORT, LONG, MULTI_DOC
from tests.parity_v1v2.harness import (
    ALL_CLASSES,
    CLASS_BLOCKING,
    CLASS_COSMETIC,
    CLASS_NON_COSMETIC,
    DBSnapshot,
    DivergenceRow,
    ParityReport,
    _equal_classification,
    _ratio_classification,
    build_report,
    compare_snapshots,
    render_markdown,
)


def _snapshot(
    path: str,
    fixture_name: str,
    *,
    result_count: int = 3,
    chunk_count: int = 1,
    status: str = "completed",
    comments: int = 0,
    retries: int = 0,
    extracted: int = 3,
    staged: int = 2,
) -> DBSnapshot:
    """Convenience constructor for tests."""
    return DBSnapshot(
        path=path,
        fixture_name=fixture_name,
        extraction_result_count=result_count,
        extraction_chunk_count=chunk_count,
        extraction_job_status=status,
        comment_count=comments,
        retry_count=retries,
        extracted_count=extracted,
        staged_count=staged,
    )


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------


def test_ratio_classification_both_zero_is_cosmetic() -> None:
    cls, rat = _ratio_classification(0, 0)
    assert cls == CLASS_COSMETIC
    assert "zero" in rat.lower()


def test_ratio_classification_one_zero_is_non_cosmetic() -> None:
    cls, rat = _ratio_classification(0, 5)
    assert cls == CLASS_NON_COSMETIC
    assert "dropping" in rat.lower() or "only one" in rat.lower()


def test_ratio_classification_both_nonzero_is_cosmetic() -> None:
    cls, rat = _ratio_classification(3, 17)
    assert cls == CLASS_COSMETIC
    assert "ratio" in rat.lower() or "expected" in rat.lower()


def test_equal_classification_match_is_cosmetic() -> None:
    cls, _ = _equal_classification("completed", "completed", "extraction_job_status")
    assert cls == CLASS_COSMETIC


def test_equal_classification_status_mismatch_is_blocking() -> None:
    cls, rat = _equal_classification(
        "completed", "failed", "extraction_job_status"
    )
    assert cls == CLASS_BLOCKING
    assert "gate the flip" in rat.lower()


def test_equal_classification_retry_mismatch_is_non_cosmetic() -> None:
    cls, rat = _equal_classification(0, 3, "retry_count")
    assert cls == CLASS_NON_COSMETIC
    assert "retry" in rat.lower()


# ---------------------------------------------------------------------------
# Snapshot comparison
# ---------------------------------------------------------------------------


def test_compare_snapshots_emits_seven_surfaces() -> None:
    v1 = _snapshot("v1", "short")
    v2 = _snapshot("v2", "short", result_count=4, chunk_count=2)
    rows = compare_snapshots(v1, v2)
    surfaces = [r.surface for r in rows]
    assert surfaces == [
        "extraction_result_count",
        "extraction_chunk_count",
        "extraction_job_status",
        "comment_count",
        "retry_count",
        "extracted_count",
        "staged_count",
    ]


def test_compare_snapshots_status_blocking_propagates_to_is_blocked() -> None:
    v1 = _snapshot("v1", "short", status="completed")
    v2 = _snapshot("v2", "short", status="failed")
    report = build_report([(v1, v2)])
    assert report.is_blocked is True


def test_compare_snapshots_cosmetic_only_is_not_blocked() -> None:
    v1 = _snapshot("v1", "short", result_count=3)
    v2 = _snapshot("v2", "short", result_count=5, chunk_count=2)
    report = build_report([(v1, v2)])
    assert report.is_blocked is False


# ---------------------------------------------------------------------------
# ParityReport properties
# ---------------------------------------------------------------------------


def test_classification_counts_sums_across_rows() -> None:
    v1 = _snapshot("v1", "short")
    v2 = _snapshot("v2", "short", result_count=5, chunk_count=2, retries=2)
    report = build_report([(v1, v2)])
    counts = report.classification_counts
    assert counts[CLASS_BLOCKING] == 0
    # 6 cosmetic surfaces (status matches, comment=0=0) + retry non-cosmetic
    assert counts[CLASS_NON_COSMETIC] >= 1
    assert sum(counts.values()) == 7


def test_fixtures_run_lists_all_supplied_pairs() -> None:
    pairs = [
        (_snapshot("v1", "short"), _snapshot("v2", "short")),
        (_snapshot("v1", "long"), _snapshot("v2", "long")),
        (_snapshot("v1", "multi-doc"), _snapshot("v2", "multi-doc")),
    ]
    report = build_report(pairs)
    assert sorted(report.fixtures_run) == ["long", "multi-doc", "short"]


def test_build_report_asserts_fixture_name_match() -> None:
    import pytest

    pairs = [
        (_snapshot("v1", "short"), _snapshot("v2", "long")),
    ]
    with pytest.raises(AssertionError, match="fixture name mismatch"):
        build_report(pairs)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_markdown_includes_verdict_and_sections() -> None:
    v1 = _snapshot("v1", "short")
    v2 = _snapshot("v2", "short", result_count=5, chunk_count=2)
    report = build_report([(v1, v2)])
    md = render_markdown(report)
    assert "# V1<->V2 Parity Report (NFM-3539)" in md
    assert "**Verdict:** READY to flip" in md
    assert "## Classification summary" in md
    assert "## Divergences" in md
    assert "## Appendix: Per-fixture snapshots" in md
    assert "`extraction_result_count`" in md
    assert "`extraction_chunk_count`" in md
    assert "`extraction_job_status`" in md
    assert "`comment_count`" in md
    assert "`retry_count`" in md
    assert "`extracted_count`" in md
    assert "`staged_count`" in md


def test_render_markdown_blocked_verdict_when_blocking_row() -> None:
    v1 = _snapshot("v1", "short", status="completed")
    v2 = _snapshot("v2", "short", status="failed")
    report = build_report([(v1, v2)])
    md = render_markdown(report)
    assert "BLOCKED" in md


def test_render_markdown_groups_rows_per_fixture() -> None:
    pairs = [
        (_snapshot("v1", "short"), _snapshot("v2", "short", result_count=5)),
        (_snapshot("v1", "long"), _snapshot("v2", "long", result_count=8)),
    ]
    report = build_report(pairs)
    md = render_markdown(report)
    assert "Fixture: `short`" in md
    assert "Fixture: `long`" in md


# ---------------------------------------------------------------------------
# End-to-end smoke (uses all 3 in-code fixtures)
# ---------------------------------------------------------------------------


def test_three_fixtures_build_report_without_crashing() -> None:
    """Smoke test: every fixture pair produces a non-empty report."""
    pairs = []
    for fix in ALL_FIXTURES:
        v1 = _snapshot("v1", fix.name)
        v2 = _snapshot("v2", fix.name, result_count=7, chunk_count=3)
        pairs.append((v1, v2))
    report = build_report(pairs)
    assert len(report.snapshots) == 2 * len(ALL_FIXTURES)
    assert len(report.divergences) == 7 * len(ALL_FIXTURES)
    assert all(f.name in (s.name for s in ALL_FIXTURES) for f in ALL_FIXTURES)


def test_three_fixtures_render_full_markdown_report() -> None:
    pairs = []
    for fix in ALL_FIXTURES:
        v1 = _snapshot("v1", fix.name)
        v2 = _snapshot("v2", fix.name, result_count=7, chunk_count=3)
        pairs.append((v1, v2))
    report = build_report(pairs)
    md = render_markdown(report)
    assert "Fixture: `short`" in md
    assert "Fixture: `long`" in md
    assert "Fixture: `multi-doc`" in md


# ---------------------------------------------------------------------------
# Sanity on the in-code fixtures themselves
# ---------------------------------------------------------------------------


def test_three_distinct_kinds_present() -> None:
    kinds = sorted(s.kind for s in ALL_FIXTURES)
    assert kinds == ["long", "multi-doc", "short"]


def test_short_fixture_is_actually_short() -> None:
    # Smoke guard so a future expansion of SHORT_TEXT does not silently
    # violate the AC #3 "short" diversity criterion.
    assert len(SHORT.text) < 500


def test_long_fixture_is_actually_long() -> None:
    assert len(LONG.text) > 1000


def test_multi_doc_fixture_has_five_documents() -> None:
    # The 5-document concatenation uses "---" as separator; assert the
    # count so the AC #3 "multi-document" diversity criterion holds.
    assert MULTI_DOC.text.count("# Doc ") == 5