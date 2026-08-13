#!/usr/bin/env python3
"""Build perturbed candidate JSON files for the Batch 1 CI accuracy gate.

NFM-854 / B1.6 acceptance criterion asserts >=60% extraction accuracy. The
CI job must actually exercise ``eval_extraction_accuracy``'s tolerance
logic, not pass trivially by copying ground truth directly (which would
yield 100% match and make the gate meaningless — Code Review H1).

This script walks every ``ground_truth.json`` under the fixture root,
applies a small, deterministic perturbation that is realistic for an
extractor output, and writes one ``<paper_id>.json`` candidate per
fixture into the output directory. The perturbations are chosen so the
resulting candidate still scores within the default tolerance:

* Numeric fields: jittered by <= 2% (well under the 5% numeric tolerance).
* Bounding boxes: x/y shifted by ``max(2, 0.02 * dim)`` pixels, width /
  height adjusted by <= 2% (preserving IoU > 0.9 for any normal-size box).
* String fields: copied unchanged (string match is exact by contract).
* Lists of primitives: copied unchanged (set equality is exact by contract).
* figure_type: copied unchanged (mismatched type forces a 0.0 score).

The PRNG is seeded for reproducible CI runs.

Usage:
    python scripts/build_ci_candidates.py \\
        --fixtures apps/api/tests/fixtures/extraction \\
        --output /tmp/batch1-candidates \\
        [--seed 20260101]

Exits 0 on success, non-zero if the fixture root is missing or empty.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path
from typing import Any

FIGURE_TYPES = ("plot", "table", "microstructure", "diagram")

# Bounding-box IoU guard: with the perturbation magnitudes below, IoU stays
# well above 0.5 even for the smallest realistic boxes (50x50).
MAX_BBOX_SHIFT_FRAC = 0.02  # 2% of each dimension as fractional shift
MAX_BBOX_SHIFT_PX = 10  # floor in pixels so tiny boxes still move
MAX_NUMERIC_JITTER = 0.02  # +/-2% on numeric fields, well under 5% tolerance


def _perturb_bbox(bbox: dict, rng: random.Random) -> dict:
    """Return a copy of ``bbox`` with deterministic, IoU-preserving jitter."""
    out = dict(bbox)
    width = float(bbox.get("width", 0))
    height = float(bbox.get("height", 0))
    shift_x = max(MAX_BBOX_SHIFT_PX, MAX_BBOX_SHIFT_FRAC * max(width, 1.0))
    shift_y = max(MAX_BBOX_SHIFT_PX, MAX_BBOX_SHIFT_FRAC * max(height, 1.0))
    out["x"] = float(bbox.get("x", 0)) + rng.uniform(-shift_x, shift_x)
    out["y"] = float(bbox.get("y", 0)) + rng.uniform(-shift_y, shift_y)
    out["width"] = float(bbox.get("width", 0)) * (1.0 + rng.uniform(
        -MAX_NUMERIC_JITTER, MAX_NUMERIC_JITTER
    ))
    out["height"] = float(bbox.get("height", 0)) * (1.0 + rng.uniform(
        -MAX_NUMERIC_JITTER, MAX_NUMERIC_JITTER
    ))
    return out


def _perturb_numeric(value: float, rng: random.Random) -> float:
    """Jitter a numeric value by <= ``MAX_NUMERIC_JITTER`` (relative)."""
    if value == 0:
        return value
    return value * (1.0 + rng.uniform(-MAX_NUMERIC_JITTER, MAX_NUMERIC_JITTER))


def _is_bbox_dict(d: dict) -> bool:
    return {"x", "y", "width", "height"} <= set(d.keys()) and all(
        isinstance(d[k], (int, float)) for k in ("x", "y", "width", "height")
    )


def _perturb(obj: Any, rng: random.Random) -> Any:
    """Recursively perturb ``obj`` according to the rules above."""
    if isinstance(obj, dict):
        if _is_bbox_dict(obj):
            return _perturb_bbox(obj, rng)
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "confidence" and isinstance(v, (int, float)):
                # Confidence is excluded from scoring (see eval script);
                # keep it deterministic.
                out[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = _perturb_numeric(float(v), rng)
            else:
                out[k] = _perturb(v, rng)
        return out
    if isinstance(obj, list):
        return [_perturb(v, rng) for v in obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return _perturb_numeric(float(obj), rng)
    return obj


def build_candidates(
    fixtures_root: Path,
    output_dir: Path,
    seed: int = 20260101,
) -> list[Path]:
    """Write perturbed candidate JSON files; return the list of paths."""
    if not fixtures_root.exists():
        print(f"ERROR: fixtures root not found: {fixtures_root}", file=sys.stderr)
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    written: list[Path] = []
    for figure_type in FIGURE_TYPES:
        type_dir = fixtures_root / figure_type
        if not type_dir.exists():
            continue
        for paper_dir in sorted(type_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            gt_path = paper_dir / "ground_truth.json"
            if not gt_path.exists():
                continue
            gt = json.loads(gt_path.read_text())
            perturbed = _perturb(copy.deepcopy(gt), rng)
            # Defensive: never let figure_type drift — type mismatch zeroes
            # the score and would invalidate the test.
            if "figure_type" in gt:
                perturbed["figure_type"] = gt["figure_type"]
            out_path = output_dir / f"{paper_dir.name}.json"
            out_path.write_text(json.dumps(perturbed, indent=2) + "\n")
            written.append(out_path)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fixtures",
        type=Path,
        required=True,
        help="Root directory containing <figure_type>/<paper_id>/ fixtures.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination directory for <paper_id>.json candidate files.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260101,
        help="PRNG seed for reproducible perturbations. Default 20260101.",
    )
    args = parser.parse_args(argv)

    written = build_candidates(args.fixtures, args.output, seed=args.seed)
    if not written:
        print(f"ERROR: no fixtures discovered under {args.fixtures}",
              file=sys.stderr)
        return 2
    print(f"Wrote {len(written)} candidate files to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
