"""Unit tests for scripts/generate_fixtures.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import generate_fixtures as gf  # noqa: E402


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_synthetic_png_has_valid_signature_and_ihdr() -> None:
    blob = gf._synthetic_png(16, 16, seed=42, fig_type="plot")
    assert blob.startswith(PNG_MAGIC)
    width = int.from_bytes(blob[16:20], "big")
    height = int.from_bytes(blob[20:24], "big")
    assert width == 16
    assert height == 16


def test_synthetic_png_is_deterministic() -> None:
    a = gf._synthetic_png(32, 24, seed=7, fig_type="table")
    b = gf._synthetic_png(32, 24, seed=7, fig_type="table")
    assert a == b


def test_synthetic_png_differs_per_figure_type() -> None:
    plot = gf._synthetic_png(8, 8, seed=1, fig_type="plot")
    micro = gf._synthetic_png(8, 8, seed=1, fig_type="microstructure")
    assert plot != micro


def test_generate_creates_expected_counts(tmp_path: Path) -> None:
    counts = {"plot": 3, "table": 2, "microstructure": 1, "diagram": 1}
    fixtures = gf.generate(tmp_path, counts, seed=123, write_images=False)
    assert len(fixtures) == 7
    by_type: dict[str, int] = {}
    for f in fixtures:
        by_type[f.figure_type] = by_type.get(f.figure_type, 0) + 1
    assert by_type == counts
    for f in fixtures:
        assert not (f.fixture_dir / "image.png").exists()
        assert (f.fixture_dir / "ground_truth.json").exists()


def test_generate_writes_png_images(tmp_path: Path) -> None:
    fixtures = gf.generate(
        tmp_path, {"plot": 2}, seed=99, write_images=True
    )
    for f in fixtures:
        img = f.fixture_dir / "image.png"
        assert img.exists()
        assert img.read_bytes().startswith(PNG_MAGIC)


def test_generate_deterministic_seed(tmp_path: Path) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    fa = gf.generate(out_a, {"plot": 2}, seed=20260101, write_images=True)
    fb = gf.generate(out_b, {"plot": 2}, seed=20260101, write_images=True)
    for x, y in zip(fa, fb):
        a_bytes = (x.fixture_dir / "image.png").read_bytes()
        b_bytes = (y.fixture_dir / "image.png").read_bytes()
        assert a_bytes == b_bytes
        a_gt = json.loads((x.fixture_dir / "ground_truth.json").read_text())
        b_gt = json.loads((y.fixture_dir / "ground_truth.json").read_text())
        assert a_gt == b_gt


def test_ground_truth_has_required_keys(tmp_path: Path) -> None:
    fixtures = gf.generate(
        tmp_path,
        {"plot": 1, "table": 1, "microstructure": 1, "diagram": 1},
        seed=11,
        write_images=False,
    )
    for f in fixtures:
        gt = json.loads((f.fixture_dir / "ground_truth.json").read_text())
        assert gt["figure_type"] == f.figure_type
        assert gt["paper_id"] == f.paper_id
        assert {"x", "y", "width", "height"} <= set(gt["bounding_box"])
        if f.figure_type == "table":
            assert "table_data" in gt
        else:
            assert "plot_data" in gt


def test_main_rejects_unknown_count_key(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gf.main([
            "--output", str(tmp_path),
            "--counts", "bogus=5",
        ])


def test_main_rejects_non_integer_count(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gf.main([
            "--output", str(tmp_path),
            "--counts", "plot=abc",
        ])


def test_main_writes_summary(tmp_path: Path, capsys) -> None:
    rc = gf.main([
        "--output", str(tmp_path),
        "--seed", "5",
        "--counts", "plot=1",
        "--counts", "table=1",
        "--counts", "microstructure=0",
        "--counts", "diagram=0",
        "--skip-images",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    summary = json.loads(out)
    assert summary["seed"] == 5
    assert summary["total"] == 2
    assert summary["by_type"]["plot"] == 1
    assert summary["by_type"]["table"] == 1
    plot_dir = next((tmp_path / "plot").iterdir())
    assert not (plot_dir / "image.png").exists()


def test_paper_id_format() -> None:
    assert gf._paper_id("plot", 0) == "plot-000"
    assert gf._paper_id("table", 14) == "table-014"
    assert gf._paper_id("diagram", 99) == "diagram-099"