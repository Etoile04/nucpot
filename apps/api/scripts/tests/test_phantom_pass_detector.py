"""Tests for apps/api/scripts/phantom_pass_detector.py (NFM-3831 / ADR-010).

Phase 1 of the phantom-pass audit rule (sibling of phantom-done from
NFM-3166 / NFM-3024). Covers the two detectors:

* **D1 — Numeric-AC itemized-table check.** Closed issues whose close
  comment contains a numeric AC pattern (e.g. ``AC-2: 6/8 PASS``) MUST
  attach an itemized scoring-card table whose row count is at least the
  number of distinct AC patterns. Missing table → ``[PHANTOM-PASS]`` +
  reopen. Verified on the canonical example NFM-3824 (NFM-3424 AC-2
  claimed PASS without the table that NFM-3396 had previously produced).

* **D2 — Verifier cross-check.** Close comments that claim
  ``verified by <agent>`` or ``verified via NFM-XXXX`` MUST be backed by
  a comment from the named agent (or in the referenced verifier
  issue). Missing verifier comment → ``[PHANTOM-VERIFICATION]`` +
  reopen. Verified on NFM-3424 AC-2 claim "verified by CTO via
  NFM-3754" — NFM-3754 had only a stale-run cleanup comment from
  NDE, no CTO verification comment.

Idempotency: issues that already carry a ``[PHANTOM-PASS]`` /
``[PHANTOM-VERIFICATION]`` marker, or that were reopened after a prior
flag, are skipped on subsequent runs. The 7-day lookback window is the
only eligible cohort.

These tests cover the *library surface* of the detector; integration
with the production Paperclip DB / API lives in the cron driver and is
out of scope for unit tests.
"""

from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

# Make apps/api/scripts/ importable as a module so we can unit-test the
# library surface without booting the API runtime.
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import phantom_pass_detector as ppd  # noqa: E402


# ---------------------------------------------------------------------------
# D1 — extract_numeric_ac_patterns
# ---------------------------------------------------------------------------


class TestExtractNumericAcPatterns:
    """AC patterns: ``AC-N: X/Y`` or ``AC-N ≥X/Y`` or ``AC-N: ≥X/Y``."""

    def test_simple_ratio(self):
        body = "AC-2: 6/8 PASS"
        assert ppd.extract_numeric_ac_patterns(body) == ["AC-2:6/8"]

    def test_gte_ratio(self):
        body = "AC-3: ≥6/8 PASS verified"
        assert ppd.extract_numeric_ac_patterns(body) == ["AC-3:≥6/8"]

    def test_full_width_colon(self):
        body = "AC-1：8/8 PASS"  # full-width colon
        assert ppd.extract_numeric_ac_patterns(body) == ["AC-1:8/8"]

    def test_multiple_acs(self):
        body = "AC-1: 5/5 PASS\nAC-2: 6/8 PASS\nAC-3: 8/8 PASS"
        assert ppd.extract_numeric_ac_patterns(body) == [
            "AC-1:5/5",
            "AC-2:6/8",
            "AC-3:8/8",
        ]

    def test_no_ac_returns_empty(self):
        body = "All tasks complete. Tests pass."
        assert ppd.extract_numeric_ac_patterns(body) == []

    def test_ac_pass_no_number_excluded(self):
        # "AC-2 PASS" without a numeric N/M is not flagged — D1 only triggers
        # when there is a quantitative claim that demands evidence.
        body = "AC-2 PASS — verified by reviewer"
        assert ppd.extract_numeric_ac_patterns(body) == []

    def test_duplicate_acs_deduped(self):
        body = "AC-2: 6/8 PASS. We confirmed AC-2: 6/8 once more."
        # Distinct ratio claim per occurrence is fine; dedup by canonical form.
        assert len(ppd.extract_numeric_ac_patterns(body)) >= 1

    def test_case_insensitive(self):
        body = "ac-2: 6/8 pass"
        assert ppd.extract_numeric_ac_patterns(body) == ["AC-2:6/8"]


# ---------------------------------------------------------------------------
# D1 — has_itemized_table
# ---------------------------------------------------------------------------


class TestHasItemizedTable:
    """Markdown pipe-table with ``>= min_rows`` body rows + header."""

    def test_three_row_table(self):
        body = (
            "| # | Checkpoint | Expected | Actual | Verdict |\n"
            "|---|-----------|----------|--------|--------|\n"
            "| 1 | Ea | 0.30 | 0.31 | PASS |\n"
            "| 2 | D0 | 3.32e-8 | 3.30e-8 | PASS |\n"
            "| 3 | Density | 10.55 | — | FAIL |\n"
        )
        assert ppd.has_itemized_table(body, min_rows=3) is True

    def test_two_row_table_below_threshold(self):
        body = (
            "| # | Checkpoint | Verdict |\n"
            "|---|-----------|--------|\n"
            "| 1 | A | PASS |\n"
            "| 2 | B | FAIL |\n"
        )
        assert ppd.has_itemized_table(body, min_rows=3) is False

    def test_no_table(self):
        body = "AC-2: 6/8 PASS verified"
        assert ppd.has_itemized_table(body, min_rows=3) is False

    def test_table_only_header_no_body(self):
        body = (
            "| # | Checkpoint | Verdict |\n"
            "|---|-----------|--------|\n"
        )
        assert ppd.has_itemized_table(body, min_rows=1) is False

    def test_six_row_table_for_eight_ac_passes(self):
        # NFM-3824 / NFM-3396 pattern — 8-row table for 8 checkpoints
        rows = [
            "| # | Checkpoint | Expected | DB Evidence | Verdict |",
            "|---|-----------|----------|-------------|---------|",
        ]
        for i in range(1, 9):
            rows.append(f"| {i} | c{i} | val | match | PASS |")
        body = "\n".join(rows)
        assert ppd.has_itemized_table(body, min_rows=8) is True

    def test_table_inside_code_block_does_not_count(self):
        # Tables in fenced code blocks are documentation, not scoring cards.
        body = (
            "```\n"
            "| # | A | B |\n"
            "|---|---|---|\n"
            "| 1 | x | y |\n"
            "| 2 | x | y |\n"
            "| 3 | x | y |\n"
            "```\n"
        )
        assert ppd.has_itemized_table(body, min_rows=3) is False


# ---------------------------------------------------------------------------
# D1 — check_d1
# ---------------------------------------------------------------------------


class TestCheckD1:
    """``check_d1`` returns a ``PhantomPassFinding`` when AC patterns
    appear without a sufficiently large itemized table, otherwise None."""

    def test_missing_table_flags_phantom_pass(self):
        body = "AC-2: 6/8 PASS verified"
        finding = ppd.check_d1(body)
        assert finding is not None
        assert finding.marker == "[PHANTOM-PASS]"
        assert "6/8" in finding.reason

    def test_present_table_passes(self):
        body = (
            "AC-2: 6/8 PASS\n\n"
            "| # | Checkpoint | Verdict |\n"
            "|---|-----------|--------|\n"
            "| 1 | a | PASS |\n"
            "| 2 | b | PASS |\n"
            "| 3 | c | PASS |\n"
            "| 4 | d | PASS |\n"
            "| 5 | e | PASS |\n"
            "| 6 | f | PASS |\n"
            "| 7 | g | FAIL |\n"
            "| 8 | h | FAIL |\n"
        )
        assert ppd.check_d1(body) is None

    def test_no_numeric_ac_returns_none(self):
        body = "Done. All work complete."
        assert ppd.check_d1(body) is None

    def test_table_too_small_for_ac_count_flags_phantom_pass(self):
        # 1 numeric AC pattern, 2-row table → insufficient
        body = (
            "AC-1: 5/5 PASS\n\n"
            "| # | A | B |\n"
            "|---|---|---|\n"
            "| 1 | x | y |\n"
            "| 2 | x | y |\n"
        )
        finding = ppd.check_d1(body)
        assert finding is not None
        assert finding.marker == "[PHANTOM-PASS]"


# ---------------------------------------------------------------------------
# D2 — extract_verifier_refs
# ---------------------------------------------------------------------------


class TestExtractVerifierRefs:
    """Verifier refs: ``verified by <agent>`` or ``verified via NFM-XXXX``."""

    def test_verified_by_agent_name(self):
        body = "AC-2 verified by CTO"
        refs = ppd.extract_verifier_refs(body)
        assert len(refs) == 1
        assert refs[0].kind == "agent"
        assert "CTO" in refs[0].target

    def test_verified_via_nfm_id(self):
        body = "Verified via NFM-3754"
        refs = ppd.extract_verifier_refs(body)
        assert len(refs) == 1
        assert refs[0].kind == "issue"
        assert refs[0].target == "NFM-3754"

    def test_confirmed_by_nfm_id(self):
        body = "Confirmed by NFM-3396 (formal AC-3 re-verification)"
        refs = ppd.extract_verifier_refs(body)
        assert len(refs) == 1
        assert refs[0].target == "NFM-3396"

    def test_no_verifier_returns_empty(self):
        body = "AC-2: 6/8 PASS"
        assert ppd.extract_verifier_refs(body) == []

    def test_multiple_verifiers(self):
        body = "Verified by CTO via NFM-3754; confirmed by NDE."
        refs = ppd.extract_verifier_refs(body)
        # Both the CTO agent ref AND the NFM-3754 issue ref are present.
        targets = {r.target for r in refs}
        assert "CTO" in targets or any("CTO" in t for t in targets)
        assert "NFM-3754" in targets

    def test_plain_nfm_mention_not_a_verifier(self):
        # "See NFM-3396" is a reference, not a verifier claim.
        body = "Cross-reference NFM-3396 for the formal scorecard."
        refs = ppd.extract_verifier_refs(body)
        # Should be empty — bare NFM mention without "verified/confirmed by"
        assert refs == []


# ---------------------------------------------------------------------------
# D2 — check_d2
# ---------------------------------------------------------------------------


class TestCheckD2:
    """``check_d2`` cross-checks the verifier claim against the
    authoritative source: a comment from the claimed agent (agent refs)
    OR a comment from the claimed verifier in the referenced issue
    (issue refs)."""

    def test_missing_verifier_comment_flags_phantom_verification(self):
        body = "verified by CTO via NFM-3754"
        # NFM-3754 has only a stale-run cleanup comment, no CTO comment.
        referenced_comments = ["stale-run cleanup by NDE"]
        finding = ppd.check_d2(body, referenced_comments)
        assert finding is not None
        assert finding.marker == "[PHANTOM-VERIFICATION]"

    def test_present_verifier_comment_passes(self):
        body = "verified by CTO via NFM-3754"
        referenced_comments = [
            "stale-run cleanup by NDE",
            "VERIFIED — all 8 checkpoints match. —CTO",
        ]
        assert ppd.check_d2(body, referenced_comments) is None

    def test_no_verifier_ref_returns_none(self):
        body = "All checks complete."
        assert ppd.check_d2(body, []) is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Already-flagged or reopened issues are skipped on subsequent runs."""

    def test_already_flagged_pseudo_pass_skip(self):
        comments = [
            "Work complete.",
            "[PHANTOM-PASS] AC-2 claimed 6/8 but no scoring-card table.",
        ]
        assert ppd.is_already_flagged(comments, "[PHANTOM-PASS]") is True

    def test_already_flagged_verification_skip(self):
        comments = [
            "Done.",
            "[PHANTOM-VERIFICATION] verifier claim unsubstantiated.",
        ]
        assert ppd.is_already_flagged(comments, "[PHANTOM-VERIFICATION]") is True

    def test_not_flagged_returns_false(self):
        comments = ["All complete, no issues."]
        assert ppd.is_already_flagged(comments, "[PHANTOM-PASS]") is False

    def test_reopened_status_skipped(self):
        # Reopened issues should be skipped regardless of prior flags.
        assert ppd.is_reopened_or_in_progress("in_progress") is True
        assert ppd.is_reopened_or_in_progress("blocked") is True
        assert ppd.is_reopened_or_in_progress("todo") is True
        assert ppd.is_reopened_or_in_progress("done") is False


# ---------------------------------------------------------------------------
# 7d lookback window
# ---------------------------------------------------------------------------


class TestLookbackWindow:
    """``within_lookback_days`` enforces the 7d window."""

    def test_within_7d(self):
        from datetime import datetime, timedelta, timezone

        # 3 days ago
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        assert ppd.within_lookback_days(three_days_ago, lookback_days=7) is True

    def test_outside_7d(self):
        from datetime import datetime, timedelta, timezone

        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        assert ppd.within_lookback_days(ten_days_ago, lookback_days=7) is False

    def test_edge_just_inside(self):
        from datetime import datetime, timedelta, timezone

        six_days_ago = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        assert ppd.within_lookback_days(six_days_ago, lookback_days=7) is True


# ---------------------------------------------------------------------------
# Comment assembly
# ---------------------------------------------------------------------------


class TestCommentBody:
    """The detector formats the audit comment deterministically so
    downstream grep / idempotency checks stay stable."""

    def test_phantom_pass_body_shape(self):
        finding = ppd.PhantomPassFinding(
            marker="[PHANTOM-PASS]",
            issue_id="NFM-3424",
            reason="AC-2 claimed 6/8 PASS but no scoring-card table",
        )
        body = ppd.render_phantom_pass_comment(finding)
        assert "[PHANTOM-PASS]" in body
        assert "NFM-3424" in body or "scoring-card" in body
        assert "ADR-010" in body  # cites the ADR

    def test_phantom_verification_body_shape(self):
        finding = ppd.PhantomVerificationFinding(
            marker="[PHANTOM-VERIFICATION]",
            issue_id="NFM-3424",
            verifier="CTO via NFM-3754",
            reason="no CTO comment in NFM-3754",
        )
        body = ppd.render_phantom_verification_comment(finding)
        assert "[PHANTOM-VERIFICATION]" in body
        assert "NFM-3754" in body or "CTO" in body


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero():
    """``python -m phantom_pass_detector --help`` exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "phantom_pass_detector", "--help"],
        capture_output=True,
        text=True,
        cwd=str(_SCRIPT_DIR),
    )
    assert proc.returncode == 0
    assert "--lookback-days" in proc.stdout