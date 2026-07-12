"""Tests for the extraction accuracy evaluation benchmark suite (NFM-863).

Validates held-out split, metric computation, threshold enforcement,
and per-type accuracy reporting.

These tests import from scripts.eval_extraction_accuracy which lives
outside the tests/ tree — PYTHONPATH is adjusted via conftest.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts package is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from eval_extraction_accuracy import (
    IOU_THRESHOLD,
    TYPE_THRESHOLDS,
    VALUE_TOLERANCE,
    compute_figure_detection_iou,
    compute_fixture_accuracy,
    compute_general_accuracy,
    compute_iou,
    compute_plot_value_accuracy,
    compute_table_cell_accuracy,
    discover_fixtures,
    discover_held_out_fixtures,
    generate_report,
    load_fixture_json,
    load_split_manifest,
    validate_fixture_structure,
    validate_figure_structure,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = _PROJECT_ROOT / "apps" / "api" / "tests" / "fixtures" / "extraction"


def _make_gt(
    fixture_id: str = "test_fixture",
    figure_type: str = "plot",
    figures: list[dict] | None = None,
) -> dict:
    """Build a minimal valid ground truth dict."""
    if figures is None:
        figures = [
            {
                "bounding_box": {"x": 10, "y": 20, "width": 100, "height": 80},
                "extracted_data": {"type": "xy_plot", "value": "Test data"},
                "confidence": 0.9,
            }
        ]
    return {
        "fixture_id": fixture_id,
        "figure_type": figure_type,
        "page_image": "page_001.png",
        "figures": figures,
    }


# ---------------------------------------------------------------------------
# IoU computation
# ---------------------------------------------------------------------------

class TestComputeIoU:
    def test_identical_boxes(self) -> None:
        bb = {"x": 0, "y": 0, "width": 100, "height": 100}
        assert compute_iou(bb, bb) == pytest.approx(1.0)

    def test_no_overlap(self) -> None:
        a = {"x": 0, "y": 0, "width": 10, "height": 10}
        b = {"x": 20, "y": 20, "width": 10, "height": 10}
        assert compute_iou(a, b) == 0.0

    def test_partial_overlap(self) -> None:
        a = {"x": 0, "y": 0, "width": 100, "height": 100}
        b = {"x": 50, "y": 50, "width": 100, "height": 100}
        inter = 50 * 50
        union = 10000 + 10000 - inter
        assert compute_iou(a, b) == pytest.approx(inter / union, abs=1e-4)

    def test_contained_box(self) -> None:
        outer = {"x": 0, "y": 0, "width": 100, "height": 100}
        inner = {"x": 25, "y": 25, "width": 50, "height": 50}
        expected = (50 * 50) / 10000
        assert compute_iou(outer, inner) == pytest.approx(expected, abs=1e-4)

    def test_zero_area_returns_zero(self) -> None:
        bb_zero = {"x": 0, "y": 0, "width": 0, "height": 0}
        assert compute_iou(bb_zero, bb_zero) == 0.0

    def test_missing_coords_returns_zero(self) -> None:
        partial = {"x": 0, "y": 0}
        assert compute_iou(partial, partial) == 0.0


# ---------------------------------------------------------------------------
# Fixture structure validation
# ---------------------------------------------------------------------------

class TestValidateFixtureStructure:
    def test_valid_fixture(self) -> None:
        errors = validate_fixture_structure(_make_gt(), "ok")
        assert errors == []

    def test_missing_required_fields(self) -> None:
        errors = validate_fixture_structure({}, "bad")
        assert any("fixture_id" in e for e in errors)
        assert any("figure_type" in e for e in errors)
        assert any("page_image" in e for e in errors)
        assert any("figures" in e for e in errors)

    def test_invalid_figure_type(self) -> None:
        errors = validate_fixture_structure(_make_gt(figure_type="nope"), "t")
        assert any("invalid figure_type" in e for e in errors)

    def test_all_valid_types(self) -> None:
        for fig_type in ("plot", "table", "microstructure", "diagram"):
            errors = validate_fixture_structure(_make_gt(figure_type=fig_type), fig_type)
            assert errors == [], f"Unexpected errors for {fig_type}: {errors}"


class TestValidateFigureStructure:
    def test_valid_figure(self) -> None:
        fig = _make_gt()["figures"][0]
        assert validate_figure_structure(fig, "ok", 0) == []

    def test_missing_bounding_box(self) -> None:
        fig = {"extracted_data": {}, "confidence": 0.5}
        assert any("bounding_box" in e for e in validate_figure_structure(fig, "t", 0))

    def test_confidence_out_of_range(self) -> None:
        fig = {
            "bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10},
            "extracted_data": {},
            "confidence": 1.5,
        }
        assert any("confidence" in e for e in validate_figure_structure(fig, "t", 0))


# ---------------------------------------------------------------------------
# Figure detection IoU metric
# ---------------------------------------------------------------------------

class TestFigureDetectionIoU:
    def test_perfect_detection(self) -> None:
        bb = {"x": 0, "y": 0, "width": 100, "height": 100}
        gt = [{"bounding_box": bb}]
        ex = [{"bounding_box": bb}]
        result = compute_figure_detection_iou(gt, ex)
        assert result["detected"] == 1
        assert result["detection_rate"] == 100.0
        assert result["total_gt"] == 1

    def test_no_detection_below_threshold(self) -> None:
        gt = [{"bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10}}]
        ex = [{"bounding_box": {"x": 100, "y": 100, "width": 10, "height": 10}}]
        result = compute_figure_detection_iou(gt, ex)
        assert result["detected"] == 0
        assert result["detection_rate"] == 0.0

    def test_empty_ground_truth(self) -> None:
        result = compute_figure_detection_iou([], [])
        assert result["detection_rate"] == 100.0
        assert result["total_gt"] == 0

    def test_partial_detection(self) -> None:
        bb_close = {"x": 0, "y": 0, "width": 100, "height": 100}
        bb_far = {"x": 200, "y": 200, "width": 100, "height": 100}
        gt = [
            {"bounding_box": bb_close},
            {"bounding_box": bb_far},
        ]
        ex = [{"bounding_box": bb_close}]
        result = compute_figure_detection_iou(gt, ex)
        assert result["detected"] == 1
        assert result["detection_rate"] == 50.0


# ---------------------------------------------------------------------------
# Plot value accuracy
# ---------------------------------------------------------------------------

class TestPlotValueAccuracy:
    def test_exact_string_match(self) -> None:
        gt = [{"extracted_data": {"type": "xy_plot", "value": "Yield strength 316SS"}}]
        ex = [{"extracted_data": {"type": "xy_plot", "value": "Yield strength 316SS"}}]
        result = compute_plot_value_accuracy(gt, ex)
        assert result["accuracy"] == 100.0
        assert result["matched"] == 1

    def test_numeric_within_tolerance(self) -> None:
        gt = [{"extracted_data": {"type": "xy_plot", "value": "100.0 MPa"}}]
        ex = [{"extracted_data": {"type": "xy_plot", "value": "105.0 MPa"}}]
        result = compute_plot_value_accuracy(gt, ex)
        # 5% relative error is within 10% tolerance → matched
        assert result["accuracy"] == 100.0

    def test_numeric_outside_tolerance(self) -> None:
        gt = [{"extracted_data": {"type": "xy_plot", "value": "100.0 MPa"}}]
        ex = [{"extracted_data": {"type": "xy_plot", "value": "115.0 MPa"}}]
        result = compute_plot_value_accuracy(gt, ex)
        # 15% relative error exceeds 10% tolerance → not matched
        assert result["accuracy"] == 0.0
        assert result["matched"] == 0

    def test_empty_ground_truth(self) -> None:
        result = compute_plot_value_accuracy([], [])
        assert result["accuracy"] == 100.0
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Table cell accuracy
# ---------------------------------------------------------------------------

class TestTableCellAccuracy:
    def test_perfect_cell_match(self) -> None:
        bb = {"x": 0, "y": 0, "width": 100, "height": 100}
        data = {
            "type": "data_table",
            "value": "Comp Table",
            "caption": "Chemical composition",
            "material": "Inconel 718",
        }
        gt = [{"bounding_box": bb, "extracted_data": data}]
        ex = [{"bounding_box": bb, "extracted_data": data}]
        result = compute_table_cell_accuracy(gt, ex)
        assert result["accuracy"] == 100.0
        assert result["matched_cells"] == result["total_cells"]

    def test_partial_cell_match(self) -> None:
        bb = {"x": 0, "y": 0, "width": 100, "height": 100}
        gt_data = {
            "type": "data_table",
            "value": "Comp Table",
            "caption": "Original caption",
            "material": "Inconel 718",
        }
        ex_data = {
            "type": "data_table",
            "value": "Comp Table",
            "caption": "Different caption",
            "material": "Inconel 718",
        }
        gt = [{"bounding_box": bb, "extracted_data": gt_data}]
        ex = [{"bounding_box": bb, "extracted_data": ex_data}]
        result = compute_table_cell_accuracy(gt, ex)
        assert result["matched_cells"] < result["total_cells"]
        assert result["accuracy"] > 0.0

    def test_empty_ground_truth(self) -> None:
        result = compute_table_cell_accuracy([], [])
        assert result["accuracy"] == 100.0
        assert result["total_cells"] == 0


# ---------------------------------------------------------------------------
# General accuracy (microstructure / diagram)
# ---------------------------------------------------------------------------

class TestGeneralAccuracy:
    def test_type_and_iou_match(self) -> None:
        bb = {"x": 0, "y": 0, "width": 100, "height": 100}
        gt = [{"bounding_box": bb, "extracted_data": {"type": "microstructure"}}]
        ex = [{"bounding_box": bb, "extracted_data": {"type": "microstructure"}}]
        result = compute_general_accuracy(gt, ex)
        assert result["accuracy"] == 100.0
        assert result["matched"] == 1

    def test_type_mismatch_with_good_iou(self) -> None:
        bb = {"x": 0, "y": 0, "width": 100, "height": 100}
        gt = [{"bounding_box": bb, "extracted_data": {"type": "microstructure"}}]
        ex = [{"bounding_box": bb, "extracted_data": {"type": "diagram"}}]
        result = compute_general_accuracy(gt, ex)
        # iou=1.0, type_match=0 → score=0.5 ≥ 0.5 → matched
        assert result["matched"] == 1
        assert result["accuracy"] == 100.0

    def test_no_match(self) -> None:
        gt = [{"bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10}, "extracted_data": {"type": "microstructure"}}]
        ex = [{"bounding_box": {"x": 100, "y": 100, "width": 10, "height": 10}, "extracted_data": {"type": "diagram"}}]
        result = compute_general_accuracy(gt, ex)
        assert result["matched"] == 0
        assert result["accuracy"] == 0.0

    def test_empty_ground_truth(self) -> None:
        result = compute_general_accuracy([], [])
        assert result["accuracy"] == 100.0
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Combined fixture accuracy
# ---------------------------------------------------------------------------

class TestComputeFixtureAccuracy:
    def test_fixture_only_mode(self) -> None:
        gt = _make_gt()
        with patch("eval_extraction_accuracy.get_eval_mode", return_value="fixture_only"):
            result = compute_fixture_accuracy(gt, {})
        assert result["accuracy"] == 100.0
        assert "fixture_only" in result["details"]

    def test_full_mode_with_empty_extraction(self) -> None:
        gt = _make_gt()
        with patch("eval_extraction_accuracy.get_eval_mode", return_value="full"):
            result = compute_fixture_accuracy(gt, {})
        assert result["detection_iou_rate"] == 0.0
        assert result["value_accuracy"] == 0.0

    def test_plot_dispatches_to_value_metric(self) -> None:
        gt = _make_gt(figure_type="plot")
        with patch("eval_extraction_accuracy.get_eval_mode", return_value="full"):
            result = compute_fixture_accuracy(gt, {})
        assert result["figure_type"] == "plot"

    def test_table_dispatches_to_cell_metric(self) -> None:
        gt = _make_gt(figure_type="table")
        with patch("eval_extraction_accuracy.get_eval_mode", return_value="full"):
            result = compute_fixture_accuracy(gt, {})
        assert result["figure_type"] == "table"

    def test_microstructure_dispatches_to_general_metric(self) -> None:
        gt = _make_gt(figure_type="microstructure")
        with patch("eval_extraction_accuracy.get_eval_mode", return_value="full"):
            result = compute_fixture_accuracy(gt, {})
        assert result["figure_type"] == "microstructure"

    def test_empty_figures_returns_100(self) -> None:
        gt = _make_gt(figures=[])
        with patch("eval_extraction_accuracy.get_eval_mode", return_value="full"):
            result = compute_fixture_accuracy(gt, {})
        assert result["accuracy"] == 100.0


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_passing_report(self) -> None:
        results = [
            {"fixture_id": "f1", "figure_type": "plot", "accuracy": 80.0},
            {"fixture_id": "f2", "figure_type": "table", "accuracy": 70.0},
        ]
        type_accs = {"plot": [80.0], "table": [70.0]}
        report = generate_report(
            results=results,
            type_accuracies=type_accs,
            all_errors=[],
            min_accuracy=60.0,
            eval_mode="fixture_only",
            fixtures_evaluated=2,
            total_fixtures=50,
        )
        assert report["overall_accuracy"] == 75.0
        assert report["overall_passed"] is True
        assert report["all_types_passed"] is True

    def test_failing_overall(self) -> None:
        results = [{"fixture_id": "f1", "figure_type": "plot", "accuracy": 50.0}]
        type_accs = {"plot": [50.0]}
        report = generate_report(
            results=results,
            type_accuracies=type_accs,
            all_errors=[],
            min_accuracy=60.0,
            eval_mode="fixture_only",
            fixtures_evaluated=1,
            total_fixtures=1,
        )
        assert report["overall_passed"] is False

    def test_empty_results(self) -> None:
        report = generate_report(
            results=[],
            type_accuracies={},
            all_errors=[],
            min_accuracy=60.0,
            eval_mode="fixture_only",
            fixtures_evaluated=0,
            total_fixtures=0,
        )
        assert report["overall_accuracy"] == 0.0
        assert report["overall_passed"] is False

    def test_type_threshold_from_type_thresholds_map(self) -> None:
        results = [{"fixture_id": "f1", "figure_type": "plot", "accuracy": 55.0}]
        type_accs = {"plot": [55.0]}
        report = generate_report(
            results=results,
            type_accuracies=type_accs,
            all_errors=[],
            min_accuracy=40.0,  # overall min is lower
            eval_mode="full",
            fixtures_evaluated=1,
            total_fixtures=1,
        )
        # Overall passes (55 > 40) but plot type fails (55 < 60 threshold)
        assert report["overall_passed"] is True
        assert report["all_types_passed"] is False
        assert report["per_type"]["plot"]["threshold"] == 60.0


# ---------------------------------------------------------------------------
# Held-out split (requires fixture files)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not FIXTURES_DIR.exists(),
    reason="Extraction fixtures directory not available",
)
class TestHeldOutSplit:
    def test_held_out_count(self) -> None:
        held_out = discover_held_out_fixtures(FIXTURES_DIR)
        assert len(held_out) == 10

    def test_train_held_out_no_overlap(self) -> None:
        manifest = load_split_manifest(FIXTURES_DIR)
        assert manifest is not None
        train_ids = set(manifest["train"])
        held_out_ids = set(manifest["held_out"])
        assert train_ids.isdisjoint(held_out_ids), (
            "Train and held-out manifest entries must not overlap"
        )
        assert train_ids | held_out_ids == set(train_ids) | set(held_out_ids)

    def test_split_manifest(self) -> None:
        manifest = load_split_manifest(FIXTURES_DIR)
        assert manifest is not None
        assert manifest["total_fixtures"] == 50
        assert len(manifest["train"]) == 40
        assert len(manifest["held_out"]) == 10
        assert manifest["held_out_ratio"] == 0.2
        assert manifest["split_method"] == "paper_number_mod5_eq0"

    def test_held_out_proportional_types(self) -> None:
        from collections import Counter

        held_out = discover_held_out_fixtures(FIXTURES_DIR)
        types = Counter()
        for p in held_out:
            gt = load_fixture_json(p)
            types[gt.get("figure_type", "?")] += 1
        # At least 3 different types should be represented
        assert len(types) >= 3, f"Only {len(types)} types in held-out: {types}"

    def test_all_held_out_are_symlinks(self) -> None:
        held_out = discover_held_out_fixtures(FIXTURES_DIR)
        for p in held_out:
            assert p.is_symlink(), f"{p.name} should be a symlink"


# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------

class TestThresholdConstants:
    def test_iou_threshold(self) -> None:
        assert IOU_THRESHOLD == 0.5

    def test_value_tolerance(self) -> None:
        assert VALUE_TOLERANCE == 0.10

    def test_type_thresholds_all_60(self) -> None:
        for key, val in TYPE_THRESHOLDS.items():
            assert val == 60.0, f"{key} threshold is {val}, expected 60.0"

    def test_type_thresholds_cover_all_types(self) -> None:
        expected = {"plot", "table", "microstructure", "diagram"}
        assert set(TYPE_THRESHOLDS.keys()) == expected
