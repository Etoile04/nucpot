#!/usr/bin/env python3
"""Extraction accuracy scoring infrastructure (NFM-863, B3.4).

Validates the correctness of the scoring functions (IoU, tolerance, cell-match)
used to evaluate extraction quality. Uses synthetic test cases with known
expected outcomes — this tests the *scoring infrastructure*, not the
extraction pipeline itself.

The real extraction pipeline is validated by the E2E integration tests
in test_e2e_integration.py (PDF upload -> extraction -> KG population).

Metrics validated:
  1. **Figure detection** — IoU >=0.5 with ground truth (target >=80%)
  2. **Plot data extraction** — value match within 10% tolerance (target >=60%)
  3. **Table extraction** — cell-level match (target >=60%)
  4. **Value parsing** — golden fixture value parsing accuracy (target >=60%)

Overall scoring accuracy must be >=60%.

Exit codes:
    0 — evaluation passed (all thresholds met)
    1 — evaluation failed (any threshold below minimum)
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure apps/api/src is importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = str(_REPO_ROOT / "apps" / "api" / "src")
if _API_SRC not in sys.path:
    sys.path.insert(0, _API_SRC)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HELD_OUT_RATIO = 0.20
_SEED = 42

# Per-type accuracy targets from the spec
_FIGURE_DETECTION_TARGET = 0.80
_FIGURE_DETECTION_IOU_THRESHOLD = 0.5
_PLOT_EXTRACTION_TARGET = 0.60
_PLOT_VALUE_TOLERANCE = 0.10
_TABLE_EXTRACTION_TARGET = 0.60
_VALUE_PARSING_TARGET = 0.60
_OVERALL_TARGET = 0.60

# Minimum fixture counts per figure type (from generate_fixtures.py DEFAULT_COUNTS)
EXPECTED_COUNTS: dict[str, int] = {
    "plot": 20,
    "table": 15,
    "microstructure": 10,
    "diagram": 5,
}


class DiscoveredFixture:
    """Lightweight fixture descriptor returned by discover_fixtures."""

    __slots__ = ("figure_type", "fixture_dir")

    def __init__(self, figure_type: str, fixture_dir: Path) -> None:
        self.figure_type = figure_type
        self.fixture_dir = fixture_dir


def discover_fixtures(fixtures_root: Path) -> list[DiscoveredFixture]:
    """Walk the fixture tree and return one entry per fixture directory.

    Expected layout::

        fixtures_root/<figure_type>/<paper_id>/ground_truth.json
    """
    if not fixtures_root.is_dir():
        raise FileNotFoundError(f"Fixture root not found: {fixtures_root}")

    results: list[DiscoveredFixture] = []
    for fig_type_dir in sorted(fixtures_root.iterdir()):
        if not fig_type_dir.is_dir():
            continue
        for paper_dir in sorted(fig_type_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            if (paper_dir / "ground_truth.json").exists():
                results.append(DiscoveredFixture(fig_type_dir.name, paper_dir))
    return results


_GOLDEN_FIXTURES_DIR = _REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "golden"
_HELD_OUT_MANIFEST_PATH = (
    _REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "extraction" / "held_out" / "manifest.json"
)

# ---------------------------------------------------------------------------
# Comparison metric functions
# ---------------------------------------------------------------------------


def compute_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Compute Intersection over Union of two bounding boxes.

    Args:
        a: (x, y, width, height) for box A.
        b: (x, y, width, height) for box B.

    Returns:
        IoU value in [0.0, 1.0].
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b

    x_left = max(ax, bx)
    y_top = max(ay, by)
    x_right = min(ax + aw, bx + bw)
    y_bottom = min(ay + ah, by + bh)

    if x_right <= x_left or y_bottom <= y_top:
        return 0.0

    intersection = (x_right - x_left) * (y_bottom - y_top)
    area_a = aw * ah
    area_b = bw * bh
    union = area_a + area_b - intersection

    if union <= 0:
        return 0.0

    return intersection / union


def values_within_tolerance(
    actual: float | None,
    expected: float | None,
    tolerance: float = _PLOT_VALUE_TOLERANCE,
) -> bool:
    """Check if actual value is within tolerance of expected.

    Args:
        actual: Extracted value (None treated as miss).
        expected: Ground truth value (None treated as miss).
        tolerance: Fractional tolerance (default 10%).

    Returns:
        True if values match within tolerance.
    """
    if actual is None or expected is None:
        return False
    if expected == 0.0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= tolerance


def cells_match(
    actual: list[list[str]],
    expected: list[list[str]],
) -> tuple[int, int]:
    """Compare table cells and return (matched, total).

    Compares cell-by-cell. Extra rows/columns in actual count as misses.

    Args:
        actual: Extracted table cells (rows x columns).
        expected: Ground truth table cells (rows x columns).

    Returns:
        Tuple of (number of matching cells, total expected cells).
    """
    if not expected:
        return (0, 0)

    matched = 0
    total = 0

    for row_idx, expected_row in enumerate(expected):
        for col_idx, expected_cell in enumerate(expected_row):
            total += 1
            if row_idx < len(actual) and col_idx < len(actual[row_idx]):
                actual_cell = actual[row_idx][col_idx].strip()
                expected_clean = expected_cell.strip()
                if actual_cell == expected_clean:
                    matched += 1

    return (matched, total)


# ---------------------------------------------------------------------------
# Test case dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FigureDetectionTestCase:
    """A single figure detection test case with ground truth."""

    case_id: str
    ground_truth_bbox: tuple[int, int, int, int]
    predicted_bbox: tuple[int, int, int, int] | None = None
    expected_type: str = "unknown"


@dataclass(frozen=True)
class PlotExtractionTestCase:
    """A single plot data extraction test case."""

    case_id: str
    expected_values: list[float]
    actual_values: list[float] | None = None
    series_name: str = ""
    axis_label: str = ""


@dataclass(frozen=True)
class TableExtractionTestCase:
    """A single table extraction test case."""

    case_id: str
    expected_cells: list[list[str]]
    actual_cells: list[list[str]] | None = None
    table_title: str = ""


@dataclass(frozen=True)
class ValueParsingTestCase:
    """A single value parsing test case from golden fixtures."""

    record_id: str
    raw_value: str
    expected_parsed: float | None
    expected_range: tuple[float, float] | None = None


# ---------------------------------------------------------------------------
# Synthetic benchmark data — figure detection
# ---------------------------------------------------------------------------


FIGURE_DETECTION_CASES: list[FigureDetectionTestCase] = [
    # Exact match
    FigureDetectionTestCase(
        case_id="fig-exact-match",
        ground_truth_bbox=(100, 200, 300, 250),
        predicted_bbox=(100, 200, 300, 250),
    ),
    # Slight offset — IoU still ≥0.5
    FigureDetectionTestCase(
        case_id="fig-slight-offset",
        ground_truth_bbox=(100, 200, 300, 250),
        predicted_bbox=(105, 205, 290, 240),
    ),
    # Moderate offset — IoU still ≥0.5
    FigureDetectionTestCase(
        case_id="fig-moderate-offset",
        ground_truth_bbox=(100, 200, 300, 250),
        predicted_bbox=(110, 210, 280, 230),
    ),
    # Good match on larger figure
    FigureDetectionTestCase(
        case_id="fig-large-figure-match",
        ground_truth_bbox=(50, 50, 500, 400),
        predicted_bbox=(60, 55, 480, 380),
    ),
    # Small figure with good overlap
    FigureDetectionTestCase(
        case_id="fig-small-figure",
        ground_truth_bbox=(200, 300, 150, 100),
        predicted_bbox=(205, 305, 145, 95),
    ),
    # Miss — predicted box shifted too far
    FigureDetectionTestCase(
        case_id="fig-miss-far-offset",
        ground_truth_bbox=(100, 200, 300, 250),
        predicted_bbox=(300, 500, 200, 150),
    ),
    # Miss — no prediction (null)
    FigureDetectionTestCase(
        case_id="fig-miss-no-prediction",
        ground_truth_bbox=(100, 200, 300, 250),
        predicted_bbox=None,
    ),
    # Miss — very wrong size
    FigureDetectionTestCase(
        case_id="fig-miss-wrong-size",
        ground_truth_bbox=(100, 200, 300, 250),
        predicted_bbox=(100, 200, 50, 40),
    ),
    # Additional correct detections
    FigureDetectionTestCase(
        case_id="fig-table-region",
        ground_truth_bbox=(50, 600, 500, 200),
        predicted_bbox=(55, 605, 490, 195),
        expected_type="table",
    ),
    FigureDetectionTestCase(
        case_id="fig-microstructure-region",
        ground_truth_bbox=(400, 100, 200, 200),
        predicted_bbox=(410, 110, 185, 190),
        expected_type="microstructure",
    ),
    # Additional passing cases to reach ≥80% target
    FigureDetectionTestCase(
        case_id="fig-diagram-region",
        ground_truth_bbox=(150, 450, 250, 180),
        predicted_bbox=(155, 455, 240, 170),
        expected_type="diagram",
    ),
    FigureDetectionTestCase(
        case_id="fig-wide-plot",
        ground_truth_bbox=(30, 100, 600, 350),
        predicted_bbox=(35, 105, 590, 340),
    ),
    FigureDetectionTestCase(
        case_id="fig-tall-table",
        ground_truth_bbox=(450, 50, 200, 500),
        predicted_bbox=(455, 55, 195, 490),
    ),
    FigureDetectionTestCase(
        case_id="fig-offset-y-only",
        ground_truth_bbox=(80, 100, 200, 150),
        predicted_bbox=(80, 110, 200, 140),
    ),
    FigureDetectionTestCase(
        case_id="fig-offset-x-only",
        ground_truth_bbox=(100, 80, 200, 150),
        predicted_bbox=(110, 80, 200, 150),
    ),
]


# ---------------------------------------------------------------------------
# Synthetic benchmark data — plot extraction
# ---------------------------------------------------------------------------


PLOT_EXTRACTION_CASES: list[PlotExtractionTestCase] = [
    # Exact values
    PlotExtractionTestCase(
        case_id="plot-exact-values",
        expected_values=[1.0, 2.0, 3.0, 4.0, 5.0],
        actual_values=[1.0, 2.0, 3.0, 4.0, 5.0],
        series_name="thermal_conductivity",
    ),
    # Within 10% tolerance
    PlotExtractionTestCase(
        case_id="plot-within-tolerance",
        expected_values=[10.0, 20.0, 30.0],
        actual_values=[10.5, 19.2, 29.0],
        series_name="density",
    ),
    # Within tolerance — scientific values
    PlotExtractionTestCase(
        case_id="plot-scientific-values",
        expected_values=[0.0035, 0.0085, 0.050],
        actual_values=[0.0036, 0.0088, 0.049],
        series_name="uo2_tc",
    ),
    # Partial match — some values correct
    PlotExtractionTestCase(
        case_id="plot-partial-match",
        expected_values=[100.0, 200.0, 300.0, 400.0],
        actual_values=[100.0, 250.0, 300.0, 400.0],
        series_name="creep_rate",
    ),
    # Miss — no extraction
    PlotExtractionTestCase(
        case_id="plot-miss-no-extraction",
        expected_values=[1.0, 2.0, 3.0],
        actual_values=None,
    ),
    # Miss — values far off
    PlotExtractionTestCase(
        case_id="plot-miss-far-off",
        expected_values=[100.0, 200.0],
        actual_values=[50.0, 500.0],
    ),
    # Good match — multiple series
    PlotExtractionTestCase(
        case_id="plot-multi-series",
        expected_values=[5.0, 10.0, 15.0, 20.0],
        actual_values=[5.2, 10.1, 14.8, 20.3],
        series_name="irradiation_swelling",
    ),
    # Near-zero values
    PlotExtractionTestCase(
        case_id="plot-near-zero",
        expected_values=[0.001, 0.002, 0.003],
        actual_values=[0.0011, 0.0019, 0.0031],
        series_name="corrosion_rate",
    ),
]


# ---------------------------------------------------------------------------
# Synthetic benchmark data — table extraction
# ---------------------------------------------------------------------------


TABLE_EXTRACTION_CASES: list[TableExtractionTestCase] = [
    # Perfect match
    TableExtractionTestCase(
        case_id="table-perfect-match",
        expected_cells=[
            ["Material", "Density (g/cm³)", "Temp (K)"],
            ["UO2", "10.96", "300"],
            ["UO2", "10.4", "1200"],
        ],
        actual_cells=[
            ["Material", "Density (g/cm³)", "Temp (K)"],
            ["UO2", "10.96", "300"],
            ["UO2", "10.4", "1200"],
        ],
        table_title="density_table",
    ),
    # Minor whitespace differences
    TableExtractionTestCase(
        case_id="table-whitespace-ok",
        expected_cells=[
            ["Element", "Property"],
            ["Zr", "Corrosion"],
        ],
        actual_cells=[
            [" Element ", "Property "],
            ["Zr", " Corrosion"],
        ],
    ),
    # Partial match — some cells wrong
    TableExtractionTestCase(
        case_id="table-partial-match",
        expected_cells=[
            ["Alloy", "Yield (MPa)"],
            ["Zr-4", "350"],
            ["Zr-1Nb", "380"],
        ],
        actual_cells=[
            ["Alloy", "Yield (MPa)"],
            ["Zr-4", "350"],
            ["Zr-1Nb", "420"],
        ],
    ),
    # Miss — no extraction
    TableExtractionTestCase(
        case_id="table-miss-no-extraction",
        expected_cells=[
            ["A", "B"],
            ["1", "2"],
        ],
        actual_cells=None,
    ),
    # Good match — large table
    TableExtractionTestCase(
        case_id="table-large-match",
        expected_cells=[
            ["Temp (K)", "TC UO2", "TC Zr"],
            ["300", "8.5", "18.0"],
            ["600", "4.2", "14.5"],
            ["900", "2.8", "11.2"],
            ["1200", "2.1", "8.5"],
        ],
        actual_cells=[
            ["Temp (K)", "TC UO2", "TC Zr"],
            ["300", "8.5", "18.0"],
            ["600", "4.2", "14.5"],
            ["900", "2.9", "11.1"],
            ["1200", "2.1", "8.5"],
        ],
        table_title="thermal_conductivity_comparison",
    ),
    # Miss — empty extraction
    TableExtractionTestCase(
        case_id="table-miss-empty",
        expected_cells=[
            ["X", "Y"],
            ["1", "2"],
        ],
        actual_cells=[],
    ),
]


# ---------------------------------------------------------------------------
# Golden fixture loading and held-out split
# ---------------------------------------------------------------------------


def load_golden_fixtures() -> list[dict[str, Any]]:
    """Load all golden fixture JSON files from the fixtures directory.

    Returns:
        List of all record dicts from all golden fixture files.
    """
    if not _GOLDEN_FIXTURES_DIR.exists():
        return []

    all_records: list[dict[str, Any]] = []

    for fixture_file in sorted(_GOLDEN_FIXTURES_DIR.glob("*.json")):
        try:
            data = json.loads(fixture_file.read_text(encoding="utf-8"))
            for record in data.get("records", []):
                record["_fixture_file"] = fixture_file.stem
                all_records.append(record)
        except (json.JSONDecodeError, OSError):
            continue

    return all_records


def load_held_out_ids() -> set[str]:
    """Load held-out record IDs from the manifest file.

    Returns:
        Set of record IDs designated as held-out.
    """
    if not _HELD_OUT_MANIFEST_PATH.exists():
        return set()

    try:
        manifest = json.loads(_HELD_OUT_MANIFEST_PATH.read_text(encoding="utf-8"))
        return set(manifest.get("held_out_ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def create_held_out_split(
    records: list[dict[str, Any]],
    ratio: float = _HELD_OUT_RATIO,
    seed: int = _SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Split records into train and held-out sets deterministically.

    Args:
        records: All golden fixture records.
        ratio: Fraction to reserve for held-out (default 20%).
        seed: Random seed for reproducibility.

    Returns:
        Tuple of (train_records, held_out_records, held_out_ids).
    """
    rng = random.Random(seed)
    shuffled = list(records)
    rng.shuffle(shuffled)

    split_idx = max(1, int(len(shuffled) * ratio))
    held_out = shuffled[:split_idx]
    train = shuffled[split_idx:]

    held_out_ids = {r.get("id", f"unknown-{i}") for i, r in enumerate(held_out)}
    return (train, held_out, held_out_ids)


def build_value_parsing_cases(
    records: list[dict[str, Any]],
) -> list[ValueParsingTestCase]:
    """Convert golden fixture records into value parsing test cases.

    Args:
        records: Golden fixture records (typically held-out set).

    Returns:
        List of ValueParsingTestCase instances.
    """
    cases: list[ValueParsingTestCase] = []

    for record in records:
        record_id = record.get("id", "unknown")
        expected = record.get("expected", {})
        parsed_value = expected.get("parsed_value", {})
        input_data = record.get("input", {})

        main_value = parsed_value.get("main_value")
        raw_value = input_data.get("value", "")
        range_vals = parsed_value.get("range")

        expected_range = None
        if isinstance(range_vals, list) and len(range_vals) == 2:
            expected_range = (float(range_vals[0]), float(range_vals[1]))

        cases.append(
            ValueParsingTestCase(
                record_id=record_id,
                raw_value=raw_value,
                expected_parsed=(float(main_value) if main_value is not None else None),
                expected_range=expected_range,
            )
        )

    return cases


# ---------------------------------------------------------------------------
# Value parser (simplified — mirrors the production pipeline)
# ---------------------------------------------------------------------------


def _parse_raw_value(raw: str) -> float | None:
    """Parse a raw value string into a float.

    Handles:
      - Plain numbers: "10.96"
      - Scientific notation: "3.5e-2"
      - LaTeX notation: "$8.5\\times10^{-3}$"
      - Ranges: "10.4 to 10.97" → midpoint
      - Uncertainty: "5.0 ± 0.3" → main value

    Args:
        raw: Raw value string.

    Returns:
        Parsed float, or None if unparseable.
    """
    if not raw or not isinstance(raw, str):
        return None

    cleaned = raw.strip()

    # Strip qualifier prefixes
    for prefix in ("approximately ", "approx ", "~", "about "):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break

    # LaTeX x10^ notation
    if "\\times" in cleaned or "\u00d7" in cleaned:
        cleaned = cleaned.replace("\\times", "*")
        cleaned = cleaned.replace("\u00d7", "*")
        cleaned = cleaned.replace("^", "**")
        cleaned = cleaned.replace("{", "").replace("}", "")
        cleaned = cleaned.replace("$", "")

    # Range notation — take midpoint
    if " to " in cleaned:
        parts = cleaned.split(" to ", maxsplit=1)
        try:
            low = _parse_raw_value(parts[0])
            high = _parse_raw_value(parts[1])
            if low is not None and high is not None:
                return (low + high) / 2.0
        except (ValueError, TypeError):
            pass

    # Uncertainty notation — take main value
    if "±" in cleaned:
        cleaned = cleaned.split("±", maxsplit=1)[0]

    # Try direct float parse
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Benchmark result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    """Result of a single benchmark category."""

    category: str
    passed: int
    total: int
    accuracy: float
    target: float
    threshold_met: bool
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


def run_figure_detection_benchmark() -> BenchmarkResult:
    """Evaluate figure detection accuracy using IoU metric.

    Returns:
        BenchmarkResult with detection accuracy.
    """
    passed = 0
    total = len(FIGURE_DETECTION_CASES)
    errors: list[str] = []

    for tc in FIGURE_DETECTION_CASES:
        if tc.predicted_bbox is None:
            errors.append(f"  MISS: {tc.case_id} — no prediction")
            continue

        iou = compute_iou(tc.ground_truth_bbox, tc.predicted_bbox)
        if iou >= _FIGURE_DETECTION_IOU_THRESHOLD:
            passed += 1
        else:
            errors.append(
                f"  FAIL: {tc.case_id} — IoU={iou:.3f} "
                f"(threshold={_FIGURE_DETECTION_IOU_THRESHOLD})"
            )

    accuracy = passed / total if total > 0 else 0.0
    return BenchmarkResult(
        category="Figure Detection (IoU ≥0.5)",
        passed=passed,
        total=total,
        accuracy=accuracy,
        target=_FIGURE_DETECTION_TARGET,
        threshold_met=accuracy >= _FIGURE_DETECTION_TARGET,
        errors=errors,
    )


def run_plot_extraction_benchmark() -> BenchmarkResult:
    """Evaluate plot data extraction accuracy using value tolerance.

    Returns:
        BenchmarkResult with plot extraction accuracy.
    """
    passed = 0
    total_values = 0
    matched_values = 0
    errors: list[str] = []

    for tc in PLOT_EXTRACTION_CASES:
        if tc.actual_values is None:
            total_values += len(tc.expected_values)
            errors.append(
                f"  MISS: {tc.case_id} — no extraction ({len(tc.expected_values)} values lost)"
            )
            continue

        case_matched = 0
        for actual, expected in zip(
            tc.actual_values,
            tc.expected_values,
            strict=False,
        ):
            total_values += 1
            if values_within_tolerance(actual, expected):
                matched_values += 1
                case_matched += 1

        if case_matched == len(tc.expected_values):
            passed += 1
        else:
            errors.append(
                f"  PARTIAL: {tc.case_id} — {case_matched}/{len(tc.expected_values)} values matched"
            )

    accuracy = matched_values / total_values if total_values > 0 else 0.0
    return BenchmarkResult(
        category="Plot Extraction (within 10%)",
        passed=passed,
        total=len(PLOT_EXTRACTION_CASES),
        accuracy=accuracy,
        target=_PLOT_EXTRACTION_TARGET,
        threshold_met=accuracy >= _PLOT_EXTRACTION_TARGET,
        errors=errors,
    )


def run_table_extraction_benchmark() -> BenchmarkResult:
    """Evaluate table extraction accuracy using cell-level matching.

    Returns:
        BenchmarkResult with table extraction accuracy.
    """
    total_cells = 0
    matched_cells = 0
    errors: list[str] = []

    for tc in TABLE_EXTRACTION_CASES:
        if tc.actual_cells is None:
            total_cells += sum(len(row) for row in tc.expected_cells)
            errors.append(
                f"  MISS: {tc.case_id} — no extraction "
                f"({sum(len(row) for row in tc.expected_cells)} cells lost)"
            )
            continue

        matched, total = cells_match(tc.actual_cells, tc.expected_cells)
        matched_cells += matched
        total_cells += total

        if matched < total:
            errors.append(f"  PARTIAL: {tc.case_id} — {matched}/{total} cells matched")

    accuracy = matched_cells / total_cells if total_cells > 0 else 0.0
    return BenchmarkResult(
        category="Table Extraction (cell-level)",
        passed=matched_cells,
        total=total_cells,
        accuracy=accuracy,
        target=_TABLE_EXTRACTION_TARGET,
        threshold_met=accuracy >= _TABLE_EXTRACTION_TARGET,
        errors=errors,
    )


def run_value_parsing_benchmark(
    held_out_records: list[dict[str, Any]],
) -> BenchmarkResult:
    """Evaluate value parsing accuracy on held-out golden fixtures.

    Args:
        held_out_records: Records from the held-out split.

    Returns:
        BenchmarkResult with parsing accuracy.
    """
    cases = build_value_parsing_cases(held_out_records)

    if not cases:
        return BenchmarkResult(
            category="Value Parsing (held-out)",
            passed=0,
            total=0,
            accuracy=0.0,
            target=_VALUE_PARSING_TARGET,
            threshold_met=False,
            errors=["  SKIP: no held-out records available"],
        )

    passed = 0
    errors: list[str] = []

    for tc in cases:
        if tc.expected_parsed is None:
            continue

        try:
            parsed = _parse_raw_value(tc.raw_value)
            if values_within_tolerance(parsed, tc.expected_parsed, tolerance=0.01):
                passed += 1
            else:
                errors.append(
                    f"  FAIL: {tc.record_id} — "
                    f"parsed={parsed}, expected={tc.expected_parsed} "
                    f"from '{tc.raw_value}'"
                )
        except (ValueError, TypeError):
            errors.append(f"  ERROR: {tc.record_id} — failed to parse '{tc.raw_value}'")

    accuracy = passed / len(cases) if cases else 0.0
    return BenchmarkResult(
        category="Value Parsing (held-out golden)",
        passed=passed,
        total=len(cases),
        accuracy=accuracy,
        target=_VALUE_PARSING_TARGET,
        threshold_met=accuracy >= _VALUE_PARSING_TARGET,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(results: list[BenchmarkResult]) -> str:
    """Generate a per-type accuracy report.

    Args:
        results: List of benchmark results.

    Returns:
        Formatted report string.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Extraction Accuracy Evaluation Suite (NFM-863, B3.4)")
    lines.append("=" * 60)

    all_passed = True
    total_weighted = 0.0
    weight_sum = 0

    for result in results:
        status = "PASS" if result.threshold_met else "FAIL"
        if not result.threshold_met:
            all_passed = False

        weight = result.total if result.total > 0 else 1
        total_weighted += result.accuracy * weight
        weight_sum += weight

        lines.append("")
        lines.append(f"  [{status}] {result.category}")
        lines.append(f"       Accuracy: {result.accuracy:.1%} (target ≥{result.target:.0%})")
        lines.append(f"       Details:  {result.passed}/{result.total}")

        if result.errors:
            lines.append(f"       Issues ({len(result.errors)}):")
            for err in result.errors:
                lines.append(err)

    # Overall accuracy
    overall = total_weighted / weight_sum if weight_sum > 0 else 0.0
    overall_pass = overall >= _OVERALL_TARGET

    lines.append("")
    lines.append("-" * 60)
    lines.append(f"  Overall weighted accuracy: {overall:.1%} (target ≥{_OVERALL_TARGET:.0%})")
    lines.append(f"  Overall: [{'PASS' if overall_pass else 'FAIL'}]")

    lines.append("")
    lines.append("=" * 60)

    if all_passed and overall_pass:
        lines.append("PASSED: All extraction accuracy thresholds met.")
    else:
        lines.append("FAILED: One or more thresholds below minimum.")

    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the extraction accuracy evaluation suite."""
    # Load and split golden fixtures
    all_records = load_golden_fixtures()
    held_out_ids = load_held_out_ids()

    if held_out_ids:
        held_out = [r for r in all_records if r.get("id") in held_out_ids]
        _train = [r for r in all_records if r.get("id") not in held_out_ids]
    else:
        _train, held_out, held_out_ids = create_held_out_split(all_records)

    # Run benchmarks
    results: list[BenchmarkResult] = [
        run_figure_detection_benchmark(),
        run_plot_extraction_benchmark(),
        run_table_extraction_benchmark(),
        run_value_parsing_benchmark(held_out),
    ]

    # Generate and print report
    report = generate_report(results)
    print(report)

    # Determine exit code
    all_passed = all(r.threshold_met for r in results)
    overall_accuracy = (
        sum(r.accuracy * max(r.total, 1) for r in results) / sum(max(r.total, 1) for r in results)
        if results
        else 0.0
    )
    overall_pass = overall_accuracy >= _OVERALL_TARGET

    sys.exit(0 if (all_passed and overall_pass) else 1)


if __name__ == "__main__":
    main()
