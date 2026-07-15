"""Unit tests for extraction accuracy benchmark (NFM-863, B3.4).

Tests the comparison metric functions and held-out split logic
from scripts/eval_extraction_accuracy.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the eval script is importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = str(_REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from eval_extraction_accuracy import (
    BenchmarkResult,
    ValueParsingTestCase,
    build_value_parsing_cases,
    cells_match,
    compute_iou,
    create_held_out_split,
    generate_report,
    load_held_out_ids,
    run_figure_detection_benchmark,
    run_plot_extraction_benchmark,
    run_table_extraction_benchmark,
    values_within_tolerance,
)

# ===========================================================================
# compute_iou tests
# ===========================================================================


class TestComputeIou:
    """Tests for the IoU bounding box comparison metric."""

    def test_identical_boxes(self) -> None:
        assert compute_iou((0, 0, 100, 100), (0, 0, 100, 100)) == 1.0

    def test_no_overlap_disjoint(self) -> None:
        assert compute_iou((0, 0, 50, 50), (100, 100, 50, 50)) == 0.0

    def test_partial_overlap(self) -> None:
        # 50x50 boxes offset by 25px each direction
        iou = compute_iou((0, 0, 50, 50), (25, 25, 50, 50))
        assert 0.0 < iou < 1.0
        # Intersection: 25x25 = 625
        # Union: 2500 + 2500 - 625 = 4375
        assert abs(iou - 625 / 4375) < 1e-9

    def test_one_inside_other(self) -> None:
        # Small box entirely inside large box
        iou = compute_iou((0, 0, 100, 100), (25, 25, 50, 50))
        # Intersection: 50*50 = 2500
        # Union: 10000 + 2500 - 2500 = 10000
        assert abs(iou - 0.25) < 1e-9

    def test_edge_adjacent_no_overlap(self) -> None:
        assert compute_iou((0, 0, 50, 50), (50, 0, 50, 50)) == 0.0

    def test_zero_size_boxes(self) -> None:
        # Degenerate but valid boxes (min size is 1 in schema)
        iou = compute_iou((0, 0, 1, 1), (0, 0, 1, 1))
        assert iou == 1.0

    def test_touching_corners(self) -> None:
        assert compute_iou((0, 0, 10, 10), (10, 10, 10, 10)) == 0.0


# ===========================================================================
# values_within_tolerance tests
# ===========================================================================


class TestValuesWithinTolerance:
    """Tests for the value tolerance comparison metric."""

    def test_exact_match(self) -> None:
        assert values_within_tolerance(10.0, 10.0) is True

    def test_within_10_percent(self) -> None:
        assert values_within_tolerance(10.5, 10.0) is True
        assert values_within_tolerance(9.0, 10.0) is True

    def test_at_10_percent_boundary(self) -> None:
        assert values_within_tolerance(11.0, 10.0) is True
        assert values_within_tolerance(9.0, 10.0) is True

    def test_beyond_10_percent(self) -> None:
        assert values_within_tolerance(11.1, 10.0) is False
        assert values_within_tolerance(8.9, 10.0) is False

    def test_none_actual(self) -> None:
        assert values_within_tolerance(None, 10.0) is False

    def test_none_expected(self) -> None:
        assert values_within_tolerance(10.0, None) is False

    def test_both_none(self) -> None:
        assert values_within_tolerance(None, None) is False

    def test_zero_expected_near_zero_actual(self) -> None:
        assert values_within_tolerance(1e-10, 0.0) is True

    def test_zero_expected_large_actual(self) -> None:
        assert values_within_tolerance(1.0, 0.0) is False

    def test_custom_tolerance(self) -> None:
        # 5% tolerance
        assert values_within_tolerance(10.4, 10.0, tolerance=0.05) is True
        assert values_within_tolerance(10.6, 10.0, tolerance=0.05) is False

    def test_negative_values(self) -> None:
        assert values_within_tolerance(-10.5, -10.0) is True
        assert values_within_tolerance(-8.0, -10.0) is False

    def test_scientific_notation_values(self) -> None:
        assert values_within_tolerance(0.0036, 0.0035) is True
        assert values_within_tolerance(0.0085, 0.0085) is True


# ===========================================================================
# cells_match tests
# ===========================================================================


class TestCellsMatch:
    """Tests for the cell-level table comparison metric."""

    def test_perfect_match(self) -> None:
        actual = [["A", "B"], ["1", "2"]]
        expected = [["A", "B"], ["1", "2"]]
        matched, total = cells_match(actual, expected)
        assert matched == 4
        assert total == 4

    def test_whitespace_ignored(self) -> None:
        actual = [[" A ", "B"], ["1", " 2 "]]
        expected = [["A", "B"], ["1", "2"]]
        matched, total = cells_match(actual, expected)
        assert matched == 4
        assert total == 4

    def test_partial_match(self) -> None:
        actual = [["A", "B"], ["1", "X"]]
        expected = [["A", "B"], ["1", "2"]]
        matched, total = cells_match(actual, expected)
        assert matched == 3
        assert total == 4

    def test_empty_expected(self) -> None:
        matched, total = cells_match([], [])
        assert matched == 0
        assert total == 0

    def test_actual_fewer_rows(self) -> None:
        actual = [["A", "B"]]
        expected = [["A", "B"], ["1", "2"]]
        matched, total = cells_match(actual, expected)
        assert matched == 2
        assert total == 4

    def test_actual_fewer_columns(self) -> None:
        actual = [["A"], ["1"]]
        expected = [["A", "B"], ["1", "2"]]
        matched, total = cells_match(actual, expected)
        assert matched == 2
        assert total == 4

    def test_empty_actual(self) -> None:
        matched, total = cells_match([], [["A", "B"]])
        assert matched == 0
        assert total == 2

    def test_single_cell(self) -> None:
        matched, total = cells_match([["X"]], [["X"]])
        assert matched == 1
        assert total == 1


# ===========================================================================
# Held-out split tests
# ===========================================================================


class TestHeldOutSplit:
    """Tests for the golden fixture held-out split logic."""

    def test_split_ratio(self) -> None:
        records = [{"id": f"rec-{i}"} for i in range(100)]
        train, held_out, held_out_ids = create_held_out_split(records, ratio=0.2)
        assert len(held_out) == 20
        assert len(train) == 80
        assert len(held_out_ids) == 20

    def test_split_deterministic(self) -> None:
        records = [{"id": f"rec-{i}"} for i in range(50)]
        _, held_out_a, ids_a = create_held_out_split(records, seed=42)
        _, held_out_b, ids_b = create_held_out_split(records, seed=42)
        assert ids_a == ids_b

    def test_split_different_seeds(self) -> None:
        records = [{"id": f"rec-{i}"} for i in range(50)]
        _, _, ids_a = create_held_out_split(records, seed=1)
        _, _, ids_b = create_held_out_split(records, seed=2)
        assert ids_a != ids_b

    def test_split_no_overlap(self) -> None:
        records = [{"id": f"rec-{i}"} for i in range(30)]
        train, held_out, held_out_ids = create_held_out_split(records)
        train_ids = {r["id"] for r in train}
        assert train_ids.isdisjoint(held_out_ids)

    def test_split_minimum_held_out(self) -> None:
        records = [{"id": f"rec-{i}"} for i in range(3)]
        _, held_out, _ = create_held_out_split(records, ratio=0.2)
        assert len(held_out) >= 1

    def test_load_manifest_ids(self) -> None:
        manifest_path = (
            _REPO_ROOT
            / "apps"
            / "api"
            / "tests"
            / "fixtures"
            / "extraction"
            / "held_out"
            / "manifest.json"
        )
        if manifest_path.exists():
            ids = load_held_out_ids()
            assert isinstance(ids, set)
            assert len(ids) == 6
            assert "uo2-porosity" in ids


# ===========================================================================
# Value parsing tests
# ===========================================================================


class TestValueParsing:
    """Tests for the golden fixture value parsing test case builder."""

    def test_build_cases_from_records(self) -> None:
        records = [
            {
                "id": "test-1",
                "input": {"value": "10.96"},
                "expected": {"parsed_value": {"main_value": 10.96}},
            },
            {
                "id": "test-2",
                "input": {"value": "3.5e-2"},
                "expected": {"parsed_value": {"main_value": 0.035}},
            },
        ]
        cases = build_value_parsing_cases(records)
        assert len(cases) == 2
        assert isinstance(cases[0], ValueParsingTestCase)
        assert cases[0].raw_value == "10.96"
        assert cases[0].expected_parsed == 10.96
        assert cases[1].expected_parsed == 0.035

    def test_build_cases_with_range(self) -> None:
        records = [
            {
                "id": "range-test",
                "input": {"value": "10.4 to 10.97"},
                "expected": {
                    "parsed_value": {
                        "main_value": 10.685,
                        "range": [10.4, 10.97],
                    }
                },
            }
        ]
        cases = build_value_parsing_cases(records)
        assert cases[0].expected_range == (10.4, 10.97)

    def test_build_cases_missing_parsed_value(self) -> None:
        records = [{"id": "no-parse", "input": {}, "expected": {}}]
        cases = build_value_parsing_cases(records)
        assert cases[0].expected_parsed is None


# ===========================================================================
# Benchmark runner tests
# ===========================================================================


class TestFigureDetectionBenchmark:
    """Tests for the figure detection benchmark runner."""

    def test_benchmark_runs(self) -> None:
        result = run_figure_detection_benchmark()
        assert isinstance(result, BenchmarkResult)
        assert result.total > 0
        assert 0.0 <= result.accuracy <= 1.0

    def test_benchmark_accuracy_above_threshold(self) -> None:
        result = run_figure_detection_benchmark()
        # The synthetic data is designed so ≥80% of cases pass
        assert result.accuracy >= 0.80


class TestPlotExtractionBenchmark:
    """Tests for the plot extraction benchmark runner."""

    def test_benchmark_runs(self) -> None:
        result = run_plot_extraction_benchmark()
        assert isinstance(result, BenchmarkResult)
        assert result.total > 0

    def test_benchmark_accuracy_above_threshold(self) -> None:
        result = run_plot_extraction_benchmark()
        # Synthetic data designed so ≥60% of values match
        assert result.accuracy >= 0.60


class TestTableExtractionBenchmark:
    """Tests for the table extraction benchmark runner."""

    def test_benchmark_runs(self) -> None:
        result = run_table_extraction_benchmark()
        assert isinstance(result, BenchmarkResult)
        assert result.total > 0

    def test_benchmark_accuracy_above_threshold(self) -> None:
        result = run_table_extraction_benchmark()
        # Synthetic data designed so ≥60% of cells match
        assert result.accuracy >= 0.60


# ===========================================================================
# Report generation tests
# ===========================================================================


class TestReportGeneration:
    """Tests for the accuracy report generator."""

    def test_report_contains_all_categories(self) -> None:
        results = [
            BenchmarkResult(
                category="Test Cat",
                passed=5,
                total=10,
                accuracy=0.50,
                target=0.60,
                threshold_met=False,
            )
        ]
        report = generate_report(results)
        assert "Test Cat" in report
        assert "50.0%" in report
        assert "FAIL" in report

    def test_report_shows_overall(self) -> None:
        results = [
            BenchmarkResult("A", 10, 10, 1.0, 0.6, True),
            BenchmarkResult("B", 5, 10, 0.5, 0.6, False),
        ]
        report = generate_report(results)
        assert "Overall weighted accuracy" in report

    def test_report_passed_message(self) -> None:
        results = [
            BenchmarkResult("All Good", 10, 10, 1.0, 0.6, True),
        ]
        report = generate_report(results)
        assert "PASSED" in report

    def test_report_failed_message(self) -> None:
        results = [
            BenchmarkResult("Bad", 0, 10, 0.0, 0.6, False),
        ]
        report = generate_report(results)
        assert "FAILED" in report
