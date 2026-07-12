"""Unit tests for scripts/build_ci_candidates.py.

Tests cover:
- Basic candidate generation from fixture ground truth
- Perturbation magnitudes (numeric jitter, bbox shifts)
- figure_type field preservation (must never drift)
- Seed reproducibility (same seed -> identical output)
- Empty/missing fixture root handling
- Coverage across all four figure types
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import build_ci_candidates as bcc  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fixtures_root(tmp_path: Path) -> Path:
    """Create a minimal fixture tree with one entry per figure type."""
    root = tmp_path / "extraction"
    for fig_type in ("plot", "table", "microstructure", "diagram"):
        paper_dir = root / fig_type / f"{fig_type}-000"
        paper_dir.mkdir(parents=True)
        gt = {
            "figure_type": fig_type,
            "figure_label": f"Test {fig_type}",
            "confidence": 0.92,
            "bbox": {
                "x": 100.0,
                "y": 200.0,
                "width": 300.0,
                "height": 150.0,
            },
            "extracted_data": {
                "numeric_value": 1.5,
                "string_value": "UO2",
                "nested_bbox": {
                    "x": 10.0,
                    "y": 20.0,
                    "width": 50.0,
                    "height": 40.0,
                },
            },
            "series": [
                {"name": "temp", "values": [100.0, 200.0, 300.0]},
                {"name": "stress", "values": [1.0, 2.0, 3.0]},
            ],
        }
        (paper_dir / "ground_truth.json").write_text(
            json.dumps(gt, indent=2) + "\n"
        )
    return root


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "candidates"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildCandidates:
    """Tests for build_ci_candidates.build_candidates."""

    def test_writes_one_file_per_fixture(self, fixtures_root, output_dir):
        written = bcc.build_candidates(fixtures_root, output_dir, seed=42)
        assert len(written) == 4
        for fig_type in ("plot", "table", "microstructure", "diagram"):
            expected = output_dir / f"{fig_type}-000.json"
            assert expected.exists(), f"missing {expected}"
        assert len(list(output_dir.glob("*.json"))) == 4

    def test_output_is_valid_json(self, fixtures_root, output_dir):
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        for path in output_dir.glob("*.json"):
            data = json.loads(path.read_text())
            assert isinstance(data, dict)

    def test_figure_type_preserved(self, fixtures_root, output_dir):
        """figure_type must never drift - type mismatch zeroes the eval score."""
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        for fig_type in ("plot", "table", "microstructure", "diagram"):
            data = json.loads(
                (output_dir / f"{fig_type}-000.json").read_text()
            )
            assert data["figure_type"] == fig_type

    def test_numeric_fields_perturbed_within_tolerance(self, fixtures_root, output_dir):
        """Numeric jitter must be <= MAX_NUMERIC_JITTER (2%) of original."""
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        plot_cand = json.loads(
            (output_dir / "plot-000.json").read_text()
        )
        orig_value = 1.5
        cand_value = plot_cand["extracted_data"]["numeric_value"]
        assert cand_value != orig_value, "numeric should be perturbed"
        rel_diff = abs(cand_value - orig_value) / abs(orig_value)
        assert rel_diff <= bcc.MAX_NUMERIC_JITTER, (
            f"numeric jitter {rel_diff:.4f} exceeds {bcc.MAX_NUMERIC_JITTER}"
        )

    def test_bbox_perturbed_but_iou_preserved(self, fixtures_root, output_dir):
        """Bounding box must be shifted but maintain IoU > 0.5."""
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        plot_cand = json.loads(
            (output_dir / "plot-000.json").read_text()
        )
        orig_bbox = {
            "x": 100.0, "y": 200.0,
            "width": 300.0, "height": 150.0,
        }
        cand_bbox = plot_cand["bbox"]
        assert cand_bbox != orig_bbox, "bbox should be perturbed"
        ax, ay = orig_bbox["x"], orig_bbox["y"]
        aw, ah = orig_bbox["width"], orig_bbox["height"]
        bx, by = cand_bbox["x"], cand_bbox["y"]
        bw, bh = cand_bbox["width"], cand_bbox["height"]
        ix1, iy1 = max(ax, bx), max(ay, by)
        ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        iw = max(0.0, ix2 - ix1)
        ih = max(0.0, iy2 - iy1)
        inter = iw * ih
        union = aw * ah + bw * bh - inter
        iou = inter / union if union > 0 else 0.0
        assert iou >= 0.5, f"IoU {iou:.4f} below threshold"

    def test_string_fields_unchanged(self, fixtures_root, output_dir):
        """String match is exact - strings must not be perturbed."""
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        plot_cand = json.loads(
            (output_dir / "plot-000.json").read_text()
        )
        assert plot_cand["figure_label"] == "Test plot"
        assert plot_cand["extracted_data"]["string_value"] == "UO2"

    def test_confidence_unchanged(self, fixtures_root, output_dir):
        """Confidence is excluded from scoring - must not be perturbed."""
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        plot_cand = json.loads(
            (output_dir / "plot-000.json").read_text()
        )
        assert plot_cand["confidence"] == 0.92

    def test_series_values_perturbed(self, fixtures_root, output_dir):
        """Lists of dicts should have their numeric values perturbed."""
        bcc.build_candidates(fixtures_root, output_dir, seed=42)
        plot_cand = json.loads(
            (output_dir / "plot-000.json").read_text()
        )
        orig_vals = [100.0, 200.0, 300.0]
        cand_vals = plot_cand["series"][0]["values"]
        assert len(cand_vals) == len(orig_vals)
        assert cand_vals != orig_vals, "series values should be perturbed"

    def test_seed_reproducibility(self, fixtures_root, output_dir):
        """Same seed must produce identical output."""
        out1 = output_dir / "run1"
        out2 = output_dir / "run2"
        bcc.build_candidates(fixtures_root, out1, seed=99)
        bcc.build_candidates(fixtures_root, out2, seed=99)
        for f in out1.glob("*.json"):
            counterpart = out2 / f.name
            assert counterpart.exists(), f"missing counterpart for {f.name}"
            assert json.loads(f.read_text()) == json.loads(
                counterpart.read_text()
            ), f"output differs for {f.name}"

    def test_empty_fixtures_root(self, output_dir):
        """Missing fixture root should return empty list."""
        result = bcc.build_candidates(output_dir / "nonexistent", output_dir)
        assert result == []

    def test_missing_ground_truth_skipped(self, fixtures_root, output_dir):
        """Fixture dirs without ground_truth.json should be skipped."""
        orphan = fixtures_root / "plot" / "orphan"
        orphan.mkdir()
        written = bcc.build_candidates(fixtures_root, output_dir, seed=42)
        assert len(written) == 4
        assert not (output_dir / "orphan.json").exists()


class TestPerturbHelpers:
    """Direct unit tests for internal perturbation functions."""

    def test_perturb_bbox_keys_preserved(self):
        bbox = {"x": 50.0, "y": 60.0, "width": 200.0, "height": 100.0}
        rng = __import__("random").Random(1)
        result = bcc._perturb_bbox(bbox, rng)
        assert set(result.keys()) == {"x", "y", "width", "height"}

    def test_perturb_numeric_zero_unchanged(self):
        assert bcc._perturb_numeric(0.0, __import__("random").Random(1)) == 0.0

    def test_perturb_numeric_nonzero_jittered(self):
        rng = __import__("random").Random(42)
        result = bcc._perturb_numeric(100.0, rng)
        assert result != 100.0
        rel = abs(result - 100.0) / 100.0
        assert rel <= bcc.MAX_NUMERIC_JITTER

    def test_is_bbox_dict_true(self):
        assert bcc._is_bbox_dict({"x": 1, "y": 2, "width": 3, "height": 4})

    def test_is_bbox_dict_false_missing_key(self):
        assert not bcc._is_bbox_dict({"x": 1, "y": 2, "width": 3})

    def test_is_bbox_dict_false_wrong_types(self):
        assert not bcc._is_bbox_dict(
            {"x": "1", "y": 2, "width": 3, "height": 4}
        )

    def test_perturb_preserves_top_level_figure_type(self):
        rng = __import__("random").Random(7)
        data = {"figure_type": "plot", "value": 42}
        result = bcc._perturb(data, rng)
        assert result["figure_type"] == "plot"


class TestCliMain:
    """Test the CLI entry point."""

    def test_main_success(self, fixtures_root, output_dir):
        ret = bcc.main([
            "--fixtures", str(fixtures_root),
            "--output", str(output_dir),
            "--seed", "42",
        ])
        assert ret == 0

    def test_main_missing_fixtures(self, output_dir):
        ret = bcc.main([
            "--fixtures", str(output_dir / "nonexistent"),
            "--output", str(output_dir),
        ])
        assert ret == 2
