#!/usr/bin/env python3
"""Extraction accuracy evaluation for NFMD Batch 1 (B1.6).

Compares extraction output JSON files against the golden fixture
ground truth and reports per-type and overall accuracy.

Each fixture lives under
    apps/api/tests/fixtures/extraction/<type>/<paper_id>/
with two files:
    - ground_truth.json
    - image.<ext>            # sample page image, presence optional for eval

The candidate output directory must contain one JSON file per
fixture with the same stem as the fixture directory:
    <candidates_dir>/<paper_id>.json

The candidate JSON shape mirrors the ground truth, which follows
the VisionExtractionResult contract (figure_type: 'plot' | 'table' |
'microstructure' | 'diagram' plus plot_data or table_data with
bounding boxes, extracted data, and confidence).

Per-type accuracy is defined as the fraction of fixtures whose
candidate JSON matches the ground truth within a configurable
tolerance.  Field-level scoring rules:

  * String fields: exact match.
  * Numeric fields: |candidate - truth| / |truth| <= tolerance.
  * Bounding boxes: IoU >= iou_threshold.
  * Lists of primitives: set equality (or length match for series).
  * Lists of dicts: average of per-element match scores.

Usage:
    python scripts/eval_extraction_accuracy.py \\
        --fixtures apps/api/tests/fixtures/extraction \\
        --candidates <output-dir> \\
        [--threshold 0.6] \\
        [--iou-threshold 0.5] \\
        [--numeric-tolerance 0.05]

Exits 0 if overall accuracy >= threshold, else 1.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

FIGURE_TYPES = ("plot", "table", "microstructure", "diagram")
EXPECTED_COUNTS = {"plot": 20, "table": 15, "microstructure": 10, "diagram": 5}


@dataclass
class Fixture:
    figure_type: str
    paper_id: str
    fixture_dir: Path
    ground_truth: dict


    @property
    def path(self) -> Path:
        return self.fixture_dir


@dataclass
class PerTypeReport:
    total: int = 0
    matched: int = 0
    scores: list[float] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if not self.total:
            return 0.0
        return self.matched / self.total


def discover_fixtures(fixtures_root: Path) -> list[Fixture]:
    """Walk <fixtures_root>/<figure_type>/<paper_id>/ and load fixtures."""
    fixtures: list[Fixture] = []
    for figure_type in FIGURE_TYPES:
        type_dir = fixtures_root / figure_type
        if not type_dir.exists():
            continue
        for paper_dir in sorted(type_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            gt_path = paper_dir / "ground_truth.json"
            if not gt_path.exists():
                print(
                    f"WARN: missing ground_truth.json in {paper_dir}",
                    file=sys.stderr,
                )
                continue
            try:
                gt = json.loads(gt_path.read_text())
            except json.JSONDecodeError as exc:
                print(
                    f"WARN: invalid ground_truth.json in {paper_dir}: {exc}",
                    file=sys.stderr,
                )
                continue
            fixtures.append(
                Fixture(
                    figure_type=figure_type,
                    paper_id=paper_dir.name,
                    fixture_dir=paper_dir,
                    ground_truth=gt,
                )
            )
    return fixtures


def _is_close(a: float, b: float, tol: float) -> bool:
    if a is None or b is None:
        return a == b
    if not math.isfinite(a) or not math.isfinite(b):
        return a == b
    if abs(b) < 1e-12:
        return abs(a) < tol
    return abs(a - b) / abs(b) <= tol


def _iou(box_a: dict, box_b: dict) -> float:
    """IoU for {x, y, width, height} boxes."""
    try:
        ax, ay = box_a["x"], box_a["y"]
        aw, ah = box_a["width"], box_a["height"]
        bx, by = box_b["x"], box_b["y"]
        bw, bh = box_b["width"], box_b["height"]
    except (KeyError, TypeError):
        return 0.0
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _match_value(
    truth,
    candidate,
    numeric_tol: float,
    iou_thr: float,
) -> float:
    """Return a score in [0, 1] for matching a single ground truth value."""
    if isinstance(truth, dict) and isinstance(candidate, dict):
        return _match_dict(truth, candidate, numeric_tol, iou_thr)
    if isinstance(truth, list) and isinstance(candidate, list):
        return _match_list(truth, candidate, numeric_tol, iou_thr)
    if isinstance(truth, (int, float)) and isinstance(candidate, (int, float)):
        return 1.0 if _is_close(float(candidate), float(truth), numeric_tol) else 0.0
    if truth is None and candidate is None:
        return 1.0
    if isinstance(truth, str) and isinstance(candidate, str):
        return 1.0 if truth == candidate else 0.0
    if isinstance(truth, bool) and isinstance(candidate, bool):
        return 1.0 if truth == candidate else 0.0
    return 0.0


def _match_list(
    truth: list,
    candidate: list,
    numeric_tol: float,
    iou_thr: float,
) -> float:
    if not truth and not candidate:
        return 1.0
    if not truth or not candidate:
        return 0.0
    if all(isinstance(x, dict) for x in truth) and all(
        isinstance(x, dict) for x in candidate
    ):
        scores = [
            _match_dict(t, c, numeric_tol, iou_thr)
            for t, c in zip(truth, candidate)
        ]
        return sum(scores) / len(scores)
    return 1.0 if [str(x) for x in truth] == [str(x) for x in candidate] else 0.0


def _match_dict(
    truth: dict,
    candidate: dict,
    numeric_tol: float,
    iou_thr: float,
) -> float:
    if {"x", "y", "width", "height"} <= set(truth.keys()):
        if not isinstance(candidate, dict):
            return 0.0
        iou = _iou(truth, candidate)
        return 1.0 if iou >= iou_thr else 0.0

    if not truth:
        return 1.0 if not candidate else 0.0

    matched = 0
    total = 0
    for key, tval in truth.items():
        if key == "confidence":
            continue
        total += 1
        cval = candidate.get(key) if isinstance(candidate, dict) else None
        if _match_value(tval, cval, numeric_tol, iou_thr) >= 1.0:
            matched += 1
    return matched / total if total else 1.0


def score_fixture(
    fixture: Fixture,
    candidate,
    numeric_tol: float,
    iou_thr: float,
) -> float:
    if candidate is None:
        return 0.0
    cand_type = candidate.get("figure_type")
    truth_type = fixture.ground_truth.get("figure_type")
    if cand_type and truth_type and cand_type != truth_type:
        return 0.0
    return _match_dict(
        fixture.ground_truth, candidate, numeric_tol, iou_thr
    )


def _load_candidate(candidates_dir: Path, paper_id: str):
    path = candidates_dir / f"{paper_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"WARN: invalid candidate JSON for {paper_id}: {exc}",
              file=sys.stderr)
        return None


def _report(
    fixtures: list[Fixture],
    candidates: dict,
    numeric_tol: float,
    iou_thr: float,
    counts: dict,
    overall_scores: list[float],
) -> dict:
    for fix in fixtures:
        cand = candidates.get(fix.paper_id)
        s = score_fixture(fix, cand, numeric_tol, iou_thr)
        bucket = counts.setdefault(fix.figure_type, PerTypeReport())
        bucket.total += 1
        bucket.scores.append(s)
        if s >= 0.999:
            bucket.matched += 1
        overall_scores.append(s)
    overall = (
        sum(overall_scores) / len(overall_scores) if overall_scores else 0.0
    )
    return {
        "per_type": {
            t: {
                "total": counts.get(t, PerTypeReport()).total,
                "matched": counts.get(t, PerTypeReport()).matched,
                "accuracy": counts.get(t, PerTypeReport()).accuracy,
                "expected": EXPECTED_COUNTS.get(t, 0),
            }
            for t in FIGURE_TYPES
        },
        "overall_accuracy": overall,
        "total_fixtures": len(fixtures),
        "matched_fixtures": sum(
            1 for s in overall_scores if s >= 0.999
        ),
    }


def _print_report(report: dict) -> None:
    print("Batch 1 extraction accuracy report")
    print("=" * 60)
    for t in FIGURE_TYPES:
        info = report["per_type"][t]
        line = (
            f"  {t:<14} total={info['total']:<3} matched={info['matched']:<3}"
            f"  acc={info['accuracy']:.3f}  (expected={info['expected']})"
        )
        print(line)
    print("-" * 60)
    print(
        f"  overall          total={report['total_fixtures']:<3}"
        f" matched={report['matched_fixtures']:<3}"
        f"  acc={report['overall_accuracy']:.3f}"
    )
    print("=" * 60)


def _check_expected_counts(report: dict) -> list[str]:
    warnings: list[str] = []
    for t in FIGURE_TYPES:
        actual = report["per_type"][t]["total"]
        expected = report["per_type"][t]["expected"]
        if actual < expected:
            warnings.append(
                f"fixture coverage for {t} below target "
                f"({actual}/{expected})"
            )
    return warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fixtures",
        type=Path,
        required=True,
        help="Root directory containing <figure_type>/<paper_id>/ fixtures.",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        required=True,
        help="Directory containing <paper_id>.json candidate outputs.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Minimum overall accuracy (0-1) for success. Default 0.6.",
    )
    parser.add_argument(
        "--iou-threshold",
        type=float,
        default=0.5,
        help="IoU threshold for bounding-box match. Default 0.5.",
    )
    parser.add_argument(
        "--numeric-tolerance",
        type=float,
        default=0.05,
        help="Relative tolerance for numeric fields. Default 0.05 (5%%).",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="If given, write the full numeric report here as JSON.",
    )
    parser.add_argument(
        "--strict-coverage",
        action="store_true",
        help="Fail if fixture counts do not meet B1.6 coverage targets.",
    )
    args = parser.parse_args(argv)

    fixtures = discover_fixtures(args.fixtures)
    if not fixtures:
        print(f"ERROR: no fixtures discovered under {args.fixtures}",
              file=sys.stderr)
        return 2

    candidates = {
        fix.paper_id: cand
        for fix in fixtures
        for cand in [_load_candidate(args.candidates, fix.paper_id)]
        if cand is not None
    }

    counts: dict = {}
    overall_scores: list[float] = []
    report = _report(
        fixtures, candidates, args.numeric_tolerance, args.iou_threshold,
        counts, overall_scores,
    )
    _print_report(report)
    coverage_warnings = _check_expected_counts(report)
    for w in coverage_warnings:
        print(f"WARN: {w}", file=sys.stderr)

    if args.report_json is not None:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2) + "\n")

    ok = report["overall_accuracy"] >= args.threshold
    if args.strict_coverage and coverage_warnings:
        ok = False

    if ok:
        print(
            f"\nPASS: overall accuracy "
            f"{report['overall_accuracy']:.3f} >= {args.threshold:.3f}",
            file=sys.stderr,
        )
        return 0
    print(
        f"\nFAIL: overall accuracy "
        f"{report['overall_accuracy']:.3f} < {args.threshold:.3f}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
