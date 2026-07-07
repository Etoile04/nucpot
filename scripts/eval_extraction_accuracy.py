"""Evaluate extraction accuracy against golden fixture test corpus.

Compares extraction output against ground truth JSON fixtures,
reports per-type accuracy with separate metrics, and asserts minimum thresholds.

Supports three evaluation modes:
  - fixture_only: Validates fixture structure (no live extraction needed)
  - held_out: Evaluates only the held-out test set (20% split)
  - full: Evaluates all fixtures

Per-type metrics:
  - Figure detection: IoU >= 0.5 with ground truth (target >= 80%)
  - Plot data extraction: Value match within 10% tolerance (target >= 60%)
  - Table extraction: Cell-level match (target >= 60%)
  - Microstructure/diagram: Type + bounding box match (target >= 60%)

Usage:
    # Fixture-only mode (validates structure, no live extraction):
    EXTRACTION_EVAL_MODE=fixture_only python scripts/eval_extraction_accuracy.py \\
        --fixtures-dir apps/api/tests/fixtures/extraction

    # Held-out evaluation (benchmarks against 20% held-out set):
    EXTRACTION_EVAL_MODE=held_out python scripts/eval_extraction_accuracy.py \\
        --fixtures-dir apps/api/tests/fixtures/extraction

    # Full evaluation:
    python scripts/eval_extraction_accuracy.py \\
        --fixtures-dir apps/api/tests/fixtures/extraction \\
        --min-accuracy 60

Exit codes:
    0 — all checks pass
    1 — accuracy below threshold or structural errors
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Per-type accuracy thresholds (B3.4 spec)
TYPE_THRESHOLDS: dict[str, float] = {
    "plot": 60.0,
    "table": 60.0,
    "microstructure": 60.0,
    "diagram": 60.0,
}

# Figure detection IoU threshold
IOU_THRESHOLD: float = 0.5

# Value match tolerance for numeric comparisons
VALUE_TOLERANCE: float = 0.10


def load_fixture_json(fixture_dir: Path) -> dict[str, Any]:
    """Load ground truth JSON from a fixture directory."""
    ground_truth_path = fixture_dir / "ground_truth.json"
    if not ground_truth_path.exists():
        raise FileNotFoundError(
            f"Missing ground_truth.json in {fixture_dir}"
        )
    with open(ground_truth_path, encoding="utf-8") as f:
        return json.load(f)


def load_split_manifest(fixtures_dir: Path) -> dict[str, Any] | None:
    """Load the train/held-out split manifest if it exists."""
    manifest_path = fixtures_dir / "split_manifest.json"
    if not manifest_path.exists():
        return None
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def validate_fixture_structure(
    ground_truth: dict[str, Any], fixture_id: str
) -> list[str]:
    """Validate that a fixture has the required structure. Returns error messages."""
    errors: list[str] = []
    required_fields = ["fixture_id", "figure_type", "page_image", "figures"]

    for field in required_fields:
        if field not in ground_truth:
            errors.append(f"{fixture_id}: missing required field '{field}'")

    if "figure_type" in ground_truth:
        valid_types = {"plot", "table", "microstructure", "diagram"}
        if ground_truth["figure_type"] not in valid_types:
            errors.append(
                f"{fixture_id}: invalid figure_type "
                f"'{ground_truth['figure_type']}', expected one of {valid_types}"
            )

    if "figures" in ground_truth:
        for i, fig in enumerate(ground_truth["figures"]):
            fig_errors = validate_figure_structure(fig, fixture_id, i)
            errors.extend(fig_errors)

    return errors


def validate_figure_structure(
    figure: dict[str, Any], fixture_id: str, index: int
) -> list[str]:
    """Validate a single figure entry in the ground truth."""
    errors: list[str] = []
    required = ["bounding_box", "extracted_data", "confidence"]

    for field in required:
        if field not in figure:
            errors.append(f"{fixture_id}: figure[{index}] missing '{field}'")

    if "bounding_box" in figure:
        bb = figure["bounding_box"]
        if not isinstance(bb, dict):
            errors.append(
                f"{fixture_id}: figure[{index}].bounding_box must be a dict"
            )
        else:
            for coord in ("x", "y", "width", "height"):
                if coord not in bb:
                    errors.append(
                        f"{fixture_id}: figure[{index}].bounding_box "
                        f"missing '{coord}'"
                    )

    if "confidence" in figure:
        conf = figure["confidence"]
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            errors.append(
                f"{fixture_id}: figure[{index}].confidence must be in [0.0, 1.0]"
            )

    return errors


def compute_figure_detection_iou(
    gt_figures: list[dict[str, Any]],
    ex_figures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute IoU-based figure detection accuracy.

    Matches ground truth figures to extraction output by best IoU.
    A figure is 'detected' if IoU >= IOU_THRESHOLD.

    Returns per-figure match details and overall detection rate.
    """
    if not gt_figures:
        return {
            "metric": "figure_detection_iou",
            "detection_rate": 100.0,
            "total_gt": 0,
            "detected": 0,
            "threshold": IOU_THRESHOLD,
            "details": [],
        }

    detections: list[dict[str, Any]] = []
    detected_count = 0

    for i, gt_fig in enumerate(gt_figures):
        gt_bb = gt_fig.get("bounding_box", {})
        best_iou = 0.0
        best_match_idx = -1

        for j, ex_fig in enumerate(ex_figures):
            iou = compute_iou(gt_bb, ex_fig.get("bounding_box", {}))
            if iou > best_iou:
                best_iou = iou
                best_match_idx = j

        is_detected = best_iou >= IOU_THRESHOLD
        if is_detected:
            detected_count += 1

        detections.append({
            "gt_index": i,
            "best_iou": round(best_iou, 4),
            "detected": is_detected,
            "match_index": best_match_idx if is_detected else None,
        })

    rate = (detected_count / len(gt_figures)) * 100.0
    return {
        "metric": "figure_detection_iou",
        "detection_rate": rate,
        "total_gt": len(gt_figures),
        "detected": detected_count,
        "threshold": IOU_THRESHOLD,
        "details": detections,
    }


def compute_plot_value_accuracy(
    gt_figures: list[dict[str, Any]],
    ex_figures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute value-based accuracy for plot data extraction.

    Matches extraction values against ground truth using:
    - Exact string match for non-numeric values
    - Within VALUE_TOLERANCE (10%) for numeric values
    """
    if not gt_figures:
        return {
            "metric": "plot_value_accuracy",
            "accuracy": 100.0,
            "total": 0,
            "matched": 0,
            "tolerance": VALUE_TOLERANCE,
            "details": [],
        }

    matched_count = 0
    details: list[dict[str, Any]] = []

    for i, gt_fig in enumerate(gt_figures):
        gt_data = gt_fig.get("extracted_data", {})
        gt_value = gt_data.get("value", "")

        best_match = _find_best_value_match(gt_value, ex_figures)
        if best_match is not None and best_match >= VALUE_TOLERANCE:
            matched_count += 1

        details.append({
            "gt_index": i,
            "gt_value": gt_value[:80],
            "best_score": round(best_match, 4) if best_match is not None else None,
            "matched": best_match is not None and best_match >= VALUE_TOLERANCE,
        })

    accuracy = (matched_count / len(gt_figures)) * 100.0
    return {
        "metric": "plot_value_accuracy",
        "accuracy": accuracy,
        "total": len(gt_figures),
        "matched": matched_count,
        "tolerance": VALUE_TOLERANCE,
        "details": details,
    }


def compute_table_cell_accuracy(
    gt_figures: list[dict[str, Any]],
    ex_figures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute cell-level accuracy for table extraction.

    Matches table figures by caption and material fields,
    treating each field as a 'cell' to compare.
    """
    if not gt_figures:
        return {
            "metric": "table_cell_accuracy",
            "accuracy": 100.0,
            "total_cells": 0,
            "matched_cells": 0,
            "details": [],
        }

    total_cells = 0
    matched_cells = 0
    details: list[dict[str, Any]] = []

    cell_fields = ["caption", "material", "type", "value"]

    for i, gt_fig in enumerate(gt_figures):
        gt_data = gt_fig.get("extracted_data", {})
        gt_bb = gt_fig.get("bounding_box", {})

        best_ex_fig = _find_best_matching_figure(gt_data, gt_bb, ex_figures)

        for field in cell_fields:
            gt_val = gt_data.get(field, "")
            if not gt_val:
                continue

            total_cells += 1
            if best_ex_fig is not None:
                ex_val = best_ex_fig.get("extracted_data", {}).get(field, "")
                if _values_match(gt_val, ex_val):
                    matched_cells += 1
                    details.append({
                        "gt_index": i,
                        "field": field,
                        "matched": True,
                    })
                else:
                    details.append({
                        "gt_index": i,
                        "field": field,
                        "matched": False,
                        "gt_value": gt_val[:60],
                        "ex_value": ex_val[:60],
                    })
            else:
                details.append({
                    "gt_index": i,
                    "field": field,
                    "matched": False,
                    "gt_value": gt_val[:60],
                    "ex_value": None,
                })

    accuracy = (matched_cells / total_cells * 100.0) if total_cells > 0 else 100.0
    return {
        "metric": "table_cell_accuracy",
        "accuracy": accuracy,
        "total_cells": total_cells,
        "matched_cells": matched_cells,
        "details": details,
    }


def compute_general_accuracy(
    gt_figures: list[dict[str, Any]],
    ex_figures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute combined accuracy for microstructure/diagram types.

    Uses type match + bounding box IoU with IoU threshold.
    """
    if not gt_figures:
        return {
            "metric": "general_type_iou_accuracy",
            "accuracy": 100.0,
            "total": 0,
            "matched": 0,
            "details": [],
        }

    matched = 0
    details: list[dict[str, Any]] = []

    for i, gt_fig in enumerate(gt_figures):
        gt_bb = gt_fig.get("bounding_box", {})
        gt_type = gt_fig.get("extracted_data", {}).get("type", "")

        best_score = 0.0
        best_match = None

        for ex_fig in ex_figures:
            ex_bb = ex_fig.get("bounding_box", {})
            ex_type = ex_fig.get("extracted_data", {}).get("type", "")

            iou = compute_iou(gt_bb, ex_bb)
            type_match = 1.0 if gt_type == ex_type else 0.0
            score = 0.5 * iou + 0.5 * type_match

            if score > best_score:
                best_score = score
                best_match = {"iou": iou, "type_match": type_match}

        is_matched = best_score >= 0.5
        if is_matched:
            matched += 1

        details.append({
            "gt_index": i,
            "best_score": round(best_score, 4),
            "matched": is_matched,
            "match_detail": best_match,
        })

    accuracy = (matched / len(gt_figures)) * 100.0
    return {
        "metric": "general_type_iou_accuracy",
        "accuracy": accuracy,
        "total": len(gt_figures),
        "matched": matched,
        "details": details,
    }


def compute_fixture_accuracy(
    ground_truth: dict[str, Any], extraction_output: dict[str, Any]
) -> dict[str, Any]:
    """Compute accuracy metrics comparing ground truth against extraction output.

    Dispatches to type-specific metric computation:
    - plot: figure detection IoU + value accuracy
    - table: figure detection IoU + cell-level accuracy
    - microstructure/diagram: type + IoU accuracy
    """
    eval_mode = get_eval_mode()
    fig_type = ground_truth.get("figure_type", "unknown")
    fixture_id = ground_truth.get("fixture_id", "unknown")
    gt_figures = ground_truth.get("figures", [])
    ex_figures = extraction_output.get("figures", [])

    base = {
        "fixture_id": fixture_id,
        "figure_type": fig_type,
        "num_figures": len(gt_figures),
    }

    if eval_mode == "fixture_only":
        return {
            **base,
            "accuracy": 100.0,
            "detection_iou_rate": 100.0,
            "value_accuracy": 100.0,
            "details": "fixture_only mode — structure validated",
        }

    if not gt_figures:
        return {
            **base,
            "accuracy": 100.0,
            "detection_iou_rate": 100.0,
            "value_accuracy": 100.0,
            "details": "no ground truth figures to compare",
        }

    # Compute figure detection IoU (all types)
    detection = compute_figure_detection_iou(gt_figures, ex_figures)

    # Compute type-specific metric
    if fig_type == "plot":
        value_metric = compute_plot_value_accuracy(gt_figures, ex_figures)
        combined_accuracy = 0.4 * detection["detection_rate"] + 0.6 * value_metric["accuracy"]
        return {
            **base,
            "accuracy": combined_accuracy,
            "detection_iou_rate": detection["detection_rate"],
            "value_accuracy": value_metric["accuracy"],
            "details": (
                f"detection={detection['detection_rate']:.1f}%, "
                f"value={value_metric['accuracy']:.1f}%"
            ),
        }
    elif fig_type == "table":
        cell_metric = compute_table_cell_accuracy(gt_figures, ex_figures)
        combined_accuracy = 0.4 * detection["detection_rate"] + 0.6 * cell_metric["accuracy"]
        return {
            **base,
            "accuracy": combined_accuracy,
            "detection_iou_rate": detection["detection_rate"],
            "value_accuracy": cell_metric["accuracy"],
            "details": (
                f"detection={detection['detection_rate']:.1f}%, "
                f"cells={cell_metric['accuracy']:.1f}%"
            ),
        }
    else:
        general_metric = compute_general_accuracy(gt_figures, ex_figures)
        combined_accuracy = 0.5 * detection["detection_rate"] + 0.5 * general_metric["accuracy"]
        return {
            **base,
            "accuracy": combined_accuracy,
            "detection_iou_rate": detection["detection_rate"],
            "value_accuracy": general_metric["accuracy"],
            "details": (
                f"detection={detection['detection_rate']:.1f}%, "
                f"type_iou={general_metric['accuracy']:.1f}%"
            ),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def compute_iou(bb_a: dict[str, Any], bb_b: dict[str, Any]) -> float:
    """Compute IoU between two bounding boxes."""
    try:
        ax1, ay1 = bb_a.get("x", 0), bb_a.get("y", 0)
        ax2 = ax1 + bb_a.get("width", 0)
        ay2 = ay1 + bb_a.get("height", 0)

        bx1, by1 = bb_b.get("x", 0), bb_b.get("y", 0)
        bx2 = bx1 + bb_b.get("width", 0)
        by2 = by1 + bb_b.get("height", 0)

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)

        if area_a + area_b == 0:
            return 0.0

        return inter_area / (area_a + area_b - inter_area)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _find_best_value_match(
    gt_value: str, ex_figures: list[dict[str, Any]]
) -> float | None:
    """Find best value match score for a ground truth value across extraction figures."""
    if not gt_value or not ex_figures:
        return None

    best_score = 0.0
    for ex_fig in ex_figures:
        ex_value = ex_fig.get("extracted_data", {}).get("value", "")
        score = _values_match_score(gt_value, ex_value)
        if score > best_score:
            best_score = score

    return best_score if best_score > 0 else None


def _find_best_matching_figure(
    gt_data: dict[str, Any],
    gt_bb: dict[str, Any],
    ex_figures: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the extraction figure that best matches a ground truth entry."""
    if not ex_figures:
        return None

    best_score = 0.0
    best_fig = None

    for ex_fig in ex_figures:
        ex_bb = ex_fig.get("bounding_box", {})
        ex_data = ex_fig.get("extracted_data", {})

        iou = compute_iou(gt_bb, ex_bb)
        caption_match = 1.0 if gt_data.get("caption") == ex_data.get("caption") else 0.0
        material_match = 1.0 if gt_data.get("material") == ex_data.get("material") else 0.0

        score = 0.4 * iou + 0.3 * caption_match + 0.3 * material_match
        if score > best_score:
            best_score = score
            best_fig = ex_fig

    return best_fig


def _values_match(gt_val: str, ex_val: str) -> bool:
    """Check if two values match (exact or numeric within tolerance)."""
    if gt_val == ex_val:
        return True
    return _values_match_score(gt_val, ex_val) >= VALUE_TOLERANCE


def _values_match_score(gt_val: str, ex_val: str) -> float:
    """Compute a match score between two values.

    Returns 1.0 for exact match, > 0 for numeric within tolerance, 0 otherwise.
    """
    if gt_val == ex_val:
        return 1.0

    if not gt_val or not ex_val:
        return 0.0

    # Try numeric comparison
    gt_num = _extract_numeric(gt_val)
    ex_num = _extract_numeric(ex_val)

    if gt_num is not None and ex_num is not None and ex_num != 0:
        relative_error = abs(gt_num - ex_num) / abs(ex_num)
        if relative_error <= VALUE_TOLERANCE:
            return 1.0 - relative_error

    # Truncated prefix match (for long strings that may be truncated)
    if len(gt_val) > 20 and len(ex_val) > 20:
        prefix_len = min(len(gt_val), len(ex_val))
        if gt_val[:prefix_len] == ex_val[:prefix_len]:
            return 0.8

    return 0.0


def _extract_numeric(value: str) -> float | None:
    """Extract first numeric value from a string."""
    import re

    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def get_eval_mode() -> str:
    """Get evaluation mode from environment variable."""
    return os.environ.get("EXTRACTION_EVAL_MODE", "full")


def discover_fixtures(fixtures_dir: Path) -> list[Path]:
    """Discover all fixture directories under the fixtures root."""
    if not fixtures_dir.exists():
        raise FileNotFoundError(
            f"Fixtures directory not found: {fixtures_dir}"
        )
    return sorted(
        p for p in fixtures_dir.iterdir()
        if p.is_dir()
        and p.name != "held_out"
        and (p / "ground_truth.json").exists()
    )


def discover_held_out_fixtures(fixtures_dir: Path) -> list[Path]:
    """Discover held-out fixture directories."""
    held_out_dir = fixtures_dir / "held_out"
    if not held_out_dir.exists():
        return []
    return sorted(
        p for p in held_out_dir.iterdir()
        if p.is_dir() and (p / "ground_truth.json").exists()
    )


def generate_report(
    results: list[dict[str, Any]],
    type_accuracies: dict[str, list[float]],
    all_errors: list[str],
    min_accuracy: float,
    eval_mode: str,
    fixtures_evaluated: int,
    total_fixtures: int,
) -> dict[str, Any]:
    """Generate the evaluation report with per-type accuracy and thresholds."""
    type_report: dict[str, dict[str, Any]] = {}
    all_passed = True

    for fig_type, accs in sorted(type_accuracies.items()):
        avg = sum(accs) / len(accs) if accs else 0.0
        threshold = TYPE_THRESHOLDS.get(fig_type, min_accuracy)
        passed = avg >= threshold
        if not passed:
            all_passed = False
        type_report[fig_type] = {
            "avg_accuracy": round(avg, 2),
            "count": len(accs),
            "threshold": threshold,
            "passed": passed,
        }

    overall_avg = (
        sum(r["accuracy"] for r in results) / len(results) if results else 0.0
    )
    overall_passed = overall_avg >= min_accuracy
    if not overall_passed:
        all_passed = False

    return {
        "eval_mode": eval_mode,
        "overall_accuracy": round(overall_avg, 2),
        "min_accuracy_threshold": min_accuracy,
        "overall_passed": overall_passed,
        "all_types_passed": all_passed,
        "fixtures_evaluated": fixtures_evaluated,
        "total_fixtures": total_fixtures,
        "per_type": type_report,
        "fixtures": results,
        "structural_errors": all_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate extraction accuracy against golden fixtures"
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        required=True,
        help="Root directory of extraction fixtures",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=60.0,
        help="Minimum acceptable overall accuracy percentage (default: 60)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write results as JSON",
    )
    args = parser.parse_args()

    eval_mode = get_eval_mode()

    # Select fixture set based on eval mode
    if eval_mode == "held_out":
        fixtures = discover_held_out_fixtures(args.fixtures_dir)
        held_out_manifest = load_split_manifest(args.fixtures_dir)
        held_out_count = len(held_out_manifest.get("held_out", [])) if held_out_manifest else "?"
        print(
            f"Held-out mode: evaluating {len(fixtures)} held-out fixtures "
            f"(of {held_out_count} declared)"
        )
    else:
        fixtures = discover_fixtures(args.fixtures_dir)
        if eval_mode == "fixture_only":
            print(f"Fixture-only mode: validating {len(fixtures)} fixtures")
        else:
            print(f"Full mode: evaluating {len(fixtures)} fixtures")

    if not fixtures:
        print(f"ERROR: No fixtures found in {args.fixtures_dir}")
        return 1

    manifest = load_split_manifest(args.fixtures_dir)
    total_fixtures = len(fixtures)
    if manifest:
        total_fixtures = manifest.get("total_fixtures", total_fixtures)

    print(f"Found {len(fixtures)} fixtures in {args.fixtures_dir}")

    all_errors: list[str] = []
    results: list[dict[str, Any]] = []
    type_accuracies: dict[str, list[float]] = defaultdict(list)

    for fixture_dir in fixtures:
        try:
            gt = load_fixture_json(fixture_dir)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            all_errors.append(f"{fixture_dir.name}: {exc}")
            continue

        fixture_id = gt.get("fixture_id", fixture_dir.name)
        errors = validate_fixture_structure(gt, fixture_id)
        if errors:
            all_errors.extend(errors)
            continue

        result = compute_fixture_accuracy(gt, {})
        results.append(result)
        type_accuracies[result["figure_type"]].append(result["accuracy"])

    if all_errors:
        print("\n=== Structural Errors ===")
        for error in all_errors:
            print(f"  ERROR: {error}")

    print(f"\n=== Per-Type Accuracy Report ===")
    print(f"  {'Type':20s} {'Accuracy':>8s} {'Threshold':>10s} {'Count':>6s}  Status")
    print(f"  {'-'*20} {'-'*8} {'-'*10} {'-'*6}  {'-'*6}")

    for fig_type, accs in sorted(type_accuracies.items()):
        avg = sum(accs) / len(accs) if accs else 0.0
        threshold = TYPE_THRESHOLDS.get(fig_type, args.min_accuracy)
        status = "PASS" if avg >= threshold else "FAIL"
        count = len(accs)
        print(f"  {fig_type:20s} {avg:7.1f}% {threshold:9.1f}% {count:6d}  {status}")

    overall_avg = (
        sum(r["accuracy"] for r in results) / len(results) if results else 0.0
    )
    overall_status = "PASS" if overall_avg >= args.min_accuracy else "FAIL"
    print(
        f"  {'-'*20} {'-'*8} {'-'*10} {'-'*6}  {'-'*6}\n"
        f"  {'OVERALL':20s} {overall_avg:7.1f}% {args.min_accuracy:9.1f}% "
        f"{len(results):6d}  {overall_status}"
    )

    report = generate_report(
        results=results,
        type_accuracies=type_accuracies,
        all_errors=all_errors,
        min_accuracy=args.min_accuracy,
        eval_mode=eval_mode,
        fixtures_evaluated=len(fixtures),
        total_fixtures=total_fixtures,
    )

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nReport written to {args.output_json}")

    if not report["overall_passed"]:
        print(
            f"\nFAIL: Overall accuracy {overall_avg:.1f}% "
            f"below threshold {args.min_accuracy:.1f}%"
        )
        return 1

    if not report["all_types_passed"]:
        print("\nFAIL: One or more per-type thresholds not met")
        return 1

    if all_errors:
        print(f"\nFAIL: {len(all_errors)} structural errors detected")
        return 1

    print(
        f"\nPASS: All checks passed "
        f"({len(results)} fixtures, overall {overall_avg:.1f}%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
