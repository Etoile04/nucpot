"""Unit tests for the diff classifier (NFM-3581).

The classifier consumes a `PromptDiff` (the structured diff between V1 and V2
prompt outputs) and produces a `Classification` verdict:

    status:       PASS | WARN | FAIL
    severity:     COSMETIC | NON_COSMETIC | BLOCKING | NONE

These tests pin the contract so the harness output stays stable across refactors.
"""

from __future__ import annotations

from tests.parity.diff_classifier import (
    Classification,
    PromptDiff,
    Severity,
    Status,
    classify_diff,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _empty_diff() -> PromptDiff:
    """A diff where both paths produced identical output (baseline)."""
    return PromptDiff(
        categories_only_in_v1=set(),
        categories_only_in_v2=set(),
        categories_shared={"密度"},
        properties_only_in_v1=set(),
        properties_only_in_v2=set(),
        properties_shared={"密度"},
        properties_in_ontology=set(),
        comment_diff_lines=[],
        retry_count_v1=1,
        retry_count_v2=1,
        prompt_length_v1=1000,
        prompt_length_v2=1000,
    )


def _purely_cosmetic_diff() -> PromptDiff:
    """A diff with only whitespace/punctuation changes (no semantic delta)."""
    return PromptDiff(
        categories_only_in_v1=set(),
        categories_only_in_v2=set(),
        categories_shared={"密度", "热导率"},
        properties_only_in_v1=set(),
        properties_only_in_v2=set(),
        properties_shared={"密度", "热导率"},
        properties_in_ontology=set(),
        comment_diff_lines=["- line one", "+ line one "],  # trailing-space only
        retry_count_v1=1,
        retry_count_v2=1,
        prompt_length_v1=1000,
        prompt_length_v2=1001,
    )


def _category_swap_diff() -> PromptDiff:
    """A diff where V1 used a category V2 dropped, and vice versa."""
    return PromptDiff(
        categories_only_in_v1={"已废弃类别"},
        categories_only_in_v2={"新增类别"},
        categories_shared={"密度"},
        properties_only_in_v1=set(),
        properties_only_in_v2=set(),
        properties_shared={"密度"},
        properties_in_ontology=set(),
        comment_diff_lines=[],
        retry_count_v1=1,
        retry_count_v2=1,
        prompt_length_v1=1000,
        prompt_length_v2=1000,
    )


def _blocking_property_loss_diff() -> PromptDiff:
    """A diff where V2 dropped a property that was BOTH in V1 AND in the ontology — blocking."""
    return PromptDiff(
        categories_only_in_v1=set(),
        categories_only_in_v2=set(),
        categories_shared={"密度", "腐蚀"},
        properties_only_in_v1={"critical_safety_metric"},
        properties_only_in_v2=set(),
        properties_shared={"密度"},
        properties_in_ontology={"critical_safety_metric"},  # the dropped property IS defined
        comment_diff_lines=[],
        retry_count_v1=1,
        retry_count_v2=1,
        prompt_length_v1=1000,
        prompt_length_v2=900,
    )


def _retry_regression_diff() -> PromptDiff:
    """A diff where V2 needs more retries — likely blocking for production."""
    return PromptDiff(
        categories_only_in_v1=set(),
        categories_only_in_v2=set(),
        categories_shared={"密度"},
        properties_only_in_v1=set(),
        properties_only_in_v2=set(),
        properties_shared={"密度"},
        properties_in_ontology=set(),
        comment_diff_lines=[],
        retry_count_v1=1,
        retry_count_v2=3,
        prompt_length_v1=1000,
        prompt_length_v2=1000,
    )


def _expected_drop_diff() -> PromptDiff:
    """V2 dropped a hardcoded V1 property that the ontology never defined (expected C1 behavior)."""
    return PromptDiff(
        categories_only_in_v1=set(),
        categories_only_in_v2=set(),
        categories_shared={"密度"},
        properties_only_in_v1={"legacy_legacy_legacy"},
        properties_only_in_v2=set(),
        properties_shared={"密度"},
        properties_in_ontology=set(),  # NOT defined in ontology
        comment_diff_lines=[],
        retry_count_v1=1,
        retry_count_v2=1,
        prompt_length_v1=1000,
        prompt_length_v2=950,
    )


# ---------------------------------------------------------------------------
# Status classification tests
# ---------------------------------------------------------------------------


class TestClassifyStatus:
    """Verify PASS / WARN / FAIL bucket assignment."""

    def test_identical_outputs_pass(self) -> None:
        result = classify_diff(_empty_diff())
        assert result.status is Status.PASS

    def test_whitespace_only_pass(self) -> None:
        result = classify_diff(_purely_cosmetic_diff())
        assert result.status is Status.PASS

    def test_category_swap_warns(self) -> None:
        result = classify_diff(_category_swap_diff())
        assert result.status is Status.WARN

    def test_blocking_property_loss_fails(self) -> None:
        result = classify_diff(_blocking_property_loss_diff())
        assert result.status is Status.FAIL

    def test_retry_regression_fails(self) -> None:
        result = classify_diff(_retry_regression_diff())
        assert result.status is Status.FAIL


# ---------------------------------------------------------------------------
# Severity annotation tests
# ---------------------------------------------------------------------------


class TestClassifySeverity:
    """Verify COSMETIC / NON_COSMETIC / BLOCKING annotation."""

    def test_identical_outputs_have_no_severity(self) -> None:
        result = classify_diff(_empty_diff())
        assert result.severity is Severity.NONE

    def test_whitespace_only_is_cosmetic(self) -> None:
        result = classify_diff(_purely_cosmetic_diff())
        assert result.severity is Severity.COSMETIC

    def test_category_swap_is_non_cosmetic(self) -> None:
        result = classify_diff(_category_swap_diff())
        assert result.severity is Severity.NON_COSMETIC

    def test_property_loss_is_blocking(self) -> None:
        result = classify_diff(_blocking_property_loss_diff())
        assert result.severity is Severity.BLOCKING

    def test_retry_regression_is_blocking(self) -> None:
        result = classify_diff(_retry_regression_diff())
        assert result.severity is Severity.BLOCKING


# ---------------------------------------------------------------------------
# Delta enumeration tests
# ---------------------------------------------------------------------------


class TestDeltaEnumeration:
    """Verify the classifier surfaces deltas for the report."""

    def test_property_loss_listed_in_deltas(self) -> None:
        result = classify_diff(_blocking_property_loss_diff())
        assert any("critical_safety_metric" in d for d in result.deltas)

    def test_category_swap_listed_in_deltas(self) -> None:
        result = classify_diff(_category_swap_diff())
        # Should mention both the dropped and added categories
        all_deltas = " ".join(result.deltas)
        assert "已废弃类别" in all_deltas or "新增类别" in all_deltas

    def test_cosmetic_diff_omits_delta_list(self) -> None:
        result = classify_diff(_purely_cosmetic_diff())
        assert result.deltas == [] or all(
            "whitespace" in d.lower() or "cosmetic" in d.lower() for d in result.deltas
        )

    def test_expected_c1_drop_is_warn_non_cosmetic(self) -> None:
        """V2 dropped a hardcoded V1 property not in ontology → WARN, not FAIL.

        This is the C1 fix: V2 should NOT carry forward hardcoded properties
        that the input ontology never defined. Such drops are expected.
        """
        result = classify_diff(_expected_drop_diff())
        assert result.status is Status.WARN
        assert result.severity is Severity.NON_COSMETIC

    def test_expected_c1_drop_lists_dropped_property(self) -> None:
        result = classify_diff(_expected_drop_diff())
        all_deltas = " ".join(result.deltas)
        assert "legacy_legacy_legacy" in all_deltas
        # Should NOT carry the BLOCKING annotation
        assert "defined in ontology" not in all_deltas


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Classifier must handle boundary inputs gracefully."""

    def test_empty_diff_does_not_crash(self) -> None:
        result = classify_diff(_empty_diff())
        assert isinstance(result, Classification)

    def test_determinism(self) -> None:
        """Same input must produce identical output (no hidden randomness)."""
        diff = _category_swap_diff()
        first = classify_diff(diff)
        second = classify_diff(diff)
        assert first.status is second.status
        assert first.severity is second.severity
        assert first.deltas == second.deltas

    def test_retry_count_zero_treated_as_one_attempt(self) -> None:
        """Some pipelines report 0 retries on success — must not be confused with failure."""
        diff = PromptDiff(
            categories_only_in_v1=set(),
            categories_only_in_v2=set(),
            categories_shared={"密度"},
            properties_only_in_v1=set(),
            properties_only_in_v2=set(),
            properties_shared={"密度"},
            properties_in_ontology=set(),
            comment_diff_lines=[],
            retry_count_v1=0,
            retry_count_v2=0,
            prompt_length_v1=1000,
            prompt_length_v2=1000,
        )
        result = classify_diff(diff)
        assert result.status is Status.PASS
