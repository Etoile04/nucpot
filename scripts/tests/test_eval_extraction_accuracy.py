"""Unit tests for scripts/eval_extraction_accuracy.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import eval_extraction_accuracy as ev  # noqa: E402


def _fixture_root(tmp_path: Path) -> Path:
    """Build a tiny but representative fixture corpus for tests."""
    root = tmp_path / "fx"
    for fig_type, paper in [
        ("plot", "plot-001"),
        ("plot", "plot-002"),
        ("table", "table-001"),
        ("microstructure", "micro-001"),
        ("diagram", "diagram-001"),
    ]:
        d = root / fig_type / paper
        d.mkdir(parents=True)
        gt = {
            "figure_type": fig_type,
            "title": f"{fig_type} title",
            "bounding_box": {"x": 50, "y": 50, "width": 400, "height": 300},
            "plot_data" if fig_type != "table" else "table_data": {
                "title": "x",
                "plot_type": "line",
                "x_axis": {
                    "label": "T",
                    "unit": "K",
                    "values": [1.0, 2.0, 3.0],
                    "scale": "linear",
                },
                "y_axis": {
                    "label": "S",
                    "unit": "MPa",
                    "values": [10.0, 20.0, 30.0],
                    "scale": "linear",
                },
                "series": [],
                "legend_entries": [],
                "annotations": [],
                "confidence": 0.9,
            },
            "paper_id": paper,
        }
        (d / "ground_truth.json").write_text(json.dumps(gt))
    return root


def test_discover_fixtures_finds_all_types(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    found = ev.discover_fixtures(root)
    assert len(found) == 5
    types = {f.figure_type for f in found}
    assert types == {"plot", "table", "microstructure", "diagram"}


def test_discover_fixtures_warns_on_missing_ground_truth(tmp_path: Path, capsys) -> None:
    root = tmp_path / "fx"
    (root / "plot" / "plot-001").mkdir(parents=True)
    ev.discover_fixtures(root)
    err = capsys.readouterr().err
    assert "ground_truth.json" in err


def test_iou_full_overlap() -> None:
    box = {"x": 0, "y": 0, "width": 10, "height": 10}
    assert ev._iou(box, box) == 1.0


def test_iou_no_overlap() -> None:
    a = {"x": 0, "y": 0, "width": 10, "height": 10}
    b = {"x": 100, "y": 100, "width": 10, "height": 10}
    assert ev._iou(a, b) == 0.0


def test_iou_handles_missing_keys() -> None:
    assert ev._iou({}, {"x": 0, "y": 0, "width": 1, "height": 1}) == 0.0


def test_score_fixture_perfect_match(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    [fix] = [
        f for f in ev.discover_fixtures(root)
        if f.figure_type == "plot" and f.paper_id == "plot-001"
    ]
    candidate = json.loads(
        (root / "plot" / "plot-001" / "ground_truth.json").read_text()
    )
    score = ev.score_fixture(fix, candidate, numeric_tol=0.05, iou_thr=0.5)
    assert score >= 0.999


def test_score_fixture_wrong_type_zero(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    plots = [f for f in ev.discover_fixtures(root) if f.figure_type == "plot"]
    fix = plots[0]
    candidate = {"figure_type": "table", "title": "x"}
    assert ev.score_fixture(fix, candidate, numeric_tol=0.05, iou_thr=0.5) == 0.0


def test_score_fixture_none_candidate(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    fix = ev.discover_fixtures(root)[0]
    assert ev.score_fixture(fix, None, numeric_tol=0.05, iou_thr=0.5) == 0.0


def test_score_fixture_numeric_within_tolerance(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    fix = ev.discover_fixtures(root)[0]
    candidate = json.loads(
        (fix.fixture_dir / "ground_truth.json").read_text()
    )
    candidate["bounding_box"]["width"] *= 1.01
    score = ev.score_fixture(fix, candidate, numeric_tol=0.05, iou_thr=0.5)
    assert score >= 0.999


def test_score_fixture_numeric_outside_tolerance(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    fix = ev.discover_fixtures(root)[0]
    candidate = json.loads(
        (fix.fixture_dir / "ground_truth.json").read_text()
    )
    # Push the bounding box far enough that IoU drops well below 0.5.
    candidate["bounding_box"]["x"] += 1000
    candidate["bounding_box"]["y"] += 1000
    score = ev.score_fixture(fix, candidate, numeric_tol=0.05, iou_thr=0.5)
    assert score < 1.0


def test_main_passes_with_identical_candidates(tmp_path: Path) -> None:
    fixtures = _fixture_root(tmp_path)
    candidates = tmp_path / "cands"
    candidates.mkdir()
    for gt_path in fixtures.rglob("ground_truth.json"):
        paper_id = gt_path.parent.name
        (candidates / f"{paper_id}.json").write_text(gt_path.read_text())
    rc = ev.main([
        "--fixtures", str(fixtures),
        "--candidates", str(candidates),
        "--threshold", "0.6",
    ])
    assert rc == 0


def test_main_fails_when_below_threshold(tmp_path: Path) -> None:
    fixtures = _fixture_root(tmp_path)
    candidates = tmp_path / "cands"
    candidates.mkdir()
    (candidates / "plot-001.json").write_text(
        (fixtures / "plot" / "plot-001" / "ground_truth.json").read_text()
    )
    rc = ev.main([
        "--fixtures", str(fixtures),
        "--candidates", str(candidates),
        "--threshold", "0.9",
    ])
    assert rc == 1


def test_main_returns_2_when_no_fixtures(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    candidates = tmp_path / "cands"
    candidates.mkdir()
    rc = ev.main([
        "--fixtures", str(empty),
        "--candidates", str(candidates),
    ])
    assert rc == 2


def test_main_strict_coverage_fails_on_under_target(tmp_path: Path) -> None:
    # Build a single-plot fixture set; strict coverage should fail because
    # the EXPECTED_COUNTS target for plots is >= 20.
    fixtures = tmp_path / "fx"
    d = fixtures / "plot" / "plot-001"
    d.mkdir(parents=True)
    gt = {
        "figure_type": "plot",
        "title": "t",
        "bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10},
        "plot_data": {
            "title": "x",
            "plot_type": "line",
            "x_axis": {"label": "T", "unit": "K", "values": [1.0], "scale": "linear"},
            "y_axis": {"label": "S", "unit": "MPa", "values": [1.0], "scale": "linear"},
            "series": [],
            "legend_entries": [],
            "annotations": [],
            "confidence": 1.0,
        },
        "paper_id": "plot-001",
    }
    (d / "ground_truth.json").write_text(json.dumps(gt))
    candidates = tmp_path / "cands"
    candidates.mkdir()
    (candidates / "plot-001.json").write_text(json.dumps(gt))
    rc = ev.main([
        "--fixtures", str(fixtures),
        "--candidates", str(candidates),
        "--strict-coverage",
    ])
    assert rc == 1


def test_main_writes_report_json(tmp_path: Path) -> None:
    fixtures = _fixture_root(tmp_path)
    candidates = tmp_path / "cands"
    candidates.mkdir()
    for gt_path in fixtures.rglob("ground_truth.json"):
        paper_id = gt_path.parent.name
        (candidates / f"{paper_id}.json").write_text(gt_path.read_text())
    report = tmp_path / "report.json"
    rc = ev.main([
        "--fixtures", str(fixtures),
        "--candidates", str(candidates),
        "--report-json", str(report),
    ])
    assert rc == 0
    payload = json.loads(report.read_text())
    assert payload["total_fixtures"] == 5
    assert payload["overall_accuracy"] >= 0.999